"""Prediction signals, layered from "always safe" to "genuinely novel".

Each predictor proposes :class:`~engram.predictors.base.Prediction` objects —
candidate ``(tool_name, arguments)`` calls the agent is likely to make next.
The speculator filters every proposal through the safety gate before it is
allowed to fire.

The four signals, in increasing order of speculativeness:

1. :class:`~engram.predictors.eager.EagerDispatch` — zero guessing. When the
   host signals (via the hint channel) that a fully-formed call is imminent,
   begin executing it immediately rather than after the request is routed.
2. :class:`~engram.predictors.cot_oracle.CoTOracle` — the novel part. Parse the
   model's reasoning stream for stated intent and prefetch during the think.
3. :class:`~engram.predictors.markov.MarkovModel` — learn tool→tool transitions
   from observed traffic and prefetch the likely successor.
4. The safety gate (in :mod:`engram.safety`) is the correctness layer that all
   of the above are subordinate to.
"""

from engram.predictors.base import Prediction, Predictor
from engram.predictors.eager import EagerDispatch
from engram.predictors.cot_oracle import CoTOracle
from engram.predictors.markov import MarkovModel

__all__ = ["Prediction", "Predictor", "EagerDispatch", "CoTOracle", "MarkovModel"]
