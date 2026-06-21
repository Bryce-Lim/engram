# Precog — speculative execution for AI agents

**A performance layer for the Model Context Protocol (MCP). Your tools answer
before the model finishes asking.**

AI agents are slow because they *wait* — the model reasons for seconds, emits a
tool call, fires it, blocks on the network round-trip, then repeats. The
downstream API sits idle during the "thinking," and again between every step.

Precog is a drop-in proxy that predicts an agent's next tool calls **while it's
still thinking**, fires the side-effect-free ones in parallel, and serves the
results the instant the model actually asks. Branch prediction, for agents.

```
precog wrap ./your-mcp-server     →  same agent, lower latency, zero code changes
```

```
        agent / host  ⇄ stdio JSON-RPC ⇄  PRECOG  ⇄ stdio JSON-RPC ⇄  your MCP server
                                          │
                                          ├─ observes tools/list  → learns readOnlyHint
                                          ├─ watches reasoning     → prefetches during the think
                                          ├─ learns tool→tool       → prewarms the next call
                                          └─ serves warm results    → ~0 ms on a hit
```

---

## The insight

CPUs solved this in the 1990s. A processor doesn't wait to learn whether a
branch is taken — it predicts, executes speculatively, and commits or squashes.
The same pattern maps directly onto agent tool calls:

| CPU branch prediction          | Precog, for agents                  |
| ------------------------------ | ----------------------------------- |
| Predict which branch is taken  | Predict the next tool call          |
| Speculatively execute down it  | Fire the API now, in parallel       |
| Commit the result if right     | Serve the warm result on a hit (~0 ms) |
| Squash & discard if wrong      | Drop the speculation on a miss      |

---

## How it predicts — four signals, layered from "always safe" to "genuinely novel"

1. **Eager dispatch** (zero guessing) — when the host signals (via the hint
   channel) that a fully-formed call is imminent, begin executing it
   immediately instead of after the request is routed; several such intents
   overlap. No guessing — the floor of the system.
   See [`precog/predictors/eager.py`](precog/predictors/eager.py).
2. **Chain-of-thought oracle** (the novel part) — watch the model's reasoning
   stream. It narrates intent — *"I'll look up their recent orders"* — seconds
   before the call. Precog parses that and prefetches during the think, even
   capturing arguments straight out of the narrated intent.
   See [`precog/predictors/cot_oracle.py`](precog/predictors/cot_oracle.py).
3. **Markov sequence model** — learn tool→tool transitions from traffic. *"After
   `search`, `fetch` follows 80% of the time."* Gets smarter with every run.
   See [`precog/predictors/markov.py`](precog/predictors/markov.py).
4. **Safety by protocol** (correctness gate) — speculate **only** on tools MCP
   marks `readOnlyHint: true`. Never a `send_email` or `charge_card`.
   Side-effect-free by construction, fail-closed when unsure.
   See [`precog/safety.py`](precog/safety.py).

---

## Quick start

No dependencies — pure Python 3 standard library.

```bash
# Run the split-screen demo: race the same agent with and without Precog.
python3 demo/run_demo.py

# Exaggerate the I/O cost to see the speedup grow toward the parallel ceiling.
PRECOG_DEMO_LATENCY=1.0 python3 demo/run_demo.py

# Run the test suite (83 tests).
python3 -m unittest discover -s tests
#   or:  ./run_tests.sh
```

### Web UI — race a prompt in the browser

A small web app lets you type what an agent should do and watch the same plan
race with and without Precog, with the timings measured live:

```bash
./web/run_web.sh           # builds the frontend (first run) and serves on :8765
# open http://127.0.0.1:8765
```

React + Vite + Tailwind frontend (Inter Tight throughout), served by a stdlib
Python backend (`web/server.py`) whose `POST /api/compare` runs the **real**
Precog-vs-baseline comparison against a live MCP server subprocess. Because
there is no LLM in the loop, your prompt is turned into tool calls by a
deterministic planner (`web/planner.py`) standing in for the model — but the
with/without timings are genuinely measured, not fabricated.

> Note: this host's Node runs the build on **Node 16** (`nvm use 16`); the
> system glibc predates Node 18+. `run_web.sh` selects it automatically if nvm
> is present.

### Wrap a real MCP server

```bash
# Point your agent at `precog` instead of the server. That's the whole change.
bin/precog wrap -- ./your-mcp-server --its --args

# Unlock argument-capturing chain-of-thought prediction with a rules file:
bin/precog wrap --rules demo/rules.example.json -- ./your-mcp-server
```

Flags: `--no-cot`, `--no-markov`, `--no-eager` (toggle signals), `--timeout`
(downstream call budget), `--rules FILE`, `--quiet`. Logs go to **stderr**;
stdout is reserved for the MCP byte stream.

---

## What's measured by the demo

Everything the demo prints is measured at runtime against a real MCP server
subprocess — no number is hardcoded. It has four parts:

1. **Race 1 — parallel prefetch.** With the default 400 ms tool latency and
   500 ms think time, a three-call plan that a serial agent runs in ~1.7 s
   returns in ~0.5 s through Precog (**~3×**, 100% hit rate), because the three
   round-trips collapse into one parallel prefetch during the single think. The
   speedup grows toward the parallel ceiling as I/O latency dominates the think.
   *This race uses curated chain-of-thought intent rules* (`--rules` /
   `install_demo_rules`) whose regexes match the demo prompt and capture its
   exact arguments. Without rules, the auto-keyword oracle proposes only
   empty-argument calls, so an argument-bearing scenario warms 0 — argument
   capture is what makes the headline number, and it requires either rules or a
   host that forwards reasoning the keyword layer can ground.
2. **Race 2 — Markov learning.** An identical argument-free chain goes from 0/3
   warm on the first run to 3/3 on the third as transitions are learned. *The
   demo lowers `MarkovModel.min_observations` from the shipped default of 2 to
   1* so learning surfaces within three runs; the exact 0→2→3 trajectory is a
   property of these tuned settings, not an intrinsic guarantee.
3. **Race 3 — squash on miss.** The model narrates one intent but calls a
   different tool; the wrong speculation is squashed and the agent still gets
   the correct answer. A miss costs a wasted read-only fetch, never a wrong one.
4. **Safety.** A `readOnlyHint: false` tool (`send_email`) is **never**
   speculated.

> The demo's speedup is illustrative of the mechanism on a simulated server;
> real numbers depend on your model's think time and your tools' latency.
> Measure against your own server before quoting figures.

---

## How it stays correct

- **Safety gate.** A tool call is only ever speculated if its tool is known to
  be `readOnlyHint: true`. Unknown or unannotated tools fail closed. The gate is
  re-checked at the instant of dispatch, not just when the prediction is made,
  so a tool that flips to non-read-only mid-session is never fired.
- **Squash on miss.** A wrong guess is discarded; the agent's real call falls
  through to a normal downstream execution. A miss costs a wasted (harmless)
  read, never a wrong answer.
- **Dedup.** A given `(tool, arguments)` signature is fired downstream at most
  once, no matter how many predictors propose it or how a late real call races
  it. A real call that arrives mid-flight *attaches* to the running speculation
  (a "late hit") instead of issuing a duplicate. An in-flight speculation is
  never evicted from the cache, so this guarantee holds even under load.
- **Freshness & error handling.** A cached result older than a TTL is treated
  as stale and re-fetched. Only *deterministic* errors (e.g. `INVALID_PARAMS`)
  are served from a speculation; transient/internal errors fall back to a fresh
  call that may succeed.
- **Bounded resources.** Pending-response slots are reclaimed on timeout, the
  speculation cache and Markov tables are capped, and the downstream reader
  caps any single line — so nothing grows without limit over a long session.
- **Transparent passthrough.** `initialize`, `resources/*`, `prompts/*`, `ping`,
  notifications, and any unknown method are forwarded verbatim. Precog is
  invisible except for being faster.
- **Graceful teardown.** On agent disconnect, in-flight calls are drained and
  their responses relayed before the downstream server is closed.

---

## Architecture

| Module | Responsibility |
| ------ | -------------- |
| [`precog/jsonrpc.py`](precog/jsonrpc.py) | JSON-RPC 2.0 + MCP stdio framing (newline-delimited JSON) |
| [`precog/downstream.py`](precog/downstream.py) | Subprocess MCP client with a demultiplexing reader thread |
| [`precog/safety.py`](precog/safety.py) | Tool registry + the `readOnlyHint` speculation gate |
| [`precog/cache.py`](precog/cache.py) | Speculation signatures, in-flight tracking, warm/late/miss states |
| [`precog/predictors/`](precog/predictors/) | The four signals (eager, CoT oracle, Markov) |
| [`precog/speculator.py`](precog/speculator.py) | Engine: predict → gate → reserve → dispatch → resolve |
| [`precog/metrics.py`](precog/metrics.py) | Hit rate, precision, latency saved (all runtime-measured) |
| [`precog/proxy.py`](precog/proxy.py) | The proxy: wires upstream I/O, downstream, and speculation |
| [`precog/cli.py`](precog/cli.py) | The `precog wrap` command |
| [`demo/`](demo/) | Mock MCP server, scripted agent, and the split-screen race |

### The hint channel (optional, additive)

The pure-MCP signals (Markov sequence learning and protocol-safe concurrency)
require **zero** changes to the agent. Eager dispatch and the chain-of-thought
oracle need information that lives in the host — the model's reasoning and its
imminent tool intent — not on the MCP wire. A host MAY forward that via two
custom notifications that Precog consumes and servers ignore:

- `notifications/precog/reasoning` — `{"text": "...streamed reasoning..."}`
- `notifications/precog/tool_intent` — `{"name": "...", "arguments": {...}}`

Send nothing and you still get Markov + safe concurrency for free; forward the
thinking stream and you additionally get prediction *during the think*.

---

## Status

A working reference implementation with a measured demo and 83 tests. The
prediction signals and safety gate are real; the demo's latencies are simulated
so the mechanism is reproducible offline. Bring your own MCP server and model to
measure end-to-end gains on real traffic.
