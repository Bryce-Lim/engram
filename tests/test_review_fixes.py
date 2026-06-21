"""Regression tests for issues found by the adversarial review.

Each test pins behavior that a confirmed finding said was wrong, so the fix
can't silently regress.
"""

import threading
import time
import unittest

from engram import jsonrpc
from engram.cache import SpeculationCache, canonical_signature
from engram.config import ConfigError, load_intent_rules
from engram.metrics import Metrics
from engram.predictors.base import Prediction
from engram.predictors.markov import MarkovModel
from engram.safety import ToolRegistry
from engram.speculator import Speculator


def build(dispatch, registry=None, warm_ttl=30.0, max_workers=4):
    reg = registry or ToolRegistry()
    if registry is None:
        reg.update_from_list([
            {"name": "search", "annotations": {"readOnlyHint": True}},
            {"name": "send_email", "annotations": {"readOnlyHint": False}},
        ])
    cache = SpeculationCache()
    metrics = Metrics()
    spec = Speculator(reg, cache, metrics, dispatch=dispatch,
                      max_workers=max_workers, warm_ttl=warm_ttl)
    return reg, cache, metrics, spec


class TestSafetyGateTOCTOU(unittest.TestCase):
    def test_tool_flipping_to_non_readonly_aborts_before_dispatch(self):
        # A tool is read-only at consider() time; it flips to non-read-only
        # before the worker dispatches. The worker must abort and never call.
        reg = ToolRegistry()
        reg.update_from_list([{"name": "t", "annotations": {"readOnlyHint": True}}])
        dispatched = []
        gate = threading.Event()

        def dispatch(name, args):
            dispatched.append(name)
            return {"ok": True}, None

        cache = SpeculationCache()
        metrics = Metrics()
        spec = Speculator(reg, cache, metrics, dispatch=dispatch, max_workers=2)

        # Consider while read-only (passes the first gate), but flip the
        # registry to non-read-only immediately, before the worker runs.
        reg.update_from_list([{"name": "t", "annotations": {"readOnlyHint": False}}])
        spec.consider([Prediction("t", {"a": 1}, 0.9, "cot_oracle")])
        time.sleep(0.1)
        # The pre-dispatch re-check should have aborted it.
        self.assertEqual(dispatched, [])

    def test_aborted_spec_is_a_miss_not_an_error_hit(self):
        # If a real call attaches to a spec that gets safety-aborted, it must
        # fall back to a fresh execution (miss), not receive the abort error.
        reg = ToolRegistry()
        reg.update_from_list([{"name": "t", "annotations": {"readOnlyHint": True}}])
        cache = SpeculationCache()
        metrics = Metrics()
        spec = Speculator(reg, cache, metrics, dispatch=lambda n, a: ({"ok": 1}, None))

        sig = canonical_signature("t", {"a": 1})
        s, _ = cache.reserve(sig, "t", {"a": 1}, "src")
        s.settle_aborted({"code": -32603, "message": "aborted"})
        outcome, result, error = spec.resolve_call("t", {"a": 1})
        self.assertEqual(outcome, "miss")
        self.assertIsNone(error)


class TestWarmTTL(unittest.TestCase):
    def test_stale_warm_result_falls_back(self):
        _, cache, metrics, spec = build(lambda n, a: ({"v": 1}, None), warm_ttl=0.05)
        sig = canonical_signature("search", {"q": "x"})
        s, _ = cache.reserve(sig, "search", {"q": "x"}, "src")
        s.settle_result({"v": 1})
        time.sleep(0.12)  # exceed the TTL
        outcome, _, _ = spec.resolve_call("search", {"q": "x"})
        self.assertEqual(outcome, "miss")

    def test_fresh_warm_result_is_served(self):
        _, cache, metrics, spec = build(lambda n, a: ({"v": 1}, None), warm_ttl=30.0)
        sig = canonical_signature("search", {"q": "x"})
        s, _ = cache.reserve(sig, "search", {"q": "x"}, "src")
        s.settle_result({"v": 1})
        outcome, result, _ = spec.resolve_call("search", {"q": "x"})
        self.assertEqual(outcome, "warm")
        self.assertEqual(result, {"v": 1})


class TestPrecisionHonesty(unittest.TestCase):
    def test_inflight_unconsumed_counts_wrong(self):
        # A spec still in flight at reconcile time, never consumed, is wrong.
        block = threading.Event()
        _, cache, metrics, spec = build(lambda n, a: (block.wait(2.0), ({"v": 1}, None))[1])
        spec.consider([Prediction("search", {"q": "unused"}, 0.9, "cot_oracle")])
        time.sleep(0.05)
        spec.reconcile()  # still in flight, never claimed
        self.assertGreaterEqual(metrics.wrong_speculations, 1)
        block.set()


class TestMarkovBounded(unittest.TestCase):
    def test_rows_capped(self):
        m = MarkovModel(min_observations=1, max_rows=5, max_successors=4)
        for i in range(50):
            m.learn("prev%d" % i, "next%d" % i)
        self.assertLessEqual(len(m.export()), 5)

    def test_successors_capped_and_totals_consistent(self):
        m = MarkovModel(min_observations=1, max_rows=100, max_successors=3)
        for i in range(20):
            m.learn("a", "succ%d" % i)
        row = m.export()["a"]
        self.assertLessEqual(len(row), 3)
        # _totals for "a" must equal the sum of its (capped) row counts.
        with m._lock:
            self.assertEqual(m._totals["a"], sum(row.values()))


class TestConfigHardening(unittest.TestCase):
    def test_non_dict_top_level(self):
        import tempfile, os, json
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump([1, 2, 3], fh)
        self.addCleanup(os.remove, path)
        with self.assertRaises(ConfigError):
            load_intent_rules(path)

    def test_bad_confidence_type(self):
        import tempfile, os, json
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"intent_rules": [
                {"pattern": "x", "tool": "t", "confidence": "high"}]}, fh)
        self.addCleanup(os.remove, path)
        with self.assertRaises(ConfigError):
            load_intent_rules(path)


class _BlockStream:
    """A fake byte stream that returns preset blocks from read()."""

    def __init__(self, blocks):
        self._blocks = list(blocks)

    def read(self, _n):
        if self._blocks:
            return self._blocks.pop(0)
        return b""


class TestBoundedLines(unittest.TestCase):
    def _frame(self, blocks, cap=1024):
        from engram.downstream import DownstreamClient
        c = DownstreamClient(["true"], max_line_bytes=cap)
        return list(c._bounded_lines(_BlockStream(blocks)))

    def test_message_split_across_blocks(self):
        out = self._frame([b'{"a":', b'1}\n'])
        self.assertEqual(out, [b'{"a":1}'])

    def test_final_message_without_trailing_newline(self):
        out = self._frame([b'{"a":1}\n{"b":2}'])
        self.assertEqual(out, [b'{"a":1}', b'{"b":2}'])

    def test_multiple_messages_one_block(self):
        out = self._frame([b'a\nb\nc\n'])
        self.assertEqual(out, [b'a', b'b', b'c'])

    def test_oversize_line_with_newline_is_dropped(self):
        # The cap-crossing line terminates in the same block — must be dropped,
        # and the following good line still delivered.
        out = self._frame([b'A' * 50 + b'\ngood\n'], cap=10)
        self.assertEqual(out, [b'good'])

    def test_oversize_line_no_newline_resyncs(self):
        # Oversize with no newline yet: drop and resync at the next newline.
        out = self._frame([b'A' * 20, b'BBBB\ngood\n'], cap=10)
        self.assertEqual(out, [b'good'])

    def test_oversize_terminating_in_crossing_block(self):
        # Regression: newline arrives in the same block that crosses the cap.
        out = self._frame([b'A' * 8, b'BBBBBBBB\ngood\n'], cap=10)
        self.assertEqual(out, [b'good'])


class TestJsonRpcBatch(unittest.TestCase):
    def test_batch_raises_distinct_error(self):
        with self.assertRaises(jsonrpc.BatchNotSupported):
            jsonrpc.decode(b'[{"jsonrpc":"2.0","id":1,"method":"x"}]\n')

    def test_object_still_decodes(self):
        msg = jsonrpc.decode(b'{"jsonrpc":"2.0","id":1,"method":"x"}\n')
        self.assertEqual(msg["method"], "x")


if __name__ == "__main__":
    unittest.main()
