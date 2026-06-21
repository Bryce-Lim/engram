"""Predictor interface and the Prediction value object."""

from typing import Any, Dict, List, Optional


class Prediction:
    """A candidate tool call proposed by a predictor.

    ``confidence`` is a 0..1 score used only for ordering/telemetry; the safety
    gate, not confidence, decides whether a prediction may fire. ``source`` is
    the predictor's name, recorded in metrics so we can see which signal earns
    its keep.
    """

    __slots__ = ("tool_name", "arguments", "confidence", "source")

    def __init__(self, tool_name: str, arguments: Optional[Dict[str, Any]],
                 confidence: float, source: str):
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {}
        self.confidence = confidence
        self.source = source

    def __repr__(self) -> str:
        return "Prediction(%r, %r, conf=%.2f, src=%s)" % (
            self.tool_name, self.arguments, self.confidence, self.source)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Prediction):
            return NotImplemented
        return (self.tool_name == other.tool_name
                and self.arguments == other.arguments
                and self.source == other.source)


class Predictor:
    """Base class for prediction signals.

    Subclasses override the hooks that matter to them and ignore the rest. The
    speculator calls every hook on every predictor and unions the proposals, so
    a predictor that has nothing to say simply returns ``[]``.
    """

    name = "base"

    def on_reasoning(self, text: str) -> List[Prediction]:
        """Called as the model's chain-of-thought streams in."""
        return []

    def on_partial_tool_call(self, tool_name: str,
                             arguments: Dict[str, Any]) -> List[Prediction]:
        """Called when the host signals an imminent, fully-formed tool call.

        ``arguments`` is the complete argument object for a call the host has
        announced (via the tool-intent hint channel) but that has not yet been
        formally dispatched downstream.
        """
        return []

    def on_observed_call(self, tool_name: str, arguments: Dict[str, Any]) -> List[Prediction]:
        """Called after a real tool call is dispatched, to predict the *next* one."""
        return []

    def learn(self, prev_tool: Optional[str], next_tool: str) -> None:
        """Called with each observed tool→tool transition, for online learning."""
        return None
