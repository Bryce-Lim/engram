"""Run a plan with and without Engram and return MEASURED timings.

This is the real comparison engine behind the web UI. Both runs talk to a real
mock MCP server subprocess over real JSON-RPC; the only simulated quantities are
the per-tool latency (``time.sleep`` in the server) and the model "think" time
(a sleep), exactly as in the CLI demo. Everything else — the proxy, speculation,
cache, parallel prefetch, per-call timings — is real.
"""

import os
import re
import sys
import time
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from demo.harness import (close_baseline, connect_baseline,  # noqa: E402
                          connect_engram)
from engram.predictors.cot_oracle import IntentRule  # noqa: E402


def _build_rules(rule_dicts: List[Dict[str, Any]]) -> List[IntentRule]:
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


def run_comparison(plan: Dict[str, Any], latency: float = 0.4,
                   think: float = 1.0) -> Dict[str, Any]:
    """Execute ``plan`` both ways and return timings + per-call detail."""
    os.environ["ENGRAM_DEMO_LATENCY"] = str(latency)
    reasoning = plan.get("reasoning", "")
    calls = plan.get("calls", [])
    rules = _build_rules(plan.get("rules", []))

    # ---- WITH Engram --------------------------------------------------
    handle = connect_engram(install_demo_rules=False)
    if rules and handle.proxy.cot is not None:
        for r in rules:
            handle.proxy.cot.add_rule(r)
    per_call = []  # type: List[Dict[str, Any]]
    engram_total = 0.0
    try:
        handle.driver.initialize()
        tools = handle.driver.list_tools()["result"]["tools"]
        readonly = {t["name"] for t in tools
                    if (t.get("annotations") or {}).get("readOnlyHint") is True}
        time.sleep(0.05)
        start = time.monotonic()
        if reasoning:
            handle.driver.send_reasoning(reasoning)
        if think > 0:
            time.sleep(think)
        for c in calls:
            name, args = c["name"], c.get("arguments") or {}
            emit = time.monotonic()
            resp = handle.driver.call_tool(name, args)
            dt = (time.monotonic() - emit) * 1000.0
            hit = dt < latency * 1000.0 * 0.5
            text = ""
            if "result" in resp:
                content = resp["result"].get("content") or [{}]
                text = content[0].get("text", "")
            per_call.append({
                "name": name,
                "arguments": args,
                "ms": round(dt, 1),
                "outcome": "hit" if hit else "miss",
                "read_only": name in readonly,
                "result": text,
            })
        engram_total = (time.monotonic() - start) * 1000.0
        metrics = handle.proxy.metrics.as_dict()
    finally:
        handle.close()

    # ---- WITHOUT Engram (serial baseline) -----------------------------
    driver, proc = connect_baseline()
    baseline_total = 0.0
    try:
        driver.initialize()
        driver.list_tools()
        start = time.monotonic()
        if think > 0:
            time.sleep(think)  # the model still reasons; the server sits idle
        for c in calls:
            driver.call_tool(c["name"], c.get("arguments") or {})
        baseline_total = (time.monotonic() - start) * 1000.0
    finally:
        close_baseline(proc)

    hits = sum(1 for c in per_call if c["outcome"] == "hit")
    speedup = (baseline_total / engram_total) if engram_total > 0 else 0.0

    return {
        "reasoning": reasoning,
        "latency_ms": round(latency * 1000),
        "think_ms": round(think * 1000),
        "baseline_total_ms": round(baseline_total, 1),
        "engram_total_ms": round(engram_total, 1),
        "saved_ms": round(baseline_total - engram_total, 1),
        "speedup": round(speedup, 2),
        "num_calls": len(calls),
        "hits": hits,
        "misses": len(calls) - hits,
        "hit_rate": round(hits / len(calls), 3) if calls else 0.0,
        "calls": per_call,
        "metrics": metrics,
    }
