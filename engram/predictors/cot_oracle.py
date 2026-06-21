"""Signal 2 — chain-of-thought oracle (the novel part).

Extended-thinking models narrate their intent in the reasoning stream seconds
before they emit the corresponding tool call: *"Let me look up their recent
orders"* precedes a ``get_orders`` call. That gap is free prefetch budget. The
oracle reads the reasoning as it streams, matches stated intent against the
tool catalog, extracts arguments where it can, and proposes the call so the
speculator can fire it during the think.

Matching has two layers:

* **Auto-derived keywords** — tokens from each tool's name and description
  become weak triggers, so the oracle works against any server with zero
  configuration. ``get_orders`` matches reasoning that mentions "orders".
* **Explicit intent rules** — optional regex patterns with named groups that
  map a phrase to a specific tool and capture arguments from the text. These
  raise confidence and let the oracle fill arguments it could not otherwise
  guess.

Nothing the oracle proposes is trusted on its face: every proposal still passes
the read-only safety gate, and a wrong guess is simply squashed at no
correctness cost.
"""

import re
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

from engram.predictors.base import Prediction, Predictor
from engram.safety import ToolRegistry

# Tokens too generic to be useful triggers on their own.
_STOPWORDS = frozenset([
    "the", "a", "an", "and", "or", "of", "to", "for", "with", "by", "in", "on",
    "get", "list", "fetch", "read", "set", "tool", "data", "value", "item",
    "from", "this", "that", "their", "your", "my", "it", "is", "are", "be",
])

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class IntentRule:
    """An explicit phrase→tool mapping with optional argument capture.

    ``pattern`` is a compiled regex searched against reasoning text. Named
    groups in the pattern populate ``arguments`` via ``arg_map`` (group name →
    argument key). ``static_args`` are merged in unconditionally.
    """

    def __init__(self, pattern: Pattern, tool_name: str,
                 arg_map: Optional[Dict[str, str]] = None,
                 static_args: Optional[Dict[str, Any]] = None,
                 confidence: float = 0.9):
        self.pattern = pattern
        self.tool_name = tool_name
        self.arg_map = arg_map or {}
        self.static_args = static_args or {}
        self.confidence = confidence

    def matches(self, text: str) -> List[Dict[str, Any]]:
        """Return argument dicts for *every* non-overlapping match in ``text``.

        A planning burst may name the same intent more than once with different
        arguments ("orders for alice ... orders for bob"); each occurrence
        becomes its own prediction. Duplicate argument sets are collapsed.
        """
        results = []  # type: List[Dict[str, Any]]
        seen = set()  # type: set
        for m in self.pattern.finditer(text):
            args = dict(self.static_args)
            groups = m.groupdict()
            for group_name, arg_key in self.arg_map.items():
                val = groups.get(group_name)
                if val is not None:
                    args[arg_key] = val.strip()
            key = frozenset(args.items())
            if key not in seen:
                seen.add(key)
                results.append(args)
        return results


class CoTOracle(Predictor):
    name = "cot_oracle"

    def __init__(self, registry: ToolRegistry,
                 rules: Optional[List[IntentRule]] = None,
                 min_keyword_len: int = 4):
        self.registry = registry
        self.rules = list(rules or [])
        self.min_keyword_len = min_keyword_len
        # Cache of tool_name -> set(trigger tokens), rebuilt when the tool set
        # changes so a server whose tools/list arrives late is still covered.
        self._keyword_cache = {}        # type: Dict[str, set]
        self._cached_names = ()         # type: Tuple[str, ...]

    def add_rule(self, rule: IntentRule) -> None:
        self.rules.append(rule)

    def _refresh_keywords(self) -> Dict[str, set]:
        names = tuple(sorted(self.registry.names()))
        if names == self._cached_names and self._keyword_cache:
            return self._keyword_cache
        cache = {}
        for name in names:
            tool = self.registry.get(name) or {}
            triggers = set()
            for tok in _tokens(name):
                if len(tok) >= self.min_keyword_len and tok not in _STOPWORDS:
                    triggers.add(tok)
            # Description tokens are weaker but broaden coverage.
            for tok in _tokens(tool.get("description", "")):
                if len(tok) >= self.min_keyword_len and tok not in _STOPWORDS:
                    triggers.add(tok)
            cache[name] = triggers
        self._keyword_cache = cache
        self._cached_names = names
        return cache

    def on_reasoning(self, text: str) -> List[Prediction]:
        if not text:
            return []
        predictions = []  # type: List[Prediction]
        seen = set()         # type: set  # (tool_name, frozenset args) dedupe within one pass
        ruled_tools = set()  # type: set  # tools an explicit rule already proposed

        # 1) Explicit intent rules — high confidence, can carry arguments. A
        #    single rule may fire several times if the reasoning names several
        #    instances ("orders for alice ... orders for bob").
        for rule in self.rules:
            if self.registry.get(rule.tool_name) is None:
                continue
            for args in rule.matches(text):
                key = (rule.tool_name, frozenset(args.items()))
                if key not in seen:
                    seen.add(key)
                    ruled_tools.add(rule.tool_name)
                    predictions.append(Prediction(
                        rule.tool_name, args, rule.confidence, self.name))

        # 2) Auto-derived keyword triggers — lower confidence, arguments unknown.
        lowered = text.lower()
        present = set(_tokens(lowered))
        keywords = self._refresh_keywords()
        for tool_name, triggers in keywords.items():
            if not triggers:
                continue
            # If an explicit rule already proposed this tool *with* arguments,
            # an empty-argument keyword guess for it is pure waste: it can never
            # match the real (argument-bearing) call and only burns a downstream
            # request. Suppress it.
            if tool_name in ruled_tools:
                continue
            overlap = triggers & present
            if not overlap:
                continue
            # Confidence scales with how distinctively the reasoning names this
            # tool, capped below the explicit-rule tier.
            conf = min(0.75, 0.35 + 0.2 * len(overlap))
            key = (tool_name, frozenset())
            if key not in seen:
                seen.add(key)
                predictions.append(Prediction(tool_name, {}, conf, self.name))

        return predictions
