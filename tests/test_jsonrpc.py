import unittest

from engram import jsonrpc


class TestJsonRpc(unittest.TestCase):
    def test_request_roundtrip(self):
        msg = jsonrpc.make_request(1, "tools/call", {"name": "x"})
        line = jsonrpc.encode(msg)
        self.assertTrue(line.endswith(b"\n"))
        # Exactly one newline: the framing terminator, none embedded.
        self.assertEqual(line.count(b"\n"), 1)
        back = jsonrpc.decode(line)
        self.assertEqual(back, msg)

    def test_message_classification(self):
        req = jsonrpc.make_request(1, "m")
        notif = jsonrpc.make_notification("m")
        res = jsonrpc.make_result(1, {"ok": True})
        err = jsonrpc.make_error(1, jsonrpc.INTERNAL_ERROR, "boom")

        self.assertTrue(jsonrpc.is_request(req))
        self.assertFalse(jsonrpc.is_notification(req))

        self.assertTrue(jsonrpc.is_notification(notif))
        self.assertFalse(jsonrpc.is_request(notif))

        self.assertTrue(jsonrpc.is_response(res))
        self.assertTrue(jsonrpc.is_response(err))
        self.assertFalse(jsonrpc.is_request(res))

    def test_error_has_code_and_message(self):
        err = jsonrpc.make_error("abc", jsonrpc.INVALID_PARAMS, "bad", data={"hint": 1})
        self.assertEqual(err["error"]["code"], jsonrpc.INVALID_PARAMS)
        self.assertEqual(err["error"]["message"], "bad")
        self.assertEqual(err["error"]["data"], {"hint": 1})

    def test_decode_rejects_non_object(self):
        with self.assertRaises(ValueError):
            jsonrpc.decode(b"[1,2,3]\n")
        with self.assertRaises(ValueError):
            jsonrpc.decode(b"not json\n")

    def test_encode_no_embedded_newline_with_unicode(self):
        # ensure_ascii=False keeps unicode, but json.dumps must not emit a raw
        # newline even when the payload contains an escaped one.
        msg = jsonrpc.make_result(1, {"text": "line1\nline2", "emoji": "✓"})
        line = jsonrpc.encode(msg)
        self.assertEqual(line.count(b"\n"), 1)
        back = jsonrpc.decode(line)
        self.assertEqual(back["result"]["text"], "line1\nline2")


if __name__ == "__main__":
    unittest.main()
