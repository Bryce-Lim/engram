"""A scripted MCP client (a stand-in agent) used to drive the demo and tests.

It speaks MCP stdio over any byte streams: in the demo we point it at an
in-process Engram proxy (for the speculative path) or directly at the mock
server subprocess (for the baseline). A background reader thread demultiplexes
responses by JSON-RPC id, so calls can be issued and awaited synchronously.
"""

import itertools
import threading
from typing import Any, Dict, Optional

from engram import jsonrpc
from engram.proxy import REASONING_METHOD, TOOL_INTENT_METHOD


class AgentDriver:
    """Minimal MCP client over (write_stream, read_stream) byte pipes."""

    def __init__(self, write_stream, read_stream):
        self._w = write_stream
        self._r = read_stream
        self._counter = itertools.count(1)
        self._pending = {}  # type: Dict[Any, Any]
        self._lock = threading.Lock()
        self._events = {}   # type: Dict[Any, threading.Event]
        self._reader = threading.Thread(target=self._read_loop, name="agent-reader")
        self._reader.daemon = True
        self._reader.start()

    def _read_loop(self) -> None:
        for raw in iter(self._r.readline, b""):
            if not raw.strip():
                continue
            try:
                msg = jsonrpc.decode(raw)
            except ValueError:
                continue
            if jsonrpc.is_response(msg):
                mid = msg.get("id")
                with self._lock:
                    self._pending[mid] = msg
                    ev = self._events.get(mid)
                if ev is not None:
                    ev.set()

    def _send(self, msg: Dict[str, Any]) -> None:
        self._w.write(jsonrpc.encode(msg))
        self._w.flush()

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._send(jsonrpc.make_notification(method, params))

    def send_reasoning(self, text: str) -> None:
        """Forward a chunk of the model's chain-of-thought to the proxy."""
        self.notify(REASONING_METHOD, {"text": text})

    def send_tool_intent(self, name: str, arguments: Dict[str, Any]) -> None:
        """Tell the proxy a fully-formed call is imminent (eager dispatch)."""
        self.notify(TOOL_INTENT_METHOD, {"name": name, "arguments": arguments})

    def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                timeout: float = 30.0) -> Dict[str, Any]:
        mid = next(self._counter)
        ev = threading.Event()
        with self._lock:
            self._events[mid] = ev
        self._send(jsonrpc.make_request(mid, method, params))
        if not ev.wait(timeout):
            raise TimeoutError("agent request timed out: %s" % method)
        with self._lock:
            return self._pending.pop(mid)

    def initialize(self) -> Dict[str, Any]:
        resp = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "engram-demo-agent", "version": "0.1.0"},
        })
        self.notify("notifications/initialized")
        return resp

    def list_tools(self) -> Dict[str, Any]:
        return self.request("tools/list")

    def call_tool(self, name: str, arguments: Dict[str, Any],
                  timeout: float = 30.0) -> Dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments},
                            timeout=timeout)
