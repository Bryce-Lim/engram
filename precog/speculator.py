"""The speculation engine — where prediction, safety, and dispatch meet.

The speculator is the heart of Precog. It:

1. takes :class:`~precog.predictors.base.Prediction` objects from the predictors,
2. drops any whose tool is not read-only (the safety gate — a hard correctness
   boundary; nothing speculative is dispatched without passing it),
3. reserves a :class:`~precog.cache.Speculation` slot per unique signature so a
   given call is fired downstream at most once, and
4. dispatches the downstream ``tools/call`` on a worker thread so many
   speculations overlap.

When a *real* tool call arrives, :meth:`resolve_call` looks it up by signature:

* **warm hit** — speculation already settled → return its result in ~0ms.
* **late hit** — speculation still in flight → block on it (a fraction of the
  full latency, since it had a head start) and return its result.
* **miss** — no speculation → the caller must execute the call normally.

The CPU analogy is exact: predict, execute speculatively, commit on a hit,
squash on a miss.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from precog import jsonrpc
from precog.cache import SpeculationCache, Speculation, canonical_signature
from precog.metrics import Metrics
from precog.predictors.base import Prediction
from precog.safety import ToolRegistry, is_speculatable


class Speculator:
    # Error codes that are safe to serve from a speculation on a hit: they are
    # deterministic properties of the call itself (the real call would get the
    # same answer), so caching them is correct and saves the round trip.
    # Transient/internal errors (e.g. timeouts, INTERNAL_ERROR) must NOT be
    # served — the real call should re-execute and may well succeed.
    DETERMINISTIC_ERROR_CODES = frozenset([
        jsonrpc.METHOD_NOT_FOUND, jsonrpc.INVALID_PARAMS,
        jsonrpc.INVALID_REQUEST, jsonrpc.PARSE_ERROR,
    ])

    def __init__(self, registry: ToolRegistry, cache: SpeculationCache,
                 metrics: Metrics,
                 dispatch: Callable[[str, Dict[str, Any]], "Tuple[Any, Optional[Dict[str, Any]]]"],
                 max_workers: int = 8, warm_ttl: float = 30.0,
                 on_log: Optional[Callable[[str], None]] = None):
        """``dispatch`` performs the actual downstream ``tools/call`` and returns
        ``(result, error)`` where exactly one is non-None. It is injected so the
        engine can be unit-tested without a real subprocess. ``warm_ttl`` bounds
        how long a settled speculation may be served warm before it is treated
        as stale and re-fetched.
        """
        self.registry = registry
        self.cache = cache
        self.metrics = metrics
        self._dispatch = dispatch
        self.warm_ttl = warm_ttl
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="precog-spec")
        self._on_log = on_log or (lambda m: None)

    def _serveable(self, spec: Speculation) -> bool:
        """Whether a settled speculation may be served as a hit.

        Rejects: safety-gate aborts (never dispatched), stale results (older
        than ``warm_ttl``), and non-deterministic error results.
        """
        if spec.aborted:
            return False
        if spec.error is not None:
            code = spec.error.get("code")
            return code in self.DETERMINISTIC_ERROR_CODES
        # Successful result: honor the freshness window.
        age = (time.monotonic() - spec.completed_at) if spec.completed_at else 0.0
        return age <= self.warm_ttl

    # -- prediction intake --------------------------------------------------

    def consider(self, predictions: List[Prediction]) -> List[Speculation]:
        """Filter predictions through the safety gate and fire the survivors.

        Returns the speculations this call actually dispatched (newly created),
        for telemetry/testing. Predictions for non-read-only tools, or for
        signatures already in flight, are silently dropped here — the latter
        because :meth:`SpeculationCache.reserve` dedupes them.
        """
        fired = []
        for pred in predictions:
            if not is_speculatable(self.registry, pred.tool_name):
                # Safety gate: never speculate a tool that can mutate state.
                continue
            sig = canonical_signature(pred.tool_name, pred.arguments)
            spec, created = self.cache.reserve(sig, pred.tool_name, pred.arguments, pred.source)
            if not created:
                continue  # already being speculated (by this or another predictor)
            self.metrics.record_speculation(pred.source)
            self._on_log("speculate[%s] %s %s (conf=%.2f)" % (
                pred.source, pred.tool_name, pred.arguments, pred.confidence))
            self._pool.submit(self._run_speculation, spec)
            fired.append(spec)
        return fired

    def _run_speculation(self, spec: Speculation) -> None:
        # Re-check the safety gate at the instant of dispatch, not just when the
        # prediction was considered. A server MAY change a tool's annotations
        # between tools/list responses (latest-wins in the registry); if the
        # tool flipped to non-read-only in the window between consider() and
        # this worker running, we must NOT issue the speculative call. We settle
        # the spec as an error (and drop it) so any real call already attached
        # as a late hit is released immediately and falls back to a fresh,
        # properly-ordered execution rather than blocking until the timeout.
        if not is_speculatable(self.registry, spec.tool_name):
            self._on_log("speculation aborted (no longer read-only): %s" % spec.tool_name)
            spec.settle_aborted({"code": -32603,
                                 "message": "speculation aborted: tool no longer read-only"})
            self.cache.discard(spec.signature)
            return
        try:
            result, error = self._dispatch(spec.tool_name, spec.arguments)
        except Exception as exc:  # never let a worker thread die silently
            spec.settle_error({"code": -32603, "message": "speculation failed: %s" % exc})
            return
        if error is not None:
            spec.settle_error(error)
        else:
            spec.settle_result(result)

    # -- real-call resolution ----------------------------------------------

    def resolve_call(self, tool_name: str, arguments: Dict[str, Any],
                     wait_timeout: Optional[float] = None
                     ) -> "Tuple[str, Optional[Any], Optional[Dict[str, Any]]]":
        """Resolve a *real* tool call against outstanding speculations.

        Returns ``(outcome, result, error)`` where ``outcome`` is one of
        ``"warm"``, ``"late"``, or ``"miss"``. On a miss, ``result``/``error``
        are both None and the caller must execute the call itself. A settled
        speculation is only served as a hit when :meth:`_serveable` allows it
        (not aborted, not stale, and any error is deterministic); otherwise the
        call falls back to a fresh, correctly-ordered execution.
        """
        sig = canonical_signature(tool_name, arguments)
        spec = self.cache.claim(sig)
        if spec is None:
            self.metrics.record_miss()
            return "miss", None, None

        if spec.is_ready:
            if not self._serveable(spec):
                # Aborted, stale, or a transient error — re-execute fresh.
                self.cache.discard(sig)
                self.metrics.record_miss()
                return "miss", None, None
            # Warm hit: the whole downstream round trip is saved.
            saved = spec.latency or 0.0
            self.metrics.record_warm_hit(saved)
            self.cache.discard(sig)
            return "warm", spec.result, spec.error

        # Late hit: attach to the in-flight speculation. The head start it
        # already had is the time we save versus issuing the call now.
        head_start = time.monotonic() - spec.created_at
        if not spec.done.wait(wait_timeout):
            # Speculation is taking too long; fall back to a fresh call so we
            # never hang a real request on a slow guess.
            self._on_log("late-hit timeout, falling back: %s" % tool_name)
            self.cache.discard(sig)
            self.metrics.record_miss()
            return "miss", None, None
        if not self._serveable(spec):
            # Settled while we waited, but not serveable (abort/stale/transient
            # error): fall back to a fresh execution.
            self.cache.discard(sig)
            self.metrics.record_miss()
            return "miss", None, None
        self.metrics.record_late_hit(head_start)
        self.cache.discard(sig)
        return "late", spec.result, spec.error

    # -- learning -----------------------------------------------------------

    def reconcile(self) -> None:
        """Account for speculations that were fired but never claimed (wrong).

        Called at shutdown. Any speculation still in the cache that was never
        consumed by a real call is a misprediction — whether or not it has
        settled yet (an in-flight wrong guess is still wrong). We count them so
        ``precision`` is honest. Capacity-evicted mispredictions are counted
        separately via the cache's eviction callback, so the two paths together
        cover every fired-but-unused speculation.
        """
        wrong = 0
        for sig, spec in self.cache.snapshot().items():
            if not spec.consumed:
                wrong += 1
        if wrong:
            self.metrics.record_wrong(wrong)

    def shutdown(self) -> None:
        self.reconcile()
        self._pool.shutdown(wait=False)
