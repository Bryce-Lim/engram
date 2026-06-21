"""End-to-end tests: a real agent driver, the real proxy, a real server subprocess.

These exercise the full byte path (newline-delimited JSON-RPC over OS pipes and
a subprocess) rather than mocks, so they catch wiring/concurrency bugs the unit
tests cannot. Latency is kept small to keep the suite fast.
"""

import os
import re
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from demo.harness import (connect_baseline, close_baseline, connect_engram,  # noqa: E402
                          demo_intent_rules)


# Keep the simulated server fast for tests.
os.environ.setdefault("ENGRAM_DEMO_LATENCY", "0.15")


class TestProxyPassthrough(unittest.TestCase):
    def test_initialize_and_tools_list_passthrough(self):
        handle = connect_engram(install_demo_rules=True)
        try:
            init = handle.driver.initialize()
            self.assertIn("result", init)
            self.assertEqual(init["result"]["serverInfo"]["name"], "engram-demo-server")
            tools = handle.driver.list_tools()
            names = {t["name"] for t in tools["result"]["tools"]}
            self.assertIn("get_orders", names)
            self.assertIn("send_email", names)
        finally:
            handle.close()

    def test_tool_call_correct_result(self):
        handle = connect_engram(install_demo_rules=True)
        try:
            handle.driver.initialize()
            handle.driver.list_tools()
            resp = handle.driver.call_tool("get_orders", {"customer": "zoe"})
            text = resp["result"]["content"][0]["text"]
            self.assertIn("zoe", text)
        finally:
            handle.close()


class TestSpeculativeHit(unittest.TestCase):
    def test_cot_oracle_prewarms_call(self):
        handle = connect_engram(install_demo_rules=True)
        try:
            handle.driver.initialize()
            handle.driver.list_tools()
            time.sleep(0.05)  # let tools/list populate the registry
            # Narrate intent; oracle should prefetch get_orders(customer=alice).
            handle.driver.send_reasoning("I'll pull the orders for alice now.")
            time.sleep(0.3)   # > server latency, so the speculation completes
            resp = handle.driver.call_tool("get_orders", {"customer": "alice"})
            self.assertIn("alice", resp["result"]["content"][0]["text"])
            metrics = handle.proxy.metrics.as_dict()
            self.assertGreaterEqual(metrics["warm_hits"] + metrics["late_hits"], 1)
        finally:
            handle.close()

    def test_parallel_plan_yields_speedup(self):
        latency = float(os.environ["ENGRAM_DEMO_LATENCY"])
        plan = ("Plan: pull the orders for alice, get the profile for alice, "
                "and fetch the invoice for ORD-1001.")
        scenario = [("get_orders", {"customer": "alice"}),
                    ("get_customer", {"customer": "alice"}),
                    ("fetch_invoice", {"order_id": "ORD-1001"})]

        handle = connect_engram(install_demo_rules=True)
        try:
            handle.driver.initialize()
            handle.driver.list_tools()
            time.sleep(0.05)
            handle.driver.send_reasoning(plan)
            time.sleep(latency + 0.1)  # all three prefetch in parallel
            start = time.monotonic()
            for tool, args in scenario:
                handle.driver.call_tool(tool, args)
            served = time.monotonic() - start
            # All three were prefetched; serving them should cost far less than
            # executing even one fresh call would.
            self.assertLess(served, latency)
            m = handle.proxy.metrics.as_dict()
            self.assertEqual(m["warm_hits"] + m["late_hits"], 3)
        finally:
            handle.close()


class TestSafetyEndToEnd(unittest.TestCase):
    def test_send_email_never_speculated(self):
        handle = connect_engram(install_demo_rules=True)
        try:
            handle.driver.initialize()
            handle.driver.list_tools()
            time.sleep(0.05)
            # Even if reasoning mentions email, the non-read-only tool must not
            # be speculated.
            handle.driver.send_reasoning("I'll send an email to the customer.")
            time.sleep(0.2)
            m = handle.proxy.metrics.as_dict()
            self.assertEqual(m["by_source"].get("__send_email__", 0), 0)
            # send_email must not appear among fired speculations: assert by
            # checking the cache never held it.
            sigs = list(handle.proxy.cache.snapshot().keys())
            self.assertFalse(any(s.startswith("send_email\x00") for s in sigs))
            # And calling it still works (passthrough), proving it's reachable
            # but simply never pre-fired.
            resp = handle.driver.call_tool("send_email", {"to": "a@b.c"})
            self.assertIn("email sent", resp["result"]["content"][0]["text"])
        finally:
            handle.close()


class TestMarkovLearningEndToEnd(unittest.TestCase):
    def test_hit_rate_climbs_across_runs(self):
        handle = connect_engram(install_demo_rules=False, markov_min_observations=1)
        chain = ["get_status", "get_metrics", "get_alerts"]
        try:
            handle.driver.initialize()
            handle.driver.list_tools()
            time.sleep(0.05)
            hits_per_run = []
            for _ in range(3):
                before = handle.proxy.metrics.as_dict()
                for tool in chain:
                    handle.driver.call_tool(tool, {})
                    time.sleep(0.2)  # allow successor speculation to settle
                after = handle.proxy.metrics.as_dict()
                hits = ((after["warm_hits"] + after["late_hits"])
                        - (before["warm_hits"] + before["late_hits"]))
                hits_per_run.append(hits)
            # Run 1 cannot predict anything; later runs must do strictly better.
            self.assertEqual(hits_per_run[0], 0)
            self.assertGreater(hits_per_run[-1], hits_per_run[0])
        finally:
            handle.close()


class TestCliEndToEnd(unittest.TestCase):
    """Drive the *shipped* ``bin/engram wrap`` entry point as a subprocess."""

    def _spawn_cli(self, extra_args):
        import subprocess
        cmd = [sys.executable, os.path.join(_ROOT, "bin", "engram"), "wrap", "--quiet"]
        cmd += extra_args
        cmd += ["--", sys.executable, os.path.join(_ROOT, "demo", "mock_server.py")]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def _run(self, extra_args, reasoning, call):
        from engram import jsonrpc
        latency = float(os.environ["ENGRAM_DEMO_LATENCY"])
        proc = self._spawn_cli(extra_args)
        responses = {}

        def send(m):
            proc.stdin.write(jsonrpc.encode(m))
            proc.stdin.flush()

        import threading
        def reader():
            for raw in iter(proc.stdout.readline, b""):
                if raw.strip():
                    m = jsonrpc.decode(raw)
                    if "id" in m:
                        responses[m["id"]] = m
        rt = threading.Thread(target=reader)
        rt.daemon = True
        rt.start()
        try:
            send(jsonrpc.make_request(1, "initialize",
                                      {"protocolVersion": "2024-11-05", "capabilities": {}}))
            send(jsonrpc.make_request(2, "tools/list"))
            # Wait for tools/list to come back (registry populated) like a real client.
            deadline = time.monotonic() + 5
            while 2 not in responses and time.monotonic() < deadline:
                time.sleep(0.01)
            send(jsonrpc.make_notification("notifications/engram/reasoning",
                                           {"text": reasoning}))
            time.sleep(latency + 0.1)  # think; prefetch overlaps and completes
            emit = time.monotonic()
            send(jsonrpc.make_request(3, "tools/call", call))
            while 3 not in responses and time.monotonic() - emit < 5:
                time.sleep(0.01)
            served = time.monotonic() - emit
            return responses.get(3), served, latency
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass

    def test_keyword_warm_hit_via_cli(self):
        resp, served, latency = self._run(
            [], "let me check the current status",
            {"name": "get_status", "arguments": {}})
        self.assertIsNotNone(resp)
        self.assertIn("status", resp["result"]["content"][0]["text"])
        # Warm hit: served far faster than a fresh downstream call would be.
        self.assertLess(served, latency / 2)

    def test_argument_capture_warm_hit_via_cli_with_rules(self):
        rules_path = os.path.join(_ROOT, "demo", "rules.example.json")
        resp, served, latency = self._run(
            ["--rules", rules_path], "I'll pull the orders for alice",
            {"name": "get_orders", "arguments": {"customer": "alice"}})
        self.assertIsNotNone(resp)
        self.assertIn("alice", resp["result"]["content"][0]["text"])
        self.assertLess(served, latency / 2)


class TestProtocolEdgeCases(unittest.TestCase):
    def test_batch_request_gets_error_not_silence(self):
        from engram import jsonrpc
        handle = connect_engram(install_demo_rules=False)
        try:
            handle.driver.initialize()
            # Send a raw top-level array (a batch) and expect a loud error.
            handle.driver._send([
                jsonrpc.make_request(99, "tools/list")])  # a list, not an object
            # Read the next response off the agent's stream directly.
            deadline = time.monotonic() + 3
            got = None
            # The driver's reader only tracks id-bearing responses; a batch
            # error has id null, so poll the pending map for any null-id entry.
            while time.monotonic() < deadline and got is None:
                with handle.driver._lock:
                    got = handle.driver._pending.get(None)
                time.sleep(0.02)
            self.assertIsNotNone(got)
            self.assertIn("error", got)
        finally:
            handle.close()

    def test_id_null_request_rejected(self):
        from engram import jsonrpc
        handle = connect_engram(install_demo_rules=False)
        try:
            handle.driver.initialize()
            handle.driver._send({"jsonrpc": "2.0", "id": None, "method": "tools/list"})
            deadline = time.monotonic() + 3
            got = None
            while time.monotonic() < deadline and got is None:
                with handle.driver._lock:
                    got = handle.driver._pending.get(None)
                time.sleep(0.02)
            self.assertIsNotNone(got)
            self.assertEqual(got["error"]["code"], jsonrpc.INVALID_REQUEST)
        finally:
            handle.close()


class TestBaselineSanity(unittest.TestCase):
    def test_baseline_returns_correct_results(self):
        driver, proc = connect_baseline()
        try:
            driver.initialize()
            resp = driver.call_tool("get_customer", {"customer": "kim"})
            self.assertIn("kim", resp["result"]["content"][0]["text"])
        finally:
            close_baseline(proc)


if __name__ == "__main__":
    unittest.main()
