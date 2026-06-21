import unittest

from precog.metrics import Metrics


class TestMetrics(unittest.TestCase):
    def test_hit_rate_and_precision(self):
        m = Metrics()
        m.record_speculation("cot_oracle")
        m.record_speculation("cot_oracle")
        m.record_speculation("markov")
        m.record_warm_hit(0.4)
        m.record_late_hit(0.2)
        m.record_miss()
        # 2 hits / 3 real calls
        self.assertAlmostEqual(m.hit_rate(), 2 / 3)
        self.assertEqual(m.hits, 2)
        # 3 fired, 1 wrong -> 2/3 precision
        m.record_wrong(1)
        self.assertAlmostEqual(m.precision(), 2 / 3)

    def test_zero_division_safe(self):
        m = Metrics()
        self.assertEqual(m.hit_rate(), 0.0)
        self.assertEqual(m.precision(), 0.0)

    def test_saved_seconds_accumulates_nonnegative(self):
        m = Metrics()
        m.record_warm_hit(0.5)
        m.record_warm_hit(-1.0)  # clamped to 0
        self.assertAlmostEqual(m.saved_seconds, 0.5)

    def test_as_dict_shape(self):
        m = Metrics()
        m.record_speculation("eager")
        m.record_warm_hit(0.3)
        d = m.as_dict()
        for key in ("real_calls", "speculations_fired", "warm_hits", "late_hits",
                    "misses", "hit_rate", "saved_seconds", "by_source"):
            self.assertIn(key, d)
        self.assertEqual(d["by_source"], {"eager": 1})


if __name__ == "__main__":
    unittest.main()
