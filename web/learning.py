"""Process-level cross-run learning store for the web demo.

Each web request builds a fresh in-process Precog. On its own, the Markov
sequence model would start empty every time, so the "it gets smarter as it
learns" story would never show in the UI. This module persists a Markov
transition table *across requests*, keyed by a normalized scenario signature
(the ordered set of tool names in the plan), so:

  * Run 1 of a given scenario: Markov knows nothing — only the chain-of-thought
    oracle predicts. This is the honest cold-start.
  * Run 2+: the prior runs' transitions are loaded into the new proxy's Markov
    model, so it *also* pre-warms the next-tool in a learned chain, on top of
    the oracle. The hit pattern visibly strengthens.

This is real learning persisted in memory — not a scripted animation. It is
deliberately scoped to the current server process (no disk), so a restart
resets it, which is the right behavior for a demo.

Thread-safety: a single lock guards the store; get/observe are short critical
sections that copy in/out, so concurrent requests can't corrupt the table.
"""

import threading
from typing import Any, Dict, List

_lock = threading.Lock()
# signature -> {"table": markov_export_dict, "runs": int}
_store = {}  # type: Dict[str, Dict[str, Any]]


def scenario_signature(calls: List[Dict[str, Any]]) -> str:
    """A stable key for 'the same kind of run' — the ordered tool-name path.

    Arguments are intentionally excluded: the Markov model predicts the next
    *tool*, so two runs over the same tool sequence (even with different
    customers/orders) share the learned transitions.
    """
    return ">".join(c.get("name", "?") for c in (calls or []))


def get_state(signature: str) -> Dict[str, Any]:
    """Return ``{"table": {...}, "runs": N}`` for a signature (runs seen so far)."""
    with _lock:
        entry = _store.get(signature)
        if entry is None:
            return {"table": {}, "runs": 0}
        # Copy so callers can't mutate the stored table outside the lock.
        return {"table": {k: dict(v) for k, v in entry["table"].items()},
                "runs": entry["runs"]}


def record_run(signature: str, table: Dict[str, Dict[str, int]]) -> int:
    """Persist the learned transition table from a completed run.

    Returns the new run count for this signature.
    """
    with _lock:
        entry = _store.get(signature)
        if entry is None:
            entry = {"table": {}, "runs": 0}
            _store[signature] = entry
        entry["table"] = {k: dict(v) for k, v in (table or {}).items()}
        entry["runs"] += 1
        return entry["runs"]


def reset() -> None:
    """Clear all learned state (used by tests and the preflight check)."""
    with _lock:
        _store.clear()
