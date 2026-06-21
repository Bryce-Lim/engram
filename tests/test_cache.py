import threading
import unittest

from precog.cache import SpeculationCache, canonical_signature


class TestCanonicalSignature(unittest.TestCase):
    def test_arg_order_independent(self):
        a = canonical_signature("t", {"x": 1, "y": 2})
        b = canonical_signature("t", {"y": 2, "x": 1})
        self.assertEqual(a, b)

    def test_distinguishes_tool_and_args(self):
        self.assertNotEqual(canonical_signature("t", {"x": 1}),
                            canonical_signature("t", {"x": 2}))
        self.assertNotEqual(canonical_signature("t1", {"x": 1}),
                            canonical_signature("t2", {"x": 1}))

    def test_none_and_empty_args_equivalent(self):
        self.assertEqual(canonical_signature("t", None), canonical_signature("t", {}))

    def test_non_serializable_args_do_not_crash(self):
        # default=str path
        sig = canonical_signature("t", {"obj": object()})
        self.assertIsInstance(sig, str)


class TestSpeculationCache(unittest.TestCase):
    def test_reserve_is_create_once(self):
        cache = SpeculationCache()
        sig = canonical_signature("t", {"a": 1})
        spec1, created1 = cache.reserve(sig, "t", {"a": 1}, "src")
        spec2, created2 = cache.reserve(sig, "t", {"a": 1}, "other")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertIs(spec1, spec2)

    def test_settle_and_ready(self):
        cache = SpeculationCache()
        sig = canonical_signature("t", {})
        spec, _ = cache.reserve(sig, "t", {}, "src")
        self.assertFalse(spec.is_ready)
        spec.settle_result({"ok": True})
        self.assertTrue(spec.is_ready)
        self.assertIsNotNone(spec.latency)
        self.assertIsNone(spec.error)

    def test_claim_marks_consumed(self):
        cache = SpeculationCache()
        sig = canonical_signature("t", {})
        cache.reserve(sig, "t", {}, "src")
        spec = cache.claim(sig)
        self.assertIsNotNone(spec)
        self.assertTrue(spec.consumed)
        self.assertIsNone(cache.claim(canonical_signature("absent", {})))

    def test_discard(self):
        cache = SpeculationCache()
        sig = canonical_signature("t", {})
        cache.reserve(sig, "t", {}, "src")
        cache.discard(sig)
        self.assertIsNone(cache.get(sig))
        cache.discard(sig)  # idempotent

    def test_eviction_caps_size_for_settled_entries(self):
        # Settled entries are evictable, so a flood of completed specs is capped.
        cache = SpeculationCache(max_entries=3)
        for i in range(10):
            spec, _ = cache.reserve(canonical_signature("t", {"i": i}), "t", {"i": i}, "src")
            spec.settle_result({"ok": i})  # settle so it is eligible for eviction
        self.assertLessEqual(len(cache.snapshot()), 3)

    def test_inflight_specs_are_never_evicted(self):
        # In-flight (unsettled) specs must NOT be evicted even past capacity —
        # evicting one would orphan its worker and let a real call double-fire.
        cache = SpeculationCache(max_entries=3)
        inflight = []
        for i in range(10):
            spec, _ = cache.reserve(canonical_signature("t", {"i": i}), "t", {"i": i}, "src")
            inflight.append(spec)  # never settled
        snap = cache.snapshot()
        # All ten in-flight entries survive (cap exceeded transiently, by design).
        for i in range(10):
            self.assertIn(canonical_signature("t", {"i": i}), snap)

    def test_eviction_callback_counts_unconsumed(self):
        evicted = []
        cache = SpeculationCache(max_entries=2,
                                 on_evict_unconsumed=lambda s: evicted.append(s.signature))
        for i in range(5):
            spec, _ = cache.reserve(canonical_signature("t", {"i": i}), "t", {"i": i}, "src")
            spec.settle_result({"ok": i})  # settled but never claimed -> wrong
        # The earliest settled+unconsumed specs were evicted and reported.
        self.assertGreaterEqual(len(evicted), 1)

    def test_reserve_is_thread_safe_single_creator(self):
        # Under concurrent reserve of the same signature, exactly one creator.
        cache = SpeculationCache()
        sig = canonical_signature("t", {"a": 1})
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            _, created = cache.reserve(sig, "t", {"a": 1}, "src")
            results.append(created)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for c in results if c), 1)


if __name__ == "__main__":
    unittest.main()
