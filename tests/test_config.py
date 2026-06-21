import json
import os
import tempfile
import unittest

from engram.config import ConfigError, load_intent_rules


class TestConfig(unittest.TestCase):
    def _write(self, obj):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_loads_valid_rules(self):
        path = self._write({"intent_rules": [
            {"pattern": "orders for (?P<customer>\\w+)", "tool": "get_orders",
             "args": {"customer": "customer"}, "confidence": 0.95, "flags": "i"}]})
        rules = load_intent_rules(path)
        self.assertEqual(len(rules), 1)
        # The compiled rule captures the argument from matching text.
        out = rules[0].matches("pull the Orders For alice now")
        self.assertEqual(out, [{"customer": "alice"}])
        self.assertEqual(rules[0].tool_name, "get_orders")
        self.assertEqual(rules[0].confidence, 0.95)

    def test_static_args_merged(self):
        path = self._write({"intent_rules": [
            {"pattern": "status", "tool": "get_status",
             "static_args": {"verbose": True}}]})
        rules = load_intent_rules(path)
        self.assertEqual(rules[0].matches("status"), [{"verbose": True}])

    def test_missing_intent_rules_array(self):
        path = self._write({"nope": []})
        with self.assertRaises(ConfigError):
            load_intent_rules(path)

    def test_rule_needs_pattern_and_tool(self):
        path = self._write({"intent_rules": [{"pattern": "x"}]})
        with self.assertRaises(ConfigError):
            load_intent_rules(path)

    def test_bad_regex_reported(self):
        path = self._write({"intent_rules": [{"pattern": "(", "tool": "t"}]})
        with self.assertRaises(ConfigError):
            load_intent_rules(path)

    def test_unknown_flag_reported(self):
        path = self._write({"intent_rules": [{"pattern": "x", "tool": "t", "flags": "z"}]})
        with self.assertRaises(ConfigError):
            load_intent_rules(path)

    def test_missing_file_reported(self):
        with self.assertRaises(ConfigError):
            load_intent_rules("/nonexistent/engram-rules.json")


if __name__ == "__main__":
    unittest.main()
