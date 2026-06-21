# Precog — Demo Runbook

Everything you need to run the demo tomorrow morning. Follow it top to bottom.

---

## 0. Preflight (do this first, ~15 seconds)

```bash
cd /local/home/danilief/precog
python3 web/preflight.py
```

You want **ALL 6 CHECKS PASSED**. If anything fails, it tells you exactly what.
(If "UI is built" fails, see *Rebuilding the UI* at the bottom.)

Then start the server:

```bash
python3 web/server.py --port 8765
# open http://127.0.0.1:8765
```

Liveness check any time: `curl -s http://127.0.0.1:8765/api/health`

---

## 1. The 60-second story

> "Agents are slow because they wait — the model thinks for seconds, fires a
> tool call, blocks on the network, repeats. Precog is a drop-in MCP proxy that
> predicts the agent's next calls *while it's still thinking*, fires the safe
> ones in parallel, and serves them warm the instant the model asks. Branch
> prediction, for agents."

Then: type a prompt, hit run, **watch the live race**.

---

## 2. Suggested prompts (each shows off something)

**A. The headline race (big speedup, parallel prefetch):**
```
Investigate a refund dispute for Alice and Bob. Pull their orders and profiles,
check ORD-1001 payment and shipping, review system health and logs, then email
Alice a resolution.
```
~14 calls, typically **3–4× faster**. Point out: the cyan sparks firing during
the "think", Precog finishing while the baseline is still on call #3.

**B. The safety gate (the trust moment):**
```
Issue a refund for ORD-1001 for Alice, cancel order 5512, and email her.
```
Point out the per-call list: `issue_refund`, `cancel_order`, `send_email` are
marked **"side-effecting — never speculated by design"** and ran fresh. Precog
*cannot* fire a money-moving call on a guess.

**C. Cross-run learning (run the SAME prompt twice):**
```
Check system status, metrics, alerts, and API logs.
```
Run it once → "Run #1 · predicted by chain-of-thought oracle". Run the exact
same prompt again → "Run #2 · predicted by chain-of-thought oracle + Markov
sequence model — the Markov model has learned this chain across runs." It's
genuinely learning across runs, not scripted.

> To reset learning between practice rounds:
> `curl -s -X POST http://127.0.0.1:8765/api/reset-learning`

**D. "Throw anything at it" (robustness flex):**
Invite someone to type any prompt. The planner always produces a believable
multi-step plan — support, commerce, ops, docs — and never breaks.

---

## 3. What's real vs. simulated (say this if asked — it's the honest line)

- **Real:** the MCP proxy, JSON-RPC over a real server subprocess, the
  speculation engine, parallel prefetch, the safety gate, cross-run Markov
  learning, and **every millisecond on screen** (measured live).
- **Simulated here:** per-tool latency (a fixed delay standing in for a real
  API) and the model's "think" time. With no live LLM in this environment, your
  prompt is turned into tool calls by a deterministic planner standing in for
  the model. The with/without timings are **not** fabricated; real-world hit
  rates depend on your model and tools.

This is also printed in the UI footer, so you're covered.

---

## 4. If something goes wrong

- **Page won't load:** is the server running? `curl -s http://127.0.0.1:8765/api/health`.
  Restart: `python3 web/server.py --port 8765`.
- **A run seems stuck:** every run is capped (~30s hard ceiling) and the server
  serializes runs; just wait, then re-run. The race auto-completes or errors
  cleanly — it won't hang the page.
- **Numbers look small:** raise the contrast by editing the constants at the top
  of `web/frontend/src/App.jsx` (`LATENCY`, `THINK`) and rebuilding, OR just use
  prompt A which has the most calls. Higher latency → bigger speedup.
- **Total fallback (no browser):** run the terminal demo, which is fully
  self-contained: `python3 demo/run_demo.py`.

---

## 5. Knobs (optional)

In `web/frontend/src/App.jsx`:
- `LATENCY` (default 0.4s) — simulated per-tool latency. Higher = more dramatic.
- `THINK` (default 1.0s) — model think time. The race is most watchable at 0.8–1.2s.

After editing frontend source you must rebuild (below).

---

## Rebuilding the UI (only if you change React source)

```bash
cd web/frontend
nvm use 16          # this host needs Node 16; modern Node is fine elsewhere
npm install         # first time only
npm run build       # emits to ../static
```

---

## One-liner cheat sheet

```bash
python3 web/preflight.py                                   # verify everything
python3 web/server.py --port 8765                          # run the demo UI
curl -s http://127.0.0.1:8765/api/health                   # is it up?
curl -s -X POST http://127.0.0.1:8765/api/reset-learning   # reset learning
python3 demo/run_demo.py                                   # terminal fallback
python3 -m unittest discover -s tests                      # 83 engine tests
```
