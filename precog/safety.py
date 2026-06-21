"""Safety by protocol: only speculate on side-effect-free tools.

MCP lets a server annotate each tool with hints. The one that matters here is
``readOnlyHint``: if true, the tool does not modify its environment. Precog
speculatively executes a tool call *before the model has committed to it*, so
it MUST never speculate a tool that could send an email, charge a card, or
otherwise mutate state. We treat ``readOnlyHint == true`` as the *only* license
to speculate. Absence of the hint is treated as unsafe (fail closed).

The tool registry is populated from the downstream server's ``tools/list``
response, which the proxy observes as it passes through.
"""

import threading
from typing import Any, Dict, List, Optional


class ToolRegistry:
    """Tracks tool metadata learned from ``tools/list`` responses."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools = {}  # type: Dict[str, Dict[str, Any]]

    def update_from_list(self, tools: List[Dict[str, Any]]) -> None:
        """Ingest the ``tools`` array from a ``tools/list`` result."""
        with self._lock:
            for tool in tools:
                name = tool.get("name")
                if name:
                    self._tools[name] = tool

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._tools.get(name)

    def names(self) -> List[str]:
        with self._lock:
            return list(self._tools.keys())

    def is_read_only(self, name: str) -> bool:
        """True only if the tool is explicitly annotated ``readOnlyHint: true``.

        Fail-closed: unknown tools and tools without the hint are NOT read-only,
        and therefore never eligible for speculation.
        """
        tool = self.get(name)
        if tool is None:
            return False
        annotations = tool.get("annotations") or {}
        return annotations.get("readOnlyHint") is True


def is_speculatable(registry: ToolRegistry, tool_name: str) -> bool:
    """The single correctness gate every predictor must pass through.

    A tool call may be speculatively executed iff its tool is known to the
    registry and explicitly marked read-only. This is enforced centrally so no
    individual predictor can bypass it.
    """
    return registry.is_read_only(tool_name)
