"""The speculation cache: canonical call signatures and in-flight tracking.

A speculative tool call is identified by a *signature* derived from its tool
name and arguments. The cache distinguishes three states for a signature:

* **absent** — never speculated; a real call must execute it from scratch.
* **in-flight** — speculatively dispatched, result not back yet. A real call
  that matches should *attach* to the running speculation and await it (a
  "late hit") rather than issue a duplicate downstream call.
* **ready** — the speculative result has arrived and is cached warm. A real
  call returns it in ~0ms (a "warm hit").

This module is concurrency-aware (it is touched by the proxy's reader thread
and by speculation worker threads) and guards all mutation with a single lock.
It holds no network or process state, so it is trivially unit-testable.
"""

import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


def canonical_signature(tool_name: str, arguments: Optional[Dict[str, Any]]) -> str:
    """Return a stable, hashable signature for a tool call.

    Arguments are serialized with sorted keys so that semantically identical
    calls (same args, different key order) collapse to the same signature.
    This is what lets a prediction match the real call that follows.
    """
    args = arguments if arguments is not None else {}
    try:
        encoded = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = repr(args)
    return tool_name + "\x00" + encoded


class Speculation:
    """A single speculative execution and its lifecycle state.

    A ``threading.Event`` (``done``) is set when the result (or error) lands,
    so a late hit can block on it. ``result`` holds the downstream ``tools/call``
    *result payload* on success; ``error`` holds a JSON-RPC error object on
    failure. Exactly one of them is non-None once ``done`` is set.
    """

    __slots__ = ("signature", "tool_name", "arguments", "created_at",
                 "completed_at", "result", "error", "done", "consumed", "source",
                 "aborted")

    def __init__(self, signature: str, tool_name: str, arguments: Dict[str, Any], source: str):
        self.signature = signature
        self.tool_name = tool_name
        self.arguments = arguments
        self.source = source            # which predictor proposed it
        self.created_at = time.monotonic()
        self.completed_at = None        # type: Optional[float]
        self.result = None              # type: Optional[Any]
        self.error = None               # type: Optional[Dict[str, Any]]
        self.done = threading.Event()
        self.consumed = False           # has a real call already claimed it?
        self.aborted = False            # safety-gate abort: never dispatched downstream

    def settle_result(self, result: Any) -> None:
        self.result = result
        self.completed_at = time.monotonic()
        self.done.set()

    def settle_error(self, error: Dict[str, Any]) -> None:
        self.error = error
        self.completed_at = time.monotonic()
        self.done.set()

    def settle_aborted(self, error: Dict[str, Any]) -> None:
        """Settle as a safety-gate abort — the call was never dispatched.

        A waiter that attached as a late hit must NOT consume this as a result;
        it should fall back to a fresh, correctly-ordered execution.
        """
        self.aborted = True
        self.settle_error(error)

    @property
    def is_ready(self) -> bool:
        return self.done.is_set()

    @property
    def latency(self) -> Optional[float]:
        """Seconds from dispatch to settle, or None if still in flight."""
        if self.completed_at is None:
            return None
        return self.completed_at - self.created_at


class SpeculationCache:
    """Thread-safe registry of speculations keyed by canonical signature."""

    def __init__(self, max_entries: int = 256,
                 on_evict_unconsumed: Optional[Callable[[Speculation], None]] = None):
        self._lock = threading.Lock()
        self._entries = {}              # type: Dict[str, Speculation]
        self._order = []                # type: list  # insertion order for LRU-ish eviction
        self.max_entries = max_entries
        # Called (outside the lock) when a settled-but-never-consumed spec is
        # evicted for capacity — i.e. a confirmed misprediction. Lets metrics
        # count wrong guesses that are dropped before shutdown reconciliation.
        self._on_evict_unconsumed = on_evict_unconsumed

    def get(self, signature: str) -> Optional[Speculation]:
        with self._lock:
            return self._entries.get(signature)

    def reserve(self, signature: str, tool_name: str, arguments: Dict[str, Any],
                source: str) -> Tuple[Speculation, bool]:
        """Atomically get-or-create a speculation for ``signature``.

        Returns ``(speculation, created)`` where ``created`` is True iff this
        call is responsible for dispatching it downstream. This prevents two
        predictors (or a predictor and a real call) from racing to issue the
        same downstream request twice.
        """
        evicted = []  # type: list
        with self._lock:
            existing = self._entries.get(signature)
            if existing is not None:
                return existing, False
            spec = Speculation(signature, tool_name, arguments, source)
            self._entries[signature] = spec
            self._order.append(signature)
            evicted = self._evict_if_needed_locked()
            created = (spec, True)
        # Notify about evicted mispredictions outside the lock.
        if self._on_evict_unconsumed is not None:
            for victim in evicted:
                if not victim.consumed:
                    self._on_evict_unconsumed(victim)
        return created

    def claim(self, signature: str) -> Optional[Speculation]:
        """Mark a speculation as consumed by a real call and return it.

        Returns None if there is no speculation for the signature. A second
        claim of the same signature still returns it (idempotent) but only the
        first claim flips ``consumed`` — callers use the return value, not the
        flag, to decide whether they got a hit.
        """
        with self._lock:
            spec = self._entries.get(signature)
            if spec is None:
                return None
            spec.consumed = True
            return spec

    def discard(self, signature: str) -> None:
        """Drop a speculation (a squash). Safe if absent."""
        with self._lock:
            if signature in self._entries:
                del self._entries[signature]
                try:
                    self._order.remove(signature)
                except ValueError:
                    pass

    def _evict_if_needed_locked(self) -> list:
        """Evict settled entries when over capacity (lock held).

        Crucially, never evict an in-flight (unsettled) speculation: a worker
        is still running for it, and dropping it from ``_entries`` would (a)
        orphan that worker and (b) make a concurrent real call see no entry, so
        it would issue a *second* downstream call — breaking the "fire at most
        once" guarantee. We therefore evict only *settled* entries, oldest
        first, preferring already-consumed ones. If everything over the cap is
        still in flight, we let the cache exceed ``max_entries`` transiently;
        the in-flight count is naturally bounded by the dispatch pool, so it
        cannot grow without bound. Returns the evicted Speculations.
        """
        evicted = []  # type: list
        while len(self._order) > self.max_entries:
            victim_sig = None
            # Prefer the oldest settled+consumed entry, then oldest settled.
            for sig in self._order:
                spec = self._entries.get(sig)
                if spec is not None and spec.is_ready and spec.consumed:
                    victim_sig = sig
                    break
            if victim_sig is None:
                for sig in self._order:
                    spec = self._entries.get(sig)
                    if spec is not None and spec.is_ready:
                        victim_sig = sig
                        break
            if victim_sig is None:
                # Nothing settled to evict — all over-cap entries are in flight.
                # Leave them; they'll be discarded/evicted once they settle.
                break
            self._order.remove(victim_sig)
            spec = self._entries.pop(victim_sig, None)
            if spec is not None:
                evicted.append(spec)
        return evicted

    def snapshot(self) -> Dict[str, Speculation]:
        with self._lock:
            return dict(self._entries)
