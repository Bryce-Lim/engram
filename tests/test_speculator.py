import threading
import time
import unittest

from precog.cache import SpeculationCache
from precog.metrics import Metrics
from precog.predictors.base import Prediction
from precog.safety import ToolRegistry
from precog.speculator import Speculator


def build(dispatch, max_workers=8):
    reg = ToolRegistry()
    reg.update_from_list([
        {"name": "search", "annotations": {"readOnlyHint": True}},
        {"name": "fetch", "annotations": {"readOnlyHint": True}},
        {"name": "send_email", "annotations": {"readOnlyHint": False}},
    ])
    cache = SpeculationCache()
    metrics = Metrics()
    spec = Speculator(reg, cache, metrics, dispatch=dispatch, max_workers=max_workers)
    return reg, cache, metrics, spec


class TestSpeculator(unittest.TestCase):
    def test_safety_gate_blocks_non_readonly(self):
        calls = []

        def dispatch(name, args):
            calls.append(name)
            return {"ok": True}, None

        _, _, metrics, spec = build(dispatch)
        fired = spec.consider([Prediction("send_email", {"to": "x"}, 1.0, "eager")])
        self.assertEqual(fired, [])
        # Give any (erroneously dispatched) worker a moment.
        time.sleep(0.05)
        self.assertEqual(calls, [])
        self.assertEqual(metrics.speculations_fired, 0)

    def test_warm_hit_returns_cached_result(self):
        def dispatch(name, args):
            return {"content": name}, None

        _, _, metrics, spec = build(dispatch)
        spec.consider([Prediction("search", {"q": "a"}, 0.9, "cot_oracle")])
        # Wait for the speculation to settle.
        time.sleep(0.1)
        outcome, result, error = spec.resolve_call("search", {"q": "a"})
        self.assertEqual(outcome, "warm")
        self.assertEqual(result, {"content": "search"})
        self.assertIsNone(error)
        self.assertEqual(metrics.warm_hits, 1)
        self.assertGreater(metrics.saved_seconds, 0.0)

    def test_late_hit_waits_for_inflight(self):
        release = threading.Event()

        def dispatch(name, args):
            release.wait(2.0)
            return {"content": name}, None

        _, _, metrics, spec = build(dispatch)
        spec.consider([Prediction("fetch", {"id": 1}, 0.9, "markov")])

        results = {}

        def caller():
            outcome, result, error = spec.resolve_call("fetch", {"id": 1}, wait_timeout=2.0)
            results["outcome"] = outcome
            results["result"] = result

        t = threading.Thread(target=caller)
        t.start()
        time.sleep(0.1)  # ensure the real call is blocked on the in-flight spec
        self.assertNotIn("outcome", results)  # still waiting
        release.set()
        t.join(2.0)
        self.assertEqual(results["outcome"], "late")
        self.assertEqual(results["result"], {"content": "fetch"})
        self.assertEqual(metrics.late_hits, 1)

    def test_miss_when_no_speculation(self):
        def dispatch(name, args):
            return {"ok": True}, None

        _, _, metrics, spec = build(dispatch)
        outcome, result, error = spec.resolve_call("search", {"q": "novel"})
        self.assertEqual(outcome, "miss")
        self.assertIsNone(result)
        self.assertIsNone(error)
        self.assertEqual(metrics.misses, 1)

    def test_duplicate_predictions_fire_once(self):
        calls = []
        lock = threading.Lock()

        def dispatch(name, args):
            with lock:
                calls.append(name)
            return {"ok": True}, None

        _, _, metrics, spec = build(dispatch)
        spec.consider([
            Prediction("search", {"q": "a"}, 0.9, "cot_oracle"),
            Prediction("search", {"q": "a"}, 0.8, "markov"),  # same signature
        ])
        time.sleep(0.1)
        self.assertEqual(calls.count("search"), 1)
        self.assertEqual(metrics.speculations_fired, 1)

    def test_deterministic_error_served_as_hit(self):
        # A deterministic error (e.g. INVALID_PARAMS) is a property of the call
        # itself: the real call would get the same answer, so it is served warm.
        def dispatch(name, args):
            return None, {"code": -32602, "message": "bad params"}

        _, _, metrics, spec = build(dispatch)
        spec.consider([Prediction("search", {"q": "a"}, 0.9, "cot_oracle")])
        time.sleep(0.1)
        outcome, result, error = spec.resolve_call("search", {"q": "a"})
        self.assertEqual(outcome, "warm")
        self.assertIsNone(result)
        self.assertEqual(error["message"], "bad params")
        self.assertEqual(metrics.warm_hits, 1)

    def test_transient_error_falls_back_to_fresh(self):
        # A non-deterministic error (custom/transient code) must NOT be served;
        # the real call re-executes and may succeed.
        def dispatch(name, args):
            return None, {"code": -32000, "message": "transient"}

        _, _, metrics, spec = build(dispatch)
        spec.consider([Prediction("search", {"q": "a"}, 0.9, "cot_oracle")])
        time.sleep(0.1)
        outcome, result, error = spec.resolve_call("search", {"q": "a"})
        self.assertEqual(outcome, "miss")
        self.assertEqual(metrics.misses, 1)
        self.assertEqual(metrics.warm_hits, 0)

    def test_dispatch_exception_falls_back_to_fresh(self):
        # A worker that raises settles INTERNAL_ERROR, which is transient and
        # therefore must miss (re-execute) rather than be served warm.
        def dispatch(name, args):
            raise RuntimeError("kaboom")

        _, _, metrics, spec = build(dispatch)
        spec.consider([Prediction("search", {"q": "a"}, 0.9, "cot_oracle")])
        time.sleep(0.1)
        outcome, result, error = spec.resolve_call("search", {"q": "a"})
        self.assertEqual(outcome, "miss")
        self.assertEqual(metrics.misses, 1)

    def test_reconcile_counts_wrong_speculations(self):
        def dispatch(name, args):
            return {"ok": True}, None

        _, _, metrics, spec = build(dispatch)
        spec.consider([Prediction("search", {"q": "unused"}, 0.9, "cot_oracle")])
        time.sleep(0.1)
        spec.reconcile()  # never claimed -> wrong
        self.assertEqual(metrics.wrong_speculations, 1)
        self.assertLess(metrics.precision(), 1.0)


if __name__ == "__main__":
    unittest.main()
