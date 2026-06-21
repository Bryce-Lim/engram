#!/usr/bin/env python3
"""Test Precog's prediction on YOUR reasoning text and tool calls.

This is a playground: you provide the model's reasoning (what it "narrates"
while thinking) and the tool call(s) the agent then makes. The script streams
your reasoning to an in-process Precog, waits a configurable "think" interval
(during which Precog prefetches), fires your call(s), and reports for each one
whether it was served warm (a hit) or had to execute fresh (a miss) — with
timings — plus the final metrics.

It runs against the bundled mock MCP server, whose read-only tools are:
    search, get_orders, get_customer, fetch_invoice,
    get_status, get_metrics, get_alerts        (all readOnlyHint: true)
    send_email                                  (readOnlyHint: false — never speculated)

Two ways to make the chain-of-thought oracle predict a call from your text:
  1. Auto-keywords: mention a tool's name/keywords in the reasoning, e.g.
     "let me check the orders" → get_orders (empty-args prediction).
  2. Explicit rules (--rules FILE): regexes that capture arguments from the
     text, e.g. "orders for (?P<customer>\\w+)" → get_orders{customer=...}.
     See demo/rules.example.json. Rules are required to warm an *argument-
     bearing* call, because auto-keywords can only guess the tool, not its args.

Examples
--------
# Quick: keyword prediction of an argument-free tool
python3 demo/try_prompt.py \\
    --reasoning "first let me check the current pipeline status" \\
    --call get_status

# Argument capture via rules (matches the real CLI's --rules)
python3 demo/try_prompt.py --rules demo/rules.example.json \\
    --reasoning "I'll pull the orders for alice and the profile for alice" \\
    --call get_orders '{"customer": "alice"}' \\
    --call get_customer '{"customer": "alice"}'

# Drive the whole thing from one JSON scenario file
python3 demo/try_prompt.py --scenario demo/scenario.example.json
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from demo.harness import connect_baseline, close_baseline, connect_precog  # noqa: E402
from precog.config import ConfigError, load_intent_rules  # noqa: E402
from precog.predictors.cot_oracle import IntentRule  # noqa: E402


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="try_prompt",
        description="Test Precog prediction on your own reasoning + tool calls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--reasoning", default="",
                   help="the model's reasoning text (streamed to Precog before the calls)")
    p.add_argument("--call", action="append", nargs="+", metavar=("NAME", "ARGS_JSON"),
                   default=[], help="a tool call: NAME [JSON-args]. Repeatable, in order.")
    p.add_argument("--rules", metavar="FILE",
                   help="JSON intent-rules file (enables argument-capturing prediction)")
    p.add_argument("--scenario", metavar="FILE",
                   help="JSON file with {reasoning, calls:[{name,arguments}], rules?}")
    p.add_argument("--latency", type=float,
                   default=float(os.environ.get("PRECOG_DEMO_LATENCY", "0.4")),
                   help="simulated per-tool server latency, seconds (default 0.4)")
    p.add_argument("--think", type=float,
                   default=float(os.environ.get("PRECOG_DEMO_THINK", "0.6")),
                   help="seconds to 'think' after reasoning before firing calls")
    p.add_argument("--log", action="store_true", help="show Precog's internal log on stderr")
    p.add_argument("--compare", action="store_true",
                   help="also run the same scenario WITHOUT Precog and compare total wall-clock")
    return p.parse_args(argv)


def _load_scenario(args):
    """Resolve (reasoning, calls, rules) from either flags or a scenario file."""
    reasoning = args.reasoning
    rules = []  # type: list
    calls = []  # type: list  # list of (name, arguments)

    if args.scenario:
        with open(args.scenario) as fh:
            data = json.load(fh)
        reasoning = data.get("reasoning", reasoning)
        for c in data.get("calls", []):
            calls.append((c["name"], c.get("arguments") or {}))
        for r in data.get("rules", []):
            import re
            flags = 0
            for ch in r.get("flags", ""):
                flags |= {"i": re.I, "m": re.M, "s": re.S, "x": re.X}.get(ch, 0)
            rules.append(IntentRule(re.compile(r["pattern"], flags), r["tool"],
                                    arg_map=r.get("args") or {},
                                    static_args=r.get("static_args") or {},
                                    confidence=float(r.get("confidence", 0.9))))

    if args.rules:
        rules.extend(load_intent_rules(args.rules))

    for spec in args.call:
        name = spec[0]
        arguments = {}
        if len(spec) > 1:
            arguments = json.loads(spec[1])
        calls.append((name, arguments))

    return reasoning, calls, rules


def main(argv=None):
    args = _parse_args(argv)

    # The mock server reads PRECOG_DEMO_LATENCY at spawn time.
    os.environ["PRECOG_DEMO_LATENCY"] = str(args.latency)

    try:
        reasoning, calls, rules = _load_scenario(args)
    except (OSError, ValueError, KeyError, ConfigError) as exc:
        print("error loading scenario/rules: %s" % exc, file=sys.stderr)
        return 2

    if not calls:
        print("nothing to do: provide at least one --call (or --scenario)", file=sys.stderr)
        return 2

    def log(m):
        if args.log:
            sys.stderr.write("[precog] " + m + "\n")

    handle = connect_precog(on_log=log, install_demo_rules=False)
    # Install the user's rules into the live oracle.
    if rules and handle.proxy.cot is not None:
        for r in rules:
            handle.proxy.cot.add_rule(r)

    driver = handle.driver
    try:
        driver.initialize()
        tools = driver.list_tools()["result"]["tools"]
        known = {t["name"] for t in tools}
        readonly = {t["name"] for t in tools
                    if (t.get("annotations") or {}).get("readOnlyHint") is True}
        time.sleep(0.05)  # let the registry populate

        print("=" * 64)
        print("Precog prompt test")
        print("=" * 64)
        print("server latency : %.0f ms/call    think: %.0f ms" %
              (args.latency * 1000, args.think * 1000))
        print("rules loaded   : %d" % len(rules))
        print("reasoning      : %s" % (reasoning or "(none)"))
        print()

        # Warn about calls the harness can't meaningfully serve.
        for name, _ in calls:
            if name not in known:
                print("  ! '%s' is not a tool on the mock server (known: %s)"
                      % (name, ", ".join(sorted(known))))

        # Wall-clock starts when the model begins thinking. With Precog, the
        # prefetch overlaps the think; the timer covers think + all calls.
        precog_start = time.monotonic()
        if reasoning:
            driver.send_reasoning(reasoning)
        if args.think > 0:
            time.sleep(args.think)

        print("call results (server latency is %.0f ms; a warm hit returns ~instantly):"
              % (args.latency * 1000))
        for name, arguments in calls:
            emit = time.monotonic()
            resp = driver.call_tool(name, arguments)
            dt = (time.monotonic() - emit) * 1000
            verdict = "WARM HIT" if dt < args.latency * 1000 * 0.5 else "miss"
            note = ""
            if name not in readonly and name in known:
                note = "  (not read-only — never speculated)"
            text = ""
            if "result" in resp:
                content = resp["result"].get("content") or [{}]
                text = content[0].get("text", "")
            print("  %-9s %-14s %6.0f ms  %s%s"
                  % (verdict, name, dt, _short(arguments), note))
            if text:
                print("            -> %s" % text)
        precog_total = time.monotonic() - precog_start

        m = handle.proxy.metrics.as_dict()
        print()
        print("metrics: hit_rate=%.0f%%  warm=%d late=%d miss=%d  fired=%d  saved=%.0fms"
              % (m["hit_rate"] * 100, m["warm_hits"], m["late_hits"], m["misses"],
                 m["speculations_fired"], m["saved_seconds"] * 1000))
        if m["by_source"]:
            print("predicted by: %s" % m["by_source"])
    finally:
        handle.close()

    if not args.compare:
        return 0

    # -- Baseline: same scenario, same think time, but NO Precog. ----------
    baseline_total = _run_baseline(reasoning, calls, args.think)

    print()
    print("=" * 64)
    print("Total wall-clock — same %d-call scenario, same %.0f ms think" %
          (len(calls), args.think * 1000))
    print("=" * 64)
    print("  WITHOUT precog (serial) : %7.0f ms   think, then each call waits"
          % (baseline_total * 1000))
    print("  WITH precog             : %7.0f ms   calls prefetched during the think"
          % (precog_total * 1000))
    if precog_total > 0:
        print()
        print("  speedup                 : %.2fx end-to-end" % (baseline_total / precog_total))
        print("  time saved              : %7.0f ms" % ((baseline_total - precog_total) * 1000))
    return 0


def _run_baseline(reasoning, calls, think):
    """Run the same calls straight against the server with no Precog.

    The think time is still paid (the model reasons either way), but here the
    server sits idle through it and every call pays full latency in series.
    """
    driver, proc = connect_baseline()
    try:
        driver.initialize()
        driver.list_tools()
        start = time.monotonic()
        # The reasoning has nowhere to go without Precog; the think is just dead
        # time during which the downstream API is idle.
        if think > 0:
            time.sleep(think)
        for name, arguments in calls:
            driver.call_tool(name, arguments)
        return time.monotonic() - start
    finally:
        close_baseline(proc)


def _short(args):
    s = json.dumps(args, separators=(",", ":"))
    return s if len(s) <= 30 else s[:27] + "..."


if __name__ == "__main__":
    sys.exit(main())
