#!/usr/bin/env python3
"""Precog demo preflight — one command to confirm everything works.

Run this the morning of the demo. It exercises the whole stack end-to-end and
prints a clear PASS/FAIL per check, exiting non-zero if anything is wrong, so
there are no surprises in front of the room.

    python3 web/preflight.py

Checks:
  1. Core engine imports.
  2. Planner survives a battery of normal + adversarial prompts (never empty,
     never raises).
  3. The built UI exists (web/static/index.html).
  4. A real streamed comparison runs and produces a speedup > 1.
  5. The safety gate holds: no side-effecting tool is ever served warm.
  6. Cross-run learning: run 2 of a scenario engages the Markov model.
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"

results = []


def check(name, fn):
    sys.stdout.write("  … %s\r" % name)
    sys.stdout.flush()
    try:
        detail = fn()
        results.append((True, name, detail))
        print("  %s✓%s %s   %s%s%s" % (GREEN, RESET, name, DIM, detail or "", RESET))
    except Exception as exc:
        results.append((False, name, str(exc)))
        print("  %s✗%s %s   %s%s" % (RED, RESET, name, RED, exc))


def c1_imports():
    import precog.proxy  # noqa
    import precog.speculator  # noqa
    from web import compare, planner, stream, learning  # noqa
    return "engine + web modules import"


def c2_planner():
    from web.planner import plan, READ_ONLY_TOOLS
    prompts = [
        "Investigate a refund dispute for Alice and Bob, check ORD-1001 payment "
        "and shipping, review system health, then email Alice",
        "is SKU-7781 in stock?", "why is the api slow", "how do refunds work",
        "", "???", "aaaa", "πππ 日本語", "help", "do everything for everyone",
        "cancel order 5512 for carol and open a ticket",
    ]
    for p in prompts:
        pl = plan(p)
        assert pl["calls"], "empty plan for %r" % p
        assert pl["reasoning"], "empty reasoning for %r" % p
    return "%d prompts → all non-empty, no crash" % len(prompts)


def c3_ui_built():
    idx = os.path.join(_HERE, "static", "index.html")
    assert os.path.isfile(idx), "web/static/index.html missing — run the build"
    assets = os.path.join(_HERE, "static", "assets")
    js = [f for f in os.listdir(assets)] if os.path.isdir(assets) else []
    assert any(f.endswith(".js") for f in js), "no JS bundle in web/static/assets"
    return "static UI present (%d assets)" % len(js)


def c4_stream():
    from web.planner import plan
    from web.stream import stream_comparison
    pl = plan("refund for alice and bob, check ORD-1001, review system health, email alice")
    done = None
    for ev in stream_comparison(pl, latency=0.15, think=0.4):
        if ev["ev"] == "done":
            done = ev["summary"]
    assert done, "stream produced no done event"
    assert done["speedup"] > 1.0, "speedup not > 1 (%.2f)" % done["speedup"]
    return "speedup %.2fx, %d/%d hits" % (done["speedup"], done["hits"], done["num_calls"])


def c5_safety():
    from web.planner import plan
    from web.stream import stream_comparison
    # A prompt that explicitly asks for side effects.
    pl = plan("issue a refund for ORD-1001 for alice, cancel order 5512, and email her")
    done = None
    for ev in stream_comparison(pl, latency=0.12, think=0.3):
        if ev["ev"] == "done":
            done = ev["summary"]
    violations = [c for c in done["calls"] if c["outcome"] == "hit" and not c["read_only"]]
    assert not violations, "side-effecting tool served warm: %s" % [v["name"] for v in violations]
    writes = [c["name"] for c in done["calls"] if not c["read_only"]]
    return "%d side-effecting calls, 0 speculated" % len(writes)


def c6_learning():
    from web import learning
    from web.planner import plan
    from web.stream import stream_comparison
    learning.reset()
    pl = plan("check system status, metrics, alerts and api logs")
    runs = []
    for _ in range(2):
        done = None
        for ev in stream_comparison(pl, latency=0.12, think=0.3):
            if ev["ev"] == "done":
                done = ev["summary"]
        runs.append(done)
    assert runs[0]["run"] == 1 and runs[1]["run"] == 2, "run index not advancing"
    assert "markov" in runs[1].get("by_source", {}), \
        "Markov did not engage on run 2 (sources: %s)" % runs[1].get("by_source")
    learning.reset()
    return "run1 cold-start → run2 engages Markov"


def main():
    print("\nPrecog preflight — verifying the full demo stack\n")
    check("core + web imports", c1_imports)
    check("planner robustness", c2_planner)
    check("UI is built", c3_ui_built)
    check("live comparison stream", c4_stream)
    check("safety gate holds", c5_safety)
    check("cross-run learning", c6_learning)
    passed = sum(1 for ok, _, _ in results if ok)
    total = len(results)
    print()
    if passed == total:
        print("  %sALL %d CHECKS PASSED%s — ready to demo.\n" % (GREEN, total, RESET))
        return 0
    print("  %s%d/%d PASSED — fix the ✗ above before demoing.%s\n"
          % (RED, passed, total, RESET))
    return 1


if __name__ == "__main__":
    sys.exit(main())
