"""The Precog proxy — drop-in MCP middleware.

Topology::

    agent / host  <-- stdio JSON-RPC -->  Precog  <-- stdio JSON-RPC -->  MCP server
       (upstream)                        (this)                          (downstream)

Precog speaks the MCP stdio framing on both sides. To the agent it *is* the
server; to the server it *is* the client. It forwards everything faithfully,
with three pieces of intelligence layered on:

* It observes ``tools/list`` results to learn which tools exist and which are
  ``readOnlyHint`` (the safety gate's source of truth).
* It resolves every ``tools/call`` against the speculation cache: a hit returns
  a warm result without a downstream round trip; a miss forwards the call and,
  in doing so, teaches the Markov model the tool→tool transition and prefetches
  the likely successor.
* It accepts an optional *hint channel* — custom notifications a host MAY send
  to unlock the chain-of-thought oracle and eager dispatch:

    - ``notifications/precog/reasoning``  params ``{"text": "..."}``
    - ``notifications/precog/tool_intent`` params ``{"name": ..., "arguments": {...}}``

  A host that sends nothing still gets Markov + protocol-safe concurrency with
  zero code changes; a host that forwards its thinking stream additionally gets
  prediction *during the think*.

The pure-MCP signals require **zero** changes to the agent. The hint channel is
purely additive and ignored by servers, so forwarding it is safe and optional.
"""

import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from precog import jsonrpc
from precog.cache import SpeculationCache
from precog.downstream import DownstreamClient
from precog.metrics import Metrics
from precog.predictors import CoTOracle, EagerDispatch, MarkovModel
from precog.predictors.base import Prediction, Predictor
from precog.safety import ToolRegistry
from precog.speculator import Speculator

REASONING_METHOD = "notifications/precog/reasoning"
TOOL_INTENT_METHOD = "notifications/precog/tool_intent"


class Precog:
    """Orchestrates upstream I/O, downstream dispatch, and speculation."""

    def __init__(self, downstream_command: List[str],
                 upstream_in=None, upstream_out=None,
                 enable_cot: bool = True, enable_markov: bool = True,
                 enable_eager: bool = True,
                 late_hit_timeout: float = 30.0,
                 spec_timeout: Optional[float] = None,
                 warm_ttl: float = 30.0,
                 on_log: Optional[Callable[[str], None]] = None,
                 extra_predictors: Optional[List[Predictor]] = None,
                 intent_rules=None):
        # Default to the process's real stdio (buffer = raw bytes).
        self._in = upstream_in if upstream_in is not None else sys.stdin.buffer
        self._out = upstream_out if upstream_out is not None else sys.stdout.buffer
        self._out_lock = threading.Lock()
        self.late_hit_timeout = late_hit_timeout
        # A speculative dispatch is capped well below a real call: a guess that
        # takes as long as the real call would saves nothing and just holds a
        # pool slot. Default to a third of the real timeout (min 5s).
        self.spec_timeout = (spec_timeout if spec_timeout is not None
                             else max(5.0, late_hit_timeout / 3.0))
        # A warm speculative result older than this is considered stale and
        # re-fetched, so a read-only value that changed since prefetch isn't
        # served indefinitely.
        self.warm_ttl = warm_ttl
        self._log = on_log or (lambda m: None)

        self.registry = ToolRegistry()
        self.metrics = Metrics()
        # An evicted, never-consumed speculation is a confirmed misprediction;
        # count it so precision stays honest even across long sessions.
        self.cache = SpeculationCache(
            on_evict_unconsumed=lambda spec: self.metrics.record_wrong(1))
        self.downstream = DownstreamClient(downstream_command, on_log=self._log)
        self.speculator = Speculator(
            self.registry, self.cache, self.metrics,
            dispatch=self._speculative_tool_call, warm_ttl=warm_ttl,
            on_log=self._log)

        self.predictors = []  # type: List[Predictor]
        self.eager = EagerDispatch() if enable_eager else None
        if self.eager:
            self.predictors.append(self.eager)
        self.cot = CoTOracle(self.registry) if enable_cot else None
        if self.cot:
            if intent_rules:
                for rule in intent_rules:
                    self.cot.add_rule(rule)
            self.predictors.append(self.cot)
        self.markov = MarkovModel() if enable_markov else None
        if self.markov:
            self.predictors.append(self.markov)
        if extra_predictors:
            self.predictors.extend(extra_predictors)

        # Tracks the last real tool observed, to feed Markov transitions.
        self._last_tool = None  # type: Optional[str]
        self._last_tool_lock = threading.Lock()
        self._stopped = threading.Event()

        # In-flight request workers, tracked so the run loop can drain them
        # before tearing the downstream down (otherwise an EOF / disconnect
        # would close the pipe out from under a call still in progress).
        self._workers = []  # type: List[threading.Thread]
        self._workers_lock = threading.Lock()

    def _spawn(self, target, name: str) -> None:
        """Start a tracked daemon worker thread for one upstream request."""
        t = threading.Thread(target=target, name=name)
        t.daemon = True
        with self._workers_lock:
            # Opportunistically prune finished workers so the list can't grow
            # without bound over a long-lived connection.
            self._workers = [w for w in self._workers if w.is_alive()]
            self._workers.append(t)
        t.start()

    def _drain_workers(self, timeout: float) -> None:
        """Wait for outstanding request workers to finish (best effort).

        Uses a single wall-clock deadline shared across all workers, so total
        drain time is bounded by ``timeout`` regardless of how many workers are
        outstanding (rather than ``timeout`` per worker).
        """
        with self._workers_lock:
            workers = list(self._workers)
        deadline = time.monotonic() + timeout
        for w in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            w.join(remaining)

    # -- downstream dispatch (used by speculator and by real misses) --------

    def _downstream_tool_call(self, tool_name: str, arguments: Dict[str, Any],
                              timeout: Optional[float] = None):
        """Issue a ``tools/call`` downstream and block for its outcome.

        Returns ``(result, error)`` with exactly one non-None, matching the
        contract the speculator expects. On timeout the abandoned pending slot
        is cancelled so it cannot leak.
        """
        if timeout is None:
            timeout = self.late_hit_timeout
        msg_id, fut = self.downstream.call_with_id(
            "tools/call", {"name": tool_name, "arguments": arguments})
        if not fut.wait(timeout):
            self.downstream.cancel(msg_id)
            return None, jsonrpc.make_error(None, jsonrpc.INTERNAL_ERROR,
                                            "downstream tools/call timed out")["error"]
        if fut.is_error:
            return None, fut.value
        return fut.value, None

    def _speculative_tool_call(self, tool_name: str, arguments: Dict[str, Any]):
        """Dispatch used by the speculator — bounded by ``spec_timeout``.

        A speculation that takes as long as a real call saves nothing, so we
        cap how long a worker may be held by one slow/wedged tool well below
        the real-call timeout. This prevents head-of-line starvation of the
        bounded speculation thread pool.
        """
        return self._downstream_tool_call(tool_name, arguments, timeout=self.spec_timeout)

    # -- upstream write -----------------------------------------------------

    def _write_upstream(self, msg: Dict[str, Any]) -> None:
        with self._out_lock:
            try:
                self._out.write(jsonrpc.encode(msg))
                self._out.flush()
            except (BrokenPipeError, ValueError):
                self._stopped.set()

    # -- prediction plumbing ------------------------------------------------

    def _fire(self, predictions: List[Prediction]) -> None:
        if predictions:
            self.speculator.consider(predictions)

    def _handle_reasoning(self, text: str) -> None:
        if self.cot is not None:
            self._fire(self.cot.on_reasoning(text))

    def _handle_tool_intent(self, name: str, arguments: Dict[str, Any]) -> None:
        if self.eager is not None:
            self._fire(self.eager.on_partial_tool_call(name, arguments))

    # -- the main message handlers -----------------------------------------

    def _on_upstream_message(self, msg: Dict[str, Any]) -> None:
        # 1) Hint-channel notifications are consumed here and NOT forwarded
        #    downstream (servers don't understand them).
        method = msg.get("method")
        if jsonrpc.is_notification(msg):
            if method == REASONING_METHOD:
                self._handle_reasoning((msg.get("params") or {}).get("text", ""))
                return
            if method == TOOL_INTENT_METHOD:
                params = msg.get("params") or {}
                self._handle_tool_intent(params.get("name", ""), params.get("arguments") or {})
                return
            # Any other notification (e.g. initialized) is forwarded verbatim.
            self.downstream.send_raw(msg)
            return

        # A message with a `method` and an explicit `id: null` is neither a
        # valid request (id must not be null) nor a notification (which omits
        # id). Reject it loudly so the agent isn't left waiting, and never
        # forward it downstream.
        if "method" in msg and "id" in msg and msg["id"] is None:
            self._write_upstream(jsonrpc.make_error(
                None, jsonrpc.INVALID_REQUEST, "request id must not be null"))
            return

        if jsonrpc.is_request(msg):
            if method == "tools/call":
                self._handle_tools_call(msg)
                return
            if method == "tools/list":
                self._handle_tools_list(msg)
                return
            # Everything else (initialize, resources/*, prompts/*, ping, ...)
            # is forwarded transparently and its response relayed back.
            self._forward_request(msg)
            return

        # Responses from the agent to server-initiated requests, etc.: forward.
        self.downstream.send_raw(msg)

    def _forward_request(self, msg: Dict[str, Any]) -> None:
        """Forward an arbitrary upstream request and relay its response back."""
        method = msg["method"]
        params = msg.get("params")
        upstream_id = msg["id"]
        msg_id, fut = self.downstream.call_with_id(method, params)

        def relay() -> None:
            if not fut.wait(self.late_hit_timeout):
                self.downstream.cancel(msg_id)
                self._write_upstream(jsonrpc.make_error(
                    upstream_id, jsonrpc.INTERNAL_ERROR, "downstream timed out"))
                return
            if fut.is_error:
                self._write_upstream({"jsonrpc": jsonrpc.JSONRPC_VERSION,
                                      "id": upstream_id, "error": fut.value})
            else:
                self._write_upstream(jsonrpc.make_result(upstream_id, fut.value))

        self._spawn(relay, "precog-relay")

    def _handle_tools_list(self, msg: Dict[str, Any]) -> None:
        upstream_id = msg["id"]
        msg_id, fut = self.downstream.call_with_id("tools/list", msg.get("params"))

        def relay() -> None:
            if not fut.wait(self.late_hit_timeout):
                self.downstream.cancel(msg_id)
                self._write_upstream(jsonrpc.make_error(
                    upstream_id, jsonrpc.INTERNAL_ERROR, "tools/list timed out"))
                return
            if fut.is_error:
                self._write_upstream({"jsonrpc": jsonrpc.JSONRPC_VERSION,
                                      "id": upstream_id, "error": fut.value})
                return
            result = fut.value or {}
            tools = result.get("tools") or []
            self.registry.update_from_list(tools)
            self._log("registry learned %d tools (%d read-only)" % (
                len(tools), sum(1 for t in tools
                                if (t.get("annotations") or {}).get("readOnlyHint") is True)))
            self._write_upstream(jsonrpc.make_result(upstream_id, result))

        self._spawn(relay, "precog-toolslist")

    def _handle_tools_call(self, msg: Dict[str, Any]) -> None:
        upstream_id = msg["id"]
        params = msg.get("params") or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}

        def serve() -> None:
            outcome, result, error = self.speculator.resolve_call(
                tool_name, arguments, wait_timeout=self.late_hit_timeout)

            if outcome == "miss":
                # Execute the call ourselves, then relay.
                result, error = self._downstream_tool_call(tool_name, arguments)

            self._log("tools/call %s -> %s" % (tool_name, outcome))

            # Learn the transition and prefetch the likely successor. A call
            # that errored should not seed the Markov chain (a failed step is
            # not a reliable predecessor), so we pass the error through.
            self._observe_and_predict(tool_name, arguments, error)

            if error is not None:
                self._write_upstream({"jsonrpc": jsonrpc.JSONRPC_VERSION,
                                      "id": upstream_id, "error": error})
            else:
                self._write_upstream(jsonrpc.make_result(upstream_id, result))

        self._spawn(serve, "precog-toolscall")

    def _observe_and_predict(self, tool_name: str, arguments: Dict[str, Any],
                             error: Optional[Dict[str, Any]] = None) -> None:
        if error is not None:
            # A failed call is not a reliable predecessor: do not advance the
            # Markov chain or learn a transition off it. We also skip firing
            # successor speculations, since the agent's path after an error is
            # unpredictable (it may retry, branch, or abort).
            return
        with self._last_tool_lock:
            prev = self._last_tool
            self._last_tool = tool_name
        # Online learning: every predictor sees the transition.
        for p in self.predictors:
            p.learn(prev, tool_name)
        # Predict the successor(s) of this call and speculate them now.
        preds = []  # type: List[Prediction]
        for p in self.predictors:
            preds.extend(p.on_observed_call(tool_name, arguments))
        self._fire(preds)

    # -- run loop -----------------------------------------------------------

    def serve_forever(self) -> None:
        """Start the downstream server and pump upstream messages until EOF."""
        self.downstream.start()
        self._log("precog up: downstream=%s" % " ".join(self.downstream.command))
        try:
            for raw in iter(self._in.readline, b""):
                if self._stopped.is_set():
                    break
                if not raw.strip():
                    continue
                try:
                    msg = jsonrpc.decode(raw)
                except jsonrpc.BatchNotSupported as exc:
                    # Fail loud rather than black-holing the batch: the agent
                    # gets an error instead of hanging forever.
                    self._log("upstream batch rejected: %s" % exc)
                    self._write_upstream(jsonrpc.make_error(
                        None, jsonrpc.INVALID_REQUEST,
                        "JSON-RPC batch requests are not supported by precog"))
                    continue
                except ValueError as exc:
                    self._log("upstream decode error: %s" % exc)
                    self._write_upstream(jsonrpc.make_error(
                        None, jsonrpc.PARSE_ERROR, "parse error: %s" % exc))
                    continue
                self._on_upstream_message(msg)
        finally:
            # Upstream reached EOF (agent disconnected / batch input ended).
            # Drain in-flight request workers so we relay their responses and
            # don't yank the downstream pipe out from under a live call.
            self._stopped.set()
            self._drain_workers(self.late_hit_timeout)
            self.shutdown()

    def shutdown(self) -> None:
        if self._stopped.is_set():
            # Best-effort drain in case shutdown() is called directly.
            self._drain_workers(min(self.late_hit_timeout, 5.0))
        self._stopped.set()
        self.speculator.shutdown()
        self.downstream.close()
        self._log("precog metrics: %s" % self.metrics.as_dict())
