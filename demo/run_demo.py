#!/usr/bin/env python3
"""Precog demo — race the same agent with and without speculative execution.

Everything printed here is *measured* at runtime against a real MCP server
subprocess; no number is hardcoded. The mock server sleeps ``PRECOG_DEMO_LATENCY``
seconds per tool call to stand in for real API/network I/O, and the simulated
agent "thinks" for a short while before each call (streaming its reasoning to
the proxy). Precog uses that think time to prefetch.

Run::

    python3 demo/run_demo.py
    PRECOG_DEMO_LATENCY=0.6 python3 demo/run_demo.py   # exaggerate the I/O cost
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from demo.harness import close_baseline, connect_baseline, connect_precog  # noqa: E402

LATENCY = float(os.environ.get("PRECOG_DEMO_LATENCY", "0.4"))
THINK = float(os.environ.get("PRECOG_DEMO_THINK", "0.5"))

# The model narrates its whole plan up front — naming three independent
# lookups — then emits the calls one after another. A serial agent pays the I/O
# latency three times (once per call). Precog reads the plan during the single
# think, fires all three read-only calls in parallel, and serves each warm when
# the model finally emits it: the three round trips collapse into one. This is
# the "A/B/C prefetch in parallel" mechanism from the pitch, and it's why the
# speedup grows toward 3x as I/O latency dominates the think.
PLAN = ("Plan: to answer this I need three things. I'll pull the recent orders "
        "for alice, get the customer profile for alice, and fetch the invoice "
        "for ORD-1001. Let me gather all three.")
SCENARIO = [
    ("get_orders", {"customer": "alice"}),
    ("get_customer", {"customer": "alice"}),
    ("fetch_invoice", {"order_id": "ORD-1001"}),
]

# A repeatable, argument-free chain used to show the Markov model learning.
LEARNING_CHAIN = ["get_status", "get_metrics", "get_alerts"]


def _hr(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def run_baseline():
    """Serial agent: think, call, wait — a staircase of stalls."""
    driver, proc = connect_baseline()
    try:
        driver.initialize()
        driver.list_tools()
        start = time.monotonic()
        time.sleep(THINK)                     # model reasons through its plan
        for tool, args in SCENARIO:
            resp = driver.call_tool(tool, args)   # each call pays full I/O latency
            _ = resp                          # would be consumed by the agent
        return time.monotonic() - start
    finally:
        close_baseline(proc)


def run_precog():
    """Speculative agent: reasoning streams during the think, calls return warm."""
    handle = connect_precog(install_demo_rules=True)
    driver = handle.driver
    try:
        driver.initialize()
        driver.list_tools()                   # lets Precog learn read-only hints
        # Give the proxy a beat to ingest tools/list before we speculate.
        time.sleep(0.05)
        start = time.monotonic()
        driver.send_reasoning(PLAN)            # whole plan streamed up front
        time.sleep(THINK)                      # A/B/C prefetch in parallel here
        for tool, args in SCENARIO:
            resp = driver.call_tool(tool, args)    # returns warm — already fetched
            _ = resp
        elapsed = time.monotonic() - start
        return elapsed, handle.proxy.metrics.as_dict()
    finally:
        handle.close()


def run_learning():
    """Run an argument-free chain three times; watch the Markov hit rate climb."""
    handle = connect_precog(install_demo_rules=False, markov_min_observations=1)
    driver = handle.driver
    per_run = []
    try:
        driver.initialize()
        driver.list_tools()
        time.sleep(0.05)
        for run_idx in range(3):
            before = handle.proxy.metrics.as_dict()
            for tool in LEARNING_CHAIN:
                time.sleep(THINK)
                driver.call_tool(tool, {})
            after = handle.proxy.metrics.as_dict()
            calls = after["real_calls"] - before["real_calls"]
            hits = ((after["warm_hits"] + after["late_hits"])
                    - (before["warm_hits"] + before["late_hits"]))
            per_run.append((run_idx + 1, hits, calls))
        return per_run
    finally:
        handle.close()


def run_squash():
    """Drive a misprediction and show it is squashed at no correctness cost.

    The model narrates intent for one tool but then calls a *different*
    read-only tool. Precog speculates the predicted call, the real call misses,
    the wrong speculation is squashed, and the agent still gets the right
    answer. Returns (served_result_text, metrics).
    """
    handle = connect_precog(install_demo_rules=True)
    driver = handle.driver
    try:
        driver.initialize()
        driver.list_tools()
        time.sleep(0.05)
        # Narrate "orders for alice" (Precog prefetches get_orders)...
        driver.send_reasoning("I'll pull the orders for alice.")
        time.sleep(THINK)
        # ...but actually call a different tool. The speculation is wrong.
        resp = driver.call_tool("get_customer", {"customer": "bob"})
        text = resp["result"]["content"][0]["text"]
        # Let the now-unused speculation settle, then reconcile so the wrong
        # guess is accounted for (reconcile normally runs at shutdown).
        time.sleep(LATENCY + 0.1)
        handle.proxy.speculator.reconcile()
        return text, handle.proxy.metrics.as_dict()
    finally:
        handle.close()


def main():
    print("Precog demo — speculative execution for MCP")
    print("downstream tool latency: %.0f ms   |   model think time: %.0f ms/step"
          % (LATENCY * 1000, THINK * 1000))

    _hr("Race 1 — same 3-step agent, with and without Precog")
    print("(the chain-of-thought oracle here uses curated intent rules that")
    print(" capture the customer/order arguments from the narrated plan)")
    baseline = run_baseline()
    precog_elapsed, metrics = run_precog()

    print("\n  baseline (serial)   : %6.0f ms   think -> call -> wait, repeated"
          % (baseline * 1000))
    print("  precog (speculative): %6.0f ms   calls prefetched during the think"
          % (precog_elapsed * 1000))
    if precog_elapsed > 0:
        print("\n  speedup             : %.2fx  end-to-end" % (baseline / precog_elapsed))
    print("  hit rate            : %.0f%%   (%d/%d real calls served warm)"
          % (metrics["hit_rate"] * 100,
             metrics["warm_hits"] + metrics["late_hits"], metrics["real_calls"]))
    print("  downstream I/O saved: %6.0f ms" % (metrics["saved_seconds"] * 1000))
    print("  prediction sources  : %s" % (metrics["by_source"] or "{}"))

    _hr("Race 2 — Markov model learning across three identical runs")
    print("(argument-free read-only chain: %s)" % " -> ".join(LEARNING_CHAIN))
    print("(demo lowers Markov min_observations from the shipped default of 2")
    print(" to 1 so learning surfaces within three runs)")
    per_run = run_learning()
    print()
    for run_idx, hits, calls in per_run:
        bar = "#" * hits
        print("  run %d: %d/%d calls warm  %s" % (run_idx, hits, calls, bar))
    print("\n  The model knows nothing on run 1, then learns the transitions and")
    print("  pre-warms the chain on subsequent runs — branch prediction, for agents.")

    _hr("Race 3 — a misprediction is squashed at no correctness cost")
    text, sq = run_squash()
    print("\n  Narrated 'orders for alice' (precog prefetched get_orders),")
    print("  but the agent actually called get_customer(bob). The wrong guess")
    print("  is squashed; the agent still gets the correct answer:")
    print("\n    result: %s" % text)
    print("    wrong speculations squashed: %d   (precision %.0f%%)"
          % (sq["wrong_speculations"], _precision(sq) * 100))
    print("  A miss costs a wasted read-only fetch, never a wrong answer.")

    _hr("Safety — Precog never speculates a side-effecting tool")
    print("  send_email is annotated readOnlyHint=false, so it is excluded from")
    print("  speculation by construction. Try it: a wrong guess can never send one.")
    print()


def _precision(m):
    fired = m["speculations_fired"]
    if not fired:
        return 0.0
    used = fired - m["wrong_speculations"]
    return max(0.0, used) / fired


if __name__ == "__main__":
    main()
