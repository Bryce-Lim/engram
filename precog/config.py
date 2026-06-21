"""Optional configuration for the Precog CLI.

A rules file lets an operator teach the chain-of-thought oracle explicit
intent→tool mappings with argument capture — the same mechanism the demo wires
up in code, but data-driven so ``precog wrap`` can use it with no code. The file
is JSON::

    {
      "intent_rules": [
        {
          "pattern": "orders for (?P<customer>\\\\w+)",
          "tool": "get_orders",
          "args": {"customer": "customer"},
          "static_args": {"limit": 20},
          "confidence": 0.95,
          "flags": "i"
        }
      ]
    }

* ``pattern``    — a Python regex; named groups feed ``args``.
* ``tool``       — the tool to call on a match.
* ``args``       — map of regex-group-name → tool-argument-key (capture).
* ``static_args``— arguments merged in unconditionally.
* ``confidence`` — 0..1 score (telemetry/ordering only; the safety gate decides).
* ``flags``      — subset of "imsx" applied to the regex.

Anything malformed raises ``ConfigError`` with a precise message rather than
silently misbehaving.
"""

import json
import re
from typing import List

from precog.predictors.cot_oracle import IntentRule

_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}


class ConfigError(Exception):
    pass


def _compile_flags(flags: str) -> int:
    value = 0
    for ch in flags or "":
        if ch not in _FLAG_MAP:
            raise ConfigError("unknown regex flag %r (allowed: imsx)" % ch)
        value |= _FLAG_MAP[ch]
    return value


def load_intent_rules(path: str) -> List[IntentRule]:
    """Parse a rules JSON file into :class:`IntentRule` objects."""
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read rules file %s: %s" % (path, exc))

    if not isinstance(data, dict):
        raise ConfigError("rules file must be a JSON object, got %s"
                          % type(data).__name__)
    raw_rules = data.get("intent_rules")
    if not isinstance(raw_rules, list):
        raise ConfigError("rules file must contain an 'intent_rules' array")

    rules = []  # type: List[IntentRule]
    for i, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            raise ConfigError("intent_rules[%d] must be an object" % i)
        pattern = entry.get("pattern")
        tool = entry.get("tool")
        if not pattern or not tool:
            raise ConfigError("intent_rules[%d] needs both 'pattern' and 'tool'" % i)
        try:
            compiled = re.compile(pattern, _compile_flags(entry.get("flags", "")))
        except re.error as exc:
            raise ConfigError("intent_rules[%d] bad regex: %s" % (i, exc))
        arg_map = entry.get("args") or {}
        static_args = entry.get("static_args") or {}
        if not isinstance(arg_map, dict) or not isinstance(static_args, dict):
            raise ConfigError("intent_rules[%d] 'args'/'static_args' must be objects" % i)
        confidence = entry.get("confidence", 0.9)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            raise ConfigError("intent_rules[%d] 'confidence' must be a number" % i)
        rules.append(IntentRule(compiled, tool, arg_map=arg_map,
                                static_args=static_args, confidence=confidence))
    return rules
