"""``engram`` command-line interface.

Usage::

    engram wrap [options] -- <server-command> [args...]
    engram wrap ./your-mcp-server

``wrap`` launches ``<server-command>`` as the downstream MCP server and serves
the Engram proxy on this process's stdio. Point your agent/host at ``engram``
instead of the server and you get speculative execution with zero changes to
the agent. Logs go to stderr (stdout is reserved for the MCP byte stream).
"""

import argparse
import sys
from typing import List, Optional

from engram import __version__
from engram.proxy import Engram


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engram",
        description="Speculative execution for AI agents — a performance layer for MCP.")
    parser.add_argument("--version", action="version", version="engram " + __version__)
    sub = parser.add_subparsers(dest="command")

    wrap = sub.add_parser("wrap", help="wrap an MCP server with the Engram proxy")
    wrap.add_argument("--quiet", action="store_true", help="suppress stderr logging")
    wrap.add_argument("--no-cot", action="store_true",
                      help="disable the chain-of-thought oracle")
    wrap.add_argument("--no-markov", action="store_true",
                      help="disable the Markov sequence model")
    wrap.add_argument("--no-eager", action="store_true",
                      help="disable eager dispatch")
    wrap.add_argument("--timeout", type=float, default=30.0,
                      help="downstream call timeout in seconds (default: 30)")
    wrap.add_argument("--rules", metavar="FILE",
                      help="JSON file of chain-of-thought intent rules "
                           "(enables argument-capturing prediction)")
    wrap.add_argument("server", nargs=argparse.REMAINDER,
                      help="the MCP server command (prefix with -- to be safe)")
    return parser


def _normalize_server_command(server: List[str]) -> List[str]:
    # argparse.REMAINDER keeps a leading "--" if the user wrote one; drop it.
    if server and server[0] == "--":
        return server[1:]
    return server


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "wrap":
        parser.print_help(sys.stderr)
        return 2

    server_cmd = _normalize_server_command(args.server)
    if not server_cmd:
        print("engram wrap: missing server command\n", file=sys.stderr)
        parser.parse_args(["wrap", "--help"])
        return 2

    def log(message: str) -> None:
        if not args.quiet:
            sys.stderr.write("[engram] " + message + "\n")
            sys.stderr.flush()

    intent_rules = None
    if args.rules:
        from engram.config import ConfigError, load_intent_rules
        try:
            intent_rules = load_intent_rules(args.rules)
        except ConfigError as exc:
            print("engram wrap: %s" % exc, file=sys.stderr)
            return 2
        log("loaded %d intent rule(s) from %s" % (len(intent_rules), args.rules))

    proxy = Engram(
        downstream_command=server_cmd,
        enable_cot=not args.no_cot,
        enable_markov=not args.no_markov,
        enable_eager=not args.no_eager,
        late_hit_timeout=args.timeout,
        on_log=log,
        intent_rules=intent_rules,
    )
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        proxy.shutdown()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
