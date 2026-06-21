"""JSON-RPC 2.0 message helpers and the MCP stdio framing.

MCP's stdio transport frames each JSON-RPC message as a single line of UTF-8
JSON terminated by ``\\n`` (newline-delimited JSON). Embedded newlines are not
allowed inside a message, which ``json.dumps`` guarantees by default. We keep
this module dependency-free and side-effect-free so it can be unit-tested in
isolation and reused by both the upstream (agent-facing) and downstream
(server-facing) sides of the proxy.
"""

import json
from typing import Any, Dict, Optional

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC 2.0 error codes (subset we emit).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def make_request(id: Any, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a JSON-RPC request object (one expecting a response)."""
    msg = {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_notification(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a JSON-RPC notification (no ``id``, no response expected)."""
    msg = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_result(id: Any, result: Any) -> Dict[str, Any]:
    """Build a successful JSON-RPC response."""
    return {"jsonrpc": JSONRPC_VERSION, "id": id, "result": result}


def make_error(id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """Build a JSON-RPC error response."""
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": id, "error": err}


def is_request(msg: Dict[str, Any]) -> bool:
    """A request has both a ``method`` and an ``id``."""
    return "method" in msg and "id" in msg


def is_notification(msg: Dict[str, Any]) -> bool:
    """A notification has a ``method`` but no ``id``."""
    return "method" in msg and "id" not in msg


def is_response(msg: Dict[str, Any]) -> bool:
    """A response has an ``id`` and either ``result`` or ``error`` (but no method)."""
    return "method" not in msg and "id" in msg and ("result" in msg or "error" in msg)


def encode(msg: Dict[str, Any]) -> bytes:
    """Encode a message to a single newline-terminated UTF-8 line.

    ``ensure_ascii=False`` keeps non-ASCII payloads compact; ``separators``
    drops insignificant whitespace. ``json.dumps`` never emits a raw newline,
    so the framing invariant (one message per line) always holds.
    """
    line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    return (line + "\n").encode("utf-8")


class BatchNotSupported(ValueError):
    """Raised when a top-level JSON array (a JSON-RPC batch) is received.

    Precog forwards messages one at a time and does not implement batching; a
    distinct type lets the proxy answer with a precise error instead of
    silently dropping the request (which would hang the client).
    """


def decode(line: bytes) -> Dict[str, Any]:
    """Decode one framed line into a JSON object.

    Raises ``BatchNotSupported`` for a top-level array and ``ValueError`` on
    other malformed JSON or a non-object top level, so callers can translate
    each into the appropriate JSON-RPC error.
    """
    text = line.decode("utf-8").strip()
    obj = json.loads(text)
    if isinstance(obj, list):
        raise BatchNotSupported("JSON-RPC batch requests are not supported")
    if not isinstance(obj, dict):
        raise ValueError("JSON-RPC message must be an object, got %s" % type(obj).__name__)
    return obj
