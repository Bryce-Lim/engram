# Running Engram on your laptop

This archive contains the full Engram project. The web UI's frontend is
**already built** (in `web/static/`), so you can run everything with just
Python 3 — no Node, no build step.

## 1. Unpack

```bash
tar -xzf engram.tar.gz
cd engram
```

## 2. Run the test suite (pure Python stdlib)

```bash
python3 -m unittest discover -s tests       # 83 tests
# or:  ./run_tests.sh
```

## 3. Run the CLI demo (terminal split-screen race)

```bash
python3 demo/run_demo.py
ENGRAM_DEMO_LATENCY=1.0 python3 demo/run_demo.py   # exaggerate the I/O cost
```

## 4. Run the web UI (the live race in a browser)

The build output is bundled, so just start the server:

```bash
python3 web/server.py --port 8765
# open http://127.0.0.1:8765
```

## Rebuilding the frontend (only if you change the React source)

`node_modules` was excluded to keep the archive small. To rebuild the UI:

```bash
cd web/frontend
npm install
npm run build      # emits to ../static
```

> Node note: this was authored against **Node 16** because the remote host's
> glibc was too old for Node 18/20. On your laptop any modern Node (18/20/22)
> should build it fine — the pinned versions in `package.json` (Vite 4, React 18,
> Tailwind 3) are compatible with current Node. If `npm install` complains, run
> `npm install` without the lockfile or bump Vite to 5.

## Wrap a real MCP server (the actual product)

```bash
bin/engram wrap -- ./your-mcp-server --args
bin/engram wrap --rules demo/rules.example.json -- ./your-mcp-server
```

## Layout

- `engram/`        — the proxy engine (jsonrpc, downstream, speculator, predictors, safety)
- `demo/`          — mock MCP server, scripted agent, CLI demo, prompt tester
- `tests/`         — 83 unittest tests
- `web/`           — Python server + planner + comparison/stream + React frontend
- `web/static/`    — the prebuilt UI (served by web/server.py)
- `README.md`      — full project documentation
