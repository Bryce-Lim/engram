import unittest

from engram.safety import ToolRegistry, is_speculatable


class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.update_from_list([
            {"name": "search", "annotations": {"readOnlyHint": True}},
            {"name": "send_email", "annotations": {"readOnlyHint": False}},
            {"name": "no_hint"},                       # no annotations at all
            {"name": "empty_ann", "annotations": {}},  # annotations but no hint
        ])

    def test_read_only_true(self):
        self.assertTrue(self.reg.is_read_only("search"))
        self.assertTrue(is_speculatable(self.reg, "search"))

    def test_explicit_false_is_not_speculatable(self):
        self.assertFalse(self.reg.is_read_only("send_email"))
        self.assertFalse(is_speculatable(self.reg, "send_email"))

    def test_missing_hint_fails_closed(self):
        # Fail-closed: absence of the hint means NOT speculatable.
        self.assertFalse(self.reg.is_read_only("no_hint"))
        self.assertFalse(self.reg.is_read_only("empty_ann"))
        self.assertFalse(is_speculatable(self.reg, "no_hint"))

    def test_unknown_tool_fails_closed(self):
        self.assertFalse(is_speculatable(self.reg, "never_seen"))

    def test_truthy_but_not_true_is_rejected(self):
        # Only the boolean True licenses speculation, not a truthy string.
        reg = ToolRegistry()
        reg.update_from_list([{"name": "x", "annotations": {"readOnlyHint": "yes"}}])
        self.assertFalse(reg.is_read_only("x"))

    def test_update_is_idempotent_and_merges(self):
        self.reg.update_from_list([{"name": "search", "annotations": {"readOnlyHint": False}}])
        # Latest wins.
        self.assertFalse(self.reg.is_read_only("search"))
        self.assertIn("send_email", self.reg.names())


if __name__ == "__main__":
    unittest.main()
