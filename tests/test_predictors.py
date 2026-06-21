import re
import unittest

from engram.predictors.base import Prediction
from engram.predictors.cot_oracle import CoTOracle, IntentRule
from engram.predictors.eager import EagerDispatch
from engram.predictors.markov import MarkovModel
from engram.safety import ToolRegistry


class TestEager(unittest.TestCase):
    def test_emits_committed_call_with_max_confidence(self):
        preds = EagerDispatch().on_partial_tool_call("search", {"q": "x"})
        self.assertEqual(len(preds), 1)
        self.assertEqual(preds[0].tool_name, "search")
        self.assertEqual(preds[0].arguments, {"q": "x"})
        self.assertEqual(preds[0].confidence, 1.0)
        self.assertEqual(preds[0].source, "eager")


class TestMarkov(unittest.TestCase):
    def test_predicts_after_threshold(self):
        m = MarkovModel(min_probability=0.25, top_k=2, min_observations=2)
        # One observation: below threshold, silent.
        m.learn("search", "fetch")
        self.assertEqual(m.on_observed_call("search", {}), [])
        # Second observation crosses min_observations.
        m.learn("search", "fetch")
        preds = m.on_observed_call("search", {})
        self.assertEqual([p.tool_name for p in preds], ["fetch"])
        self.assertAlmostEqual(preds[0].confidence, 1.0)

    def test_ranks_and_thresholds(self):
        m = MarkovModel(min_probability=0.3, top_k=2, min_observations=1)
        for _ in range(7):
            m.learn("a", "b")
        for _ in range(3):
            m.learn("a", "c")
        # b: 0.7, c: 0.3 — both above threshold, b first.
        preds = m.on_observed_call("a", {})
        self.assertEqual([p.tool_name for p in preds], ["b", "c"])

    def test_below_threshold_dropped(self):
        m = MarkovModel(min_probability=0.5, top_k=3, min_observations=1)
        for _ in range(8):
            m.learn("a", "b")
        for _ in range(2):
            m.learn("a", "c")  # 0.2 — below 0.5
        preds = m.on_observed_call("a", {})
        self.assertEqual([p.tool_name for p in preds], ["b"])

    def test_export_load_roundtrip(self):
        m = MarkovModel(min_observations=1)
        m.learn("a", "b")
        m.learn("a", "b")
        table = m.export()
        m2 = MarkovModel(min_observations=1)
        m2.load(table)
        self.assertEqual(m2.on_observed_call("a", {})[0].tool_name, "b")

    def test_ignores_empty_transitions(self):
        m = MarkovModel(min_observations=1)
        m.learn(None, "b")   # no prev
        m.learn("a", "")     # no next
        self.assertEqual(m.on_observed_call("a", {}), [])


class TestCoTOracle(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.update_from_list([
            {"name": "get_orders", "description": "List recent orders for a customer.",
             "annotations": {"readOnlyHint": True}},
            {"name": "get_customer", "description": "Fetch a customer profile.",
             "annotations": {"readOnlyHint": True}},
            {"name": "send_email", "description": "Send an email.",
             "annotations": {"readOnlyHint": False}},
        ])

    def test_explicit_rule_captures_arguments(self):
        oracle = CoTOracle(self.reg)
        oracle.add_rule(IntentRule(
            re.compile(r"orders for (?P<customer>\w+)", re.I),
            "get_orders", arg_map={"customer": "customer"}, confidence=0.95))
        preds = oracle.on_reasoning("Let me look up the orders for alice next.")
        match = [p for p in preds if p.tool_name == "get_orders"]
        self.assertTrue(match)
        self.assertEqual(match[0].arguments, {"customer": "alice"})
        self.assertEqual(match[0].confidence, 0.95)

    def test_rule_fires_for_multiple_instances(self):
        oracle = CoTOracle(self.reg)
        oracle.add_rule(IntentRule(
            re.compile(r"orders for (?P<customer>\w+)", re.I),
            "get_orders", arg_map={"customer": "customer"}))
        preds = oracle.on_reasoning("orders for alice, then orders for bob")
        customers = sorted(p.arguments["customer"] for p in preds
                           if p.tool_name == "get_orders")
        self.assertEqual(customers, ["alice", "bob"])

    def test_keyword_trigger_without_rule(self):
        # No explicit rule: the auto-derived keyword "orders" should still fire
        # get_orders (with empty args, lower confidence).
        oracle = CoTOracle(self.reg)
        preds = oracle.on_reasoning("I should check their recent orders.")
        names = [p.tool_name for p in preds]
        self.assertIn("get_orders", names)
        go = [p for p in preds if p.tool_name == "get_orders"][0]
        self.assertLessEqual(go.confidence, 0.75)

    def test_keyword_suppressed_when_rule_covers_tool(self):
        # When a rule already proposes get_orders WITH args, the empty-arg
        # keyword guess for the same tool must be suppressed.
        oracle = CoTOracle(self.reg)
        oracle.add_rule(IntentRule(
            re.compile(r"orders for (?P<customer>\w+)", re.I),
            "get_orders", arg_map={"customer": "customer"}))
        preds = oracle.on_reasoning("pull the orders for alice")
        go = [p for p in preds if p.tool_name == "get_orders"]
        self.assertEqual(len(go), 1)
        self.assertEqual(go[0].arguments, {"customer": "alice"})

    def test_empty_reasoning_silent(self):
        self.assertEqual(CoTOracle(self.reg).on_reasoning(""), [])

    def test_late_registry_population_is_picked_up(self):
        # Oracle built against an empty registry; tools arrive later.
        empty = ToolRegistry()
        oracle = CoTOracle(empty)
        self.assertEqual(oracle.on_reasoning("recent orders"), [])
        empty.update_from_list([
            {"name": "get_orders", "description": "orders",
             "annotations": {"readOnlyHint": True}}])
        names = [p.tool_name for p in oracle.on_reasoning("recent orders please")]
        self.assertIn("get_orders", names)


if __name__ == "__main__":
    unittest.main()
