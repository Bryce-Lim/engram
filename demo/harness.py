"""Wiring helpers shared by the demo and the integration tests.

``connect_baseline`` gives you an :class:`AgentDriver` talking *directly* to the
mock MCP server subprocess (the serial, un-accelerated path).

``connect_precog`` gives you an :class:`AgentDriver` talking to an in-process
:class:`~precog.proxy.Precog`, which in turn drives the same mock server
subprocess. The proxy runs in a daemon thread reading a real OS pipe, so the
byte path is identical to production ``precog wrap`` — only the transport
endpoints are in-process.
"""

import os
import re
import sys
import threading
from typing import List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from demo.driver import AgentDriver  # noqa: E402
from precog.predictors.cot_oracle import CoTOracle, IntentRule  # noqa: E402
from precog.proxy import Precog  # noqa: E402

MOCK_SERVER = os.path.join(_HERE, "mock_server.py")


def server_command() -> List[str]:
    return [sys.executable, MOCK_SERVER]


def demo_intent_rules() -> List[IntentRule]:
    """The explicit chain-of-thought rules the demo's oracle uses.

    Each maps a reasoning phrase to a concrete read-only call and captures the
    argument straight out of the model's narrated intent.
    """
    return [
        IntentRule(re.compile(r"orders for (?P<customer>[\w]+)", re.I),
                   "get_orders", arg_map={"customer": "customer"}, confidence=0.95),
        IntentRule(re.compile(r"profile for (?P<customer>[\w]+)", re.I),
                   "get_customer", arg_map={"customer": "customer"}, confidence=0.95),
        IntentRule(re.compile(r"invoice for (?P<order_id>[\w-]+)", re.I),
                   "fetch_invoice", arg_map={"order_id": "order_id"}, confidence=0.95),
    ]


class _Pipe:
    """A unidirectional byte pipe exposed as readable/writable file objects."""

    def __init__(self) -> None:
        r_fd, w_fd = os.pipe()
        self.reader = os.fdopen(r_fd, "rb", buffering=0)
        self.writer = os.fdopen(w_fd, "wb", buffering=0)


def connect_baseline() -> Tuple[AgentDriver, "subprocess.Popen"]:
    """Agent talks directly to the mock server subprocess (no Precog)."""
    import subprocess
    proc = subprocess.Popen(server_command(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    driver = AgentDriver(write_stream=proc.stdin, read_stream=proc.stdout)
    return driver, proc


def close_baseline(proc) -> None:
    """Tear down a baseline server subprocess and close its pipes cleanly."""
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


class PrecogHandle:
    """Owns an in-process Precog proxy and the agent connected to it."""

    def __init__(self, proxy: Precog, driver: AgentDriver,
                 agent_to_precog: _Pipe, precog_to_agent: _Pipe,
                 thread: threading.Thread) -> None:
        self.proxy = proxy
        self.driver = driver
        self._a2p = agent_to_precog
        self._p2a = precog_to_agent
        self._thread = thread

    def close(self) -> None:
        # Closing the agent->precog write end gives Precog's read loop EOF.
        try:
            self._a2p.writer.close()
        except Exception:
            pass
        self._thread.join(timeout=5)
        self.proxy.shutdown()
        # Close the remaining pipe ends to avoid leaked-fd warnings.
        for stream in (self._a2p.reader, self._p2a.reader, self._p2a.writer):
            try:
                stream.close()
            except Exception:
                pass


def connect_precog(on_log=None, install_demo_rules: bool = True,
                   markov_min_observations: int = 1) -> PrecogHandle:
    """Agent talks to an in-process Precog that drives the mock server."""
    a2p = _Pipe()  # agent writes -> precog reads
    p2a = _Pipe()  # precog writes -> agent reads

    proxy = Precog(
        downstream_command=server_command(),
        upstream_in=a2p.reader,
        upstream_out=p2a.writer,
        on_log=on_log,
    )
    if install_demo_rules and proxy.cot is not None:
        for rule in demo_intent_rules():
            proxy.cot.add_rule(rule)
    if proxy.markov is not None:
        # The demo tunes the Markov model to predict after a single observation
        # so its learning is visible within three runs.
        proxy.markov.min_observations = markov_min_observations

    thread = threading.Thread(target=proxy.serve_forever, name="precog-serve")
    thread.daemon = True
    thread.start()

    driver = AgentDriver(write_stream=a2p.writer, read_stream=p2a.reader)
    return PrecogHandle(proxy, driver, a2p, p2a, thread)
