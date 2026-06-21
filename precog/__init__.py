"""Precog — speculative execution for AI agents, as MCP middleware.

Precog is a drop-in proxy for the Model Context Protocol (MCP). It sits between
an agent (the MCP *client*/host) and a tool server (the MCP *server*), predicts
the agent's next tool calls while the model is still thinking, fires the
side-effect-free ones in parallel, and serves the results the instant the model
actually asks. Branch prediction, for agents.

The public entry points are :class:`precog.proxy.Precog` (the proxy itself) and
:func:`precog.cli.main` (the ``precog wrap ...`` command-line interface).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
