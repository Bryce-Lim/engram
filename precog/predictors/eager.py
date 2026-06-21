"""Signal 1 — eager dispatch (zero guessing).

When a host knows a fully-formed tool call is imminent, it can tell Precog via
the ``notifications/precog/tool_intent`` hint channel *before* it formally
emits the ``tools/call`` request. Eager dispatch turns that intent into a
maximum-confidence prediction so the call begins executing immediately instead
of after the request is serialized and routed — and when a turn carries several
such intents, their executions overlap.

This involves no guessing: the host has stated the model *already decided* to
make the call. It is the floor of the system. The prediction is still routed
through the read-only safety gate like every other signal: a non-read-only
intent is simply dropped by the gate (never speculatively dispatched), and the
agent's real ``tools/call`` is then served normally by the proxy. Correctness
is never at stake.

Note: this is driven by the optional hint channel, not by parsing a streamed
tool-call message off the MCP wire (MCP carries the call as a single request,
not a token stream). A host that forwards nothing still benefits from the
Markov signal and protocol-safe concurrency.
"""

from typing import Any, Dict, List

from precog.predictors.base import Prediction, Predictor


class EagerDispatch(Predictor):
    name = "eager"

    def on_partial_tool_call(self, tool_name: str,
                             arguments: Dict[str, Any]) -> List[Prediction]:
        # The call is fully specified and committed by the model — maximum
        # confidence. The only thing standing between it and dispatch is the
        # read-only safety gate applied by the speculator.
        return [Prediction(tool_name, arguments, confidence=1.0, source=self.name)]
