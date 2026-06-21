"""Streaming comparison — run both lanes concurrently and emit live events.

Unlike :mod:`web.compare` (which returns one final blob), this runs the Engram
lane and the baseline lane in parallel threads against their own real MCP server
subprocesses, and yields NDJSON events *as they actually occur* on the wall
clock. The browser renders those events into a live split-screen race.

Every timestamp is real: ``t`` is milliseconds since a shared start, measured,
not scripted. The only simulated quantities remain per-tool latency and the
model think time, exactly as elsewhere.

Event shapes (one JSON object per yielded line):
  {"ev":"start","lanes":["engram","baseline"],"num_calls":N,"latency_ms":..,"think_ms":..}
  {"ev":"think","lane":"engram","phase":"begin","t":..}
  {"ev":"think","lane":"engram","phase":"end","t":..}
  {"ev":"spec","lane":"engram","name":..,"t":..}              # a speculation fired
  {"ev":"call","lane":..,"phase":"begin","i":k,"name":..,"t":..}
  {"ev":"call","lane":..,"phase":"end","i":k,"name":..,"outcome":"hit|miss",
        "read_only":bool,"ms":..,"t":..,"result":..}
  {"ev":"lane_done","lane":..,"total_ms":..,"t":..}
  {"ev":"done","summary":{...same fields as compare.run_comparison...}}
"""

import os
import queue
import re
import sys
import threading
import time
from typing import Any, Dict, Iterator, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from demo.harness import (close_baseline, connect_baseline,  # noqa: E402
                          connect_engram)
from engram.predictors.cot_oracle import IntentRule  # noqa: E402
from web import learning  # noqa: E402


def _build_rules(rule_dicts):
    rules = []
    for r in rule_dicts:
        flags = 0
        for ch in r.get("flags", ""):
            flags |= {"i": re.I, "m": re.M, "s": re.S, "x": re.X}.get(ch, 0)
        rules.append(IntentRule(re.compile(r["pattern"], flags), r["tool"],
                                arg_map=r.get("args") or {},
                                static_args=r.get("static_args") or {},
                                confidence=float(r.get("confidence", 0.9))))
    return rules


def stream_comparison(plan: Dict[str, Any], latency: float = 0.4,
                      think: float = 1.0, learn: bool = True) -> Iterator[Dict[str, Any]]:
    """Yield live race events for ``plan``. Generator; consume to completion.

    When ``learn`` is true, the Markov sequence model persists across runs of
    the same scenario (via :mod:`web.learning`): run 1 is a cold start (only the
    chain-of-thought oracle predicts), and later runs additionally pre-warm the
    learned tool->tool chain. The ``start`` event carries the ``run`` index so
    the UI can show "Run #N · learning".
    """
    os.environ["ENGRAM_DEMO_LATENCY"] = str(latency)
    reasoning = plan.get("reasoning", "")
    calls = plan.get("calls", [])
    rules = _build_rules(plan.get("rules", []))

    # Load any transitions learned on prior runs of this same scenario shape.
    signature = learning.scenario_signature(calls)
    prior = learning.get_state(signature) if learn else {"table": {}, "runs": 0}
    run_index = prior["runs"] + 1

    events = queue.Queue()  # type: queue.Queue
    # Shared start time set once both lanes are connected and ready.
    state = {"t0": None}
    ready = threading.Barrier(3)  # engram thread, baseline thread, main

    def sync_start():
        """Align both lanes at the start line; tolerate a lane dying early."""
        try:
            ready.wait(timeout=15)
        except (threading.BrokenBarrierError, Exception):
            pass

    def now_ms():
        if state["t0"] is None:
            return 0.0
        return round((time.monotonic() - state["t0"]) * 1000.0, 1)

    def emit(obj):
        events.put(obj)

    # ---- Engram lane -------------------------------------------------
    engram_summary = {}

    def engram_lane():
        handle = connect_engram(install_demo_rules=False,
                                on_log=_spec_logger(emit, now_ms, state))
        if rules and handle.proxy.cot is not None:
            for r in rules:
                handle.proxy.cot.add_rule(r)
        # Seed the Markov model with transitions learned on prior runs of this
        # scenario, so run 2+ pre-warms the learned chain (real cross-run
        # learning, persisted in web.learning — not a scripted effect).
        if learn and prior["table"] and handle.proxy.markov is not None:
            handle.proxy.markov.load(prior["table"])
            handle.proxy.markov.min_observations = 1
        try:
            handle.driver.initialize()
            tools = handle.driver.list_tools()["result"]["tools"]
            readonly = {t["name"] for t in tools
                        if (t.get("annotations") or {}).get("readOnlyHint") is True}
            time.sleep(0.05)
            sync_start()  # align start with the baseline lane
            start = time.monotonic()
            emit({"ev": "think", "lane": "engram", "phase": "begin", "t": now_ms()})
            if reasoning:
                handle.driver.send_reasoning(reasoning)
            if think > 0:
                time.sleep(think)
            emit({"ev": "think", "lane": "engram", "phase": "end", "t": now_ms()})
            per_call = []
            for i, c in enumerate(calls):
                name, args = c["name"], c.get("arguments") or {}
                emit({"ev": "call", "lane": "engram", "phase": "begin",
                      "i": i, "name": name, "t": now_ms()})
                emit_t = time.monotonic()
                resp = handle.driver.call_tool(name, args)
                dt = (time.monotonic() - emit_t) * 1000.0
                hit = dt < latency * 1000.0 * 0.5
                text = ""
                if "result" in resp:
                    content = resp["result"].get("content") or [{}]
                    text = content[0].get("text", "")
                per_call.append({"name": name, "arguments": args, "ms": round(dt, 1),
                                 "outcome": "hit" if hit else "miss",
                                 "read_only": name in readonly, "result": text})
                emit({"ev": "call", "lane": "engram", "phase": "end", "i": i,
                      "name": name, "outcome": "hit" if hit else "miss",
                      "read_only": name in readonly, "ms": round(dt, 1),
                      "t": now_ms(), "result": text})
            total = (time.monotonic() - start) * 1000.0
            engram_summary["calls"] = per_call
            engram_summary["total_ms"] = round(total, 1)
            engram_summary["metrics"] = handle.proxy.metrics.as_dict()
            # Persist what this run learned so the next run of the same scenario
            # starts warmer.
            if learn and handle.proxy.markov is not None:
                try:
                    learning.record_run(signature, handle.proxy.markov.export())
                except Exception:
                    pass
            emit({"ev": "lane_done", "lane": "engram",
                  "total_ms": round(total, 1), "t": now_ms()})
        finally:
            handle.close()

    # ---- Baseline lane ----------------------------------------------
    baseline_summary = {}

    def baseline_lane():
        driver, proc = connect_baseline()
        try:
            driver.initialize()
            driver.list_tools()
            time.sleep(0.05)
            sync_start()  # align start
            start = time.monotonic()
            emit({"ev": "think", "lane": "baseline", "phase": "begin", "t": now_ms()})
            if think > 0:
                time.sleep(think)  # model reasons; server idle
            emit({"ev": "think", "lane": "baseline", "phase": "end", "t": now_ms()})
            for i, c in enumerate(calls):
                name, args = c["name"], c.get("arguments") or {}
                emit({"ev": "call", "lane": "baseline", "phase": "begin",
                      "i": i, "name": name, "t": now_ms()})
                emit_t = time.monotonic()
                driver.call_tool(name, args)
                dt = (time.monotonic() - emit_t) * 1000.0
                emit({"ev": "call", "lane": "baseline", "phase": "end", "i": i,
                      "name": name, "outcome": "miss", "ms": round(dt, 1),
                      "t": now_ms()})
            total = (time.monotonic() - start) * 1000.0
            baseline_summary["total_ms"] = round(total, 1)
            emit({"ev": "lane_done", "lane": "baseline",
                  "total_ms": round(total, 1), "t": now_ms()})
        finally:
            close_baseline(proc)

    pt = threading.Thread(target=_guard(engram_lane, emit, "engram"), daemon=True)
    bt = threading.Thread(target=_guard(baseline_lane, emit, "baseline"), daemon=True)
    pt.start()
    bt.start()

    # Both threads connect, then wait on the barrier; main releases the start.
    # Set t0 just before releasing the barrier so lane timestamps are relative
    # to the same instant.
    state["t0"] = time.monotonic()
    sync_start()

    yield {"ev": "start", "lanes": ["engram", "baseline"], "num_calls": len(calls),
           "latency_ms": round(latency * 1000), "think_ms": round(think * 1000),
           "reasoning": reasoning, "run": run_index, "learning": learn}

    done_lanes = 0
    # Drain until both lanes finish; a small sentinel timeout guards against hangs.
    deadline = time.monotonic() + 60
    while done_lanes < 2 and time.monotonic() < deadline:
        try:
            obj = events.get(timeout=0.2)
        except queue.Empty:
            continue
        if obj.get("ev") == "lane_done":
            done_lanes += 1
        yield obj

    pt.join(timeout=2)
    bt.join(timeout=2)

    # Final summary mirrors compare.run_comparison so the cards reuse it.
    p_total = engram_summary.get("total_ms", 0.0)
    b_total = baseline_summary.get("total_ms", 0.0)
    per_call = engram_summary.get("calls", [])
    hits = sum(1 for c in per_call if c["outcome"] == "hit")
    yield {"ev": "done", "summary": {
        "reasoning": reasoning,
        "latency_ms": round(latency * 1000),
        "think_ms": round(think * 1000),
        "baseline_total_ms": b_total,
        "engram_total_ms": p_total,
        "saved_ms": round(b_total - p_total, 1),
        "speedup": round((b_total / p_total), 2) if p_total else 0.0,
        "num_calls": len(calls),
        "hits": hits,
        "misses": len(calls) - hits,
        "hit_rate": round(hits / len(calls), 3) if calls else 0.0,
        "calls": per_call,
        "metrics": engram_summary.get("metrics", {}),
        "run": run_index,
        "markov_hits": sum(1 for c in per_call
                           if c["outcome"] == "hit"
                           and (engram_summary.get("metrics", {})
                                .get("by_source", {}).get("markov", 0) > 0)),
        "by_source": engram_summary.get("metrics", {}).get("by_source", {}),
    }}


def _spec_logger(emit, now_ms, state):
    """Translate Engram's internal 'speculate[...]' logs into spec events."""
    def log(message):
        if state.get("t0") is None:
            return
        if message.startswith("speculate["):
            # format: speculate[source] tool {args} (conf=..)
            m = re.match(r"speculate\[[^\]]+\]\s+(\S+)", message)
            if m:
                emit({"ev": "spec", "lane": "engram", "name": m.group(1), "t": now_ms()})
    return log


def _guard(fn, emit, lane):
    """Wrap a lane so an exception becomes an event instead of a silent death."""
    def wrapped():
        try:
            fn()
        except Exception as exc:  # noqa
            emit({"ev": "lane_error", "lane": lane, "error": str(exc)})
            emit({"ev": "lane_done", "lane": lane, "total_ms": 0.0, "t": 0.0})
    return wrapped
