# Engram — start here

**Speculative execution for AI agents.** Engram is a drop-in proxy for the Model
Context Protocol (MCP): it predicts an agent's next tool calls *while the model
is still thinking*, runs the safe ones in parallel, and serves the results the
instant the model asks. Branch prediction, for agents.

This package includes a **prebuilt web demo**, so you can run it with just
**Python 3 — no Node, no build step, no internet** (other than loading a web
font).

---

## Run the demo (30 seconds)

```bash
# unpack, then from inside the engram/ folder:
python3 web/server.py --port 8765
```

Open **http://127.0.0.1:8765** in your browser.

Type what an agent should do (or click an example), hit **Run the race**, and
watch the same plan run twice — once plain, once through Engram — as two
liquid-glass bars fill in real time. Everything on screen is measured live.

Try this prompt for the most dramatic result:

> Investigate a refund dispute for Alice and Bob. Pull their orders and
> profiles, check ORD-1001 payment and shipping, review system health, then
> email Alice a resolution.

---

## What you're looking at

- **With Engram** (orange) fills almost instantly — the calls were prefetched
  during the model's "think".
- **Without Engram** (white) fills one call at a time, each waiting on the
  network.
- **Safety:** anything side-effecting (send email, issue refund) is *never*
  speculated — look for "side-effecting — never speculated by design" in the
  per-call list.
- **It learns:** run the same prompt twice and Engram recognizes the pattern.

### Honest disclosure
The proxy, the MCP protocol over a real server subprocess, the speculation
engine, and **every millisecond shown are real**. What's simulated for the demo:
per-tool latency (a fixed delay standing in for a real API) and the model's
think time — and, since there's no live LLM bundled, your prompt is turned into
tool calls by a small deterministic planner standing in for the model. The
with/without timings are not fabricated; real-world speedups depend on your
model and tools.

---

## Other things you can run

```bash
python3 demo/run_demo.py                  # terminal version of the race
python3 web/preflight.py                  # health-check the whole stack
python3 -m unittest discover -s tests     # 83 engine tests (pure stdlib)
```

Wrap a real MCP server (the actual product):

```bash
bin/engram wrap -- ./your-mcp-server --args
```

---

## If you want to change the UI

The web frontend is React + Vite (already built into `web/static/`). To rebuild
after editing `web/frontend/src`:

```bash
cd web/frontend
npm install
npm run build      # outputs to ../static
```

(Any modern Node works for the build; `node_modules` was left out of this
package to keep it small.)

---

## What's in here

- `web/`        — the demo: Python server + the prebuilt UI in `web/static/`
- `engram/`     — the proxy engine (the actual product)
- `demo/`       — mock MCP server, scripted agent, terminal demo
- `tests/`      — 83 tests
- `README.md`         — full project documentation
- `DEMO_RUNBOOK.md`   — a presenter's guide (talking points, prompts, fallbacks)
