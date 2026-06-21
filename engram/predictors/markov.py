"""Signal 3 — Markov sequence model.

Tool usage has structure: after ``search`` the agent usually ``fetch``es; after
``list_files`` it usually ``read_file``s. The Markov model learns a first-order
transition table from observed traffic — counts of ``prev_tool -> next_tool`` —
and, when a real call lands, proposes the most likely successors to prefetch.

It gets smarter with every run: transitions are accumulated across the proxy's
lifetime (and can be persisted/loaded as a plain dict). Predictions carry the
empirical transition probability as their confidence, and only successors above
a threshold are proposed, so a flat/unseen distribution stays quiet.

The model predicts *which tool* comes next, not its arguments — so its
proposals usually fire with empty/default arguments. That is still a useful
warm-up for argument-free read-only tools, and it composes with the oracle,
which supplies arguments from intent.
"""

import threading
from typing import Any, Dict, List, Optional

from engram.predictors.base import Prediction, Predictor


class MarkovModel(Predictor):
    name = "markov"

    def __init__(self, min_probability: float = 0.25, top_k: int = 2,
                 min_observations: int = 2, max_rows: int = 2048,
                 max_successors: int = 64):
        self._lock = threading.Lock()
        # prev_tool -> {next_tool -> count}
        self._transitions = {}  # type: Dict[str, Dict[str, int]]
        self._totals = {}       # type: Dict[str, int]
        self.min_probability = min_probability
        self.top_k = top_k
        self.min_observations = min_observations
        # Bound both dimensions so an adversarial or pathological tool catalog
        # can't grow the table without limit over a long-lived session.
        self.max_rows = max_rows
        self.max_successors = max_successors

    def learn(self, prev_tool: Optional[str], next_tool: str) -> None:
        if not prev_tool or not next_tool:
            return
        with self._lock:
            row = self._transitions.setdefault(prev_tool, {})
            is_new_successor = next_tool not in row
            row[next_tool] = row.get(next_tool, 0) + 1
            self._totals[prev_tool] = self._totals.get(prev_tool, 0) + 1
            # Cap successors per row: evict the least-frequent one, keeping
            # _totals consistent by subtracting its count.
            if is_new_successor and len(row) > self.max_successors:
                victim = min(row, key=row.get)
                if victim != next_tool:
                    self._totals[prev_tool] -= row.pop(victim)
            # Cap the number of rows: drop the row with the fewest total
            # observations (least-learned predecessor).
            if len(self._transitions) > self.max_rows:
                victim_row = min(self._totals, key=self._totals.get)
                if victim_row != prev_tool:
                    self._transitions.pop(victim_row, None)
                    self._totals.pop(victim_row, None)

    def on_observed_call(self, tool_name: str, arguments: Dict[str, Any]) -> List[Prediction]:
        with self._lock:
            row = self._transitions.get(tool_name)
            total = self._totals.get(tool_name, 0)
            if not row or total < self.min_observations:
                return []
            # Rank successors by probability, keep the top-k above threshold.
            ranked = sorted(row.items(), key=lambda kv: kv[1], reverse=True)
            predictions = []
            for next_tool, count in ranked[: self.top_k]:
                prob = count / total
                if prob >= self.min_probability:
                    predictions.append(Prediction(
                        next_tool, {}, confidence=prob, source=self.name))
            return predictions

    def export(self) -> Dict[str, Dict[str, int]]:
        """Serialize the transition table (for persistence across runs)."""
        with self._lock:
            return {k: dict(v) for k, v in self._transitions.items()}

    def load(self, table: Dict[str, Dict[str, int]]) -> None:
        """Replace the transition table (e.g. from a prior run)."""
        with self._lock:
            self._transitions = {k: dict(v) for k, v in table.items()}
            self._totals = {k: sum(v.values()) for k, v in self._transitions.items()}
