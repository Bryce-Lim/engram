"""Counters and timing for the speculation engine.

These metrics are what make the value visible: how many real tool calls were
served warm (a hit), how many waited on an in-flight speculation (a late hit),
how many missed, and how much wall-clock the hits saved. Everything here is
measured at runtime — nothing is hardcoded.
"""

import threading
from typing import Any, Dict


class Metrics:
    """Thread-safe accumulator of speculation outcomes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.speculations_fired = 0      # downstream calls issued on a guess
        self.warm_hits = 0               # real call found a ready cached result
        self.late_hits = 0               # real call attached to an in-flight spec
        self.misses = 0                  # real call had no matching speculation
        self.wrong_speculations = 0      # fired specs never claimed by a real call
        self.saved_seconds = 0.0         # downstream latency avoided on hits
        self.real_calls = 0              # total real tools/call requests seen
        self._by_source = {}             # type: Dict[str, int]

    def record_speculation(self, source: str) -> None:
        with self._lock:
            self.speculations_fired += 1
            self._by_source[source] = self._by_source.get(source, 0) + 1

    def record_warm_hit(self, saved: float) -> None:
        with self._lock:
            self.real_calls += 1
            self.warm_hits += 1
            self.saved_seconds += max(0.0, saved)

    def record_late_hit(self, saved: float) -> None:
        with self._lock:
            self.real_calls += 1
            self.late_hits += 1
            self.saved_seconds += max(0.0, saved)

    def record_miss(self) -> None:
        with self._lock:
            self.real_calls += 1
            self.misses += 1

    def record_wrong(self, n: int = 1) -> None:
        with self._lock:
            self.wrong_speculations += n

    @property
    def hits(self) -> int:
        with self._lock:
            return self.warm_hits + self.late_hits

    def hit_rate(self) -> float:
        """Fraction of real calls served by a speculation (warm or late)."""
        with self._lock:
            if self.real_calls == 0:
                return 0.0
            return (self.warm_hits + self.late_hits) / self.real_calls

    def precision(self) -> float:
        """Fraction of fired speculations that a real call ended up using."""
        with self._lock:
            if self.speculations_fired == 0:
                return 0.0
            used = self.speculations_fired - self.wrong_speculations
            return max(0.0, used) / self.speculations_fired

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "real_calls": self.real_calls,
                "speculations_fired": self.speculations_fired,
                "warm_hits": self.warm_hits,
                "late_hits": self.late_hits,
                "misses": self.misses,
                "wrong_speculations": self.wrong_speculations,
                "hit_rate": round((self.warm_hits + self.late_hits) / self.real_calls, 4)
                if self.real_calls else 0.0,
                "saved_seconds": round(self.saved_seconds, 4),
                "by_source": dict(self._by_source),
            }
