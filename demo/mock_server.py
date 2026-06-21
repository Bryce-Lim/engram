#!/usr/bin/env python3
"""A minimal but real MCP stdio server, used by the demo and tests.

It speaks the same newline-delimited JSON-RPC framing as a production MCP
server and implements ``initialize``, ``tools/list``, and ``tools/call``. Each
tool sleeps for a configurable latency to stand in for real network/API I/O —
this is the cost Engram hides by prefetching during the model's think time.

Tools (the ``readOnlyHint`` annotation is what gates speculation):

* ``search``        (read-only)  — find order ids for a customer
* ``get_orders``    (read-only)  — list a customer's recent orders
* ``get_customer``  (read-only)  — fetch a customer profile
* ``fetch_invoice`` (read-only)  — fetch an invoice document
* ``send_email``    (NOT read-only) — a side-effecting tool; Engram must never
  speculate it.

Latency is controlled by the ``ENGRAM_DEMO_LATENCY`` env var (seconds, float).
"""

import os
import sys
import threading
import time

# Allow running directly from a checkout.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engram import jsonrpc  # noqa: E402

LATENCY = float(os.environ.get("ENGRAM_DEMO_LATENCY", "0.4"))

TOOLS = [
    {
        "name": "search",
        "description": "Search for a customer's order ids by name.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "get_orders",
        "description": "List recent orders for a customer.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"customer": {"type": "string"}}},
    },
    {
        "name": "get_customer",
        "description": "Fetch a customer profile record.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"customer": {"type": "string"}}},
    },
    {
        "name": "fetch_invoice",
        "description": "Fetch an invoice document by order id.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    },
    {
        "name": "send_email",
        "description": "Send an email to the customer. Has side effects.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {"type": "object", "properties": {"to": {"type": "string"},
                                                         "body": {"type": "string"}}},
    },
    # Argument-free read-only tools, used by the Markov learning demo: because
    # the Markov model predicts the next *tool* (not its arguments), a warm hit
    # is only possible when the call carries no arguments, so the predicted
    # empty-argument signature matches the real call exactly.
    {
        "name": "get_status",
        "description": "Get current pipeline status. No arguments.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_metrics",
        "description": "Get current dashboard metrics. No arguments.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_alerts",
        "description": "Get active alerts. No arguments.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
    },
    # --- Extended read-only catalog: lets the planner handle diverse prompts
    #     (support, ops, commerce, docs) with believable multi-step plans. ---
    {
        "name": "get_shipping",
        "description": "Get shipping and delivery status for an order id.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    },
    {
        "name": "get_refund_policy",
        "description": "Look up the refund policy for a product or region.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}},
    },
    {
        "name": "list_tickets",
        "description": "List recent support tickets for a customer.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"customer": {"type": "string"}}},
    },
    {
        "name": "search_kb",
        "description": "Search the internal knowledge base / documentation by keyword.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
    {
        "name": "get_inventory",
        "description": "Check inventory level for a product sku.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"sku": {"type": "string"}}},
    },
    {
        "name": "get_payment",
        "description": "Get the payment record for an order id.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    },
    {
        "name": "get_logs",
        "description": "Fetch recent service logs for a component. No arguments.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"component": {"type": "string"}}},
    },
    # --- Additional side-effecting tools: each is readOnlyHint:false, so the
    #     safety gate must never speculate them no matter what the prompt says.
    {
        "name": "issue_refund",
        "description": "Issue a refund for an order. Moves money. Side effects.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"},
                                                         "amount": {"type": "number"}}},
    },
    {
        "name": "cancel_order",
        "description": "Cancel an order. Side effects.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    },
    {
        "name": "create_ticket",
        "description": "Open a new support ticket. Side effects.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {"type": "object", "properties": {"subject": {"type": "string"}}},
    },
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _pick(seed, options):
    """Deterministically pick an option from a seed string (stable per arg)."""
    h = 0
    for ch in str(seed):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return options[h % len(options)]


def _run_tool(name, arguments):
    """Simulate I/O latency, then return a text content block."""
    time.sleep(LATENCY)
    if name == "search":
        q = arguments.get("query", "?")
        text = "search '%s': 2 matches — ORD-1001, ORD-1002" % q
    elif name == "get_orders":
        c = arguments.get("customer", "?")
        tier = _pick(c, ["gold", "silver", "platinum"])
        n = _pick(c + "n", ["2", "3", "4"])
        text = "recent orders for %s (%s): %s orders, latest ORD-%s" % (
            c, tier, n, _pick(c, ["1001", "1002", "2050", "3097"]))
    elif name == "get_customer":
        c = arguments.get("customer", "?")
        tier = _pick(c, ["gold", "silver", "platinum"])
        since = _pick(c + "y", ["2019", "2020", "2021", "2022"])
        text = "customer %s: tier=%s, since=%s, lifetime=$%s" % (
            c, tier, since, _pick(c + "$", ["1,240", "880", "3,510", "640"]))
    elif name == "fetch_invoice":
        o = arguments.get("order_id", "?")
        amt = _pick(o, ["42.00", "17.50", "129.99", "8.25", "256.40"])
        paid = _pick(o + "p", ["paid", "paid", "pending"])
        text = "invoice %s: total $%s, %s" % (o, amt, paid)
    elif name == "get_shipping":
        o = arguments.get("order_id", "?")
        text = "shipping %s: %s, carrier %s" % (
            o, _pick(o, ["delivered", "in transit", "out for delivery"]),
            _pick(o + "c", ["UPS", "FedEx", "USPS"]))
    elif name == "get_refund_policy":
        t = arguments.get("topic", "general")
        text = "refund policy (%s): 30-day window, full refund if unopened" % t
    elif name == "list_tickets":
        c = arguments.get("customer", "?")
        text = "tickets for %s: %s open, latest 'shipping delay'" % (
            c, _pick(c, ["0", "1", "2"]))
    elif name == "search_kb":
        q = arguments.get("query", "?")
        text = "KB '%s': 3 articles — top: 'How refunds work'" % q
    elif name == "get_inventory":
        s = arguments.get("sku", "?")
        text = "inventory %s: %s units in stock" % (s, _pick(s, ["0", "14", "230", "1,902"]))
    elif name == "get_payment":
        o = arguments.get("order_id", "?")
        text = "payment %s: %s, %s" % (
            o, _pick(o, ["Visa ••4242", "Amex ••1009", "PayPal"]),
            _pick(o + "s", ["captured", "authorized"]))
    elif name == "get_logs":
        comp = arguments.get("component", "service")
        text = "logs %s: 0 errors, last event %ss ago" % (comp, _pick(comp, ["3", "12", "47"]))
    elif name == "send_email":
        text = "email sent to %s" % arguments.get("to", "?")
    elif name == "issue_refund":
        text = "refund issued for %s" % arguments.get("order_id", "?")
    elif name == "cancel_order":
        text = "order %s cancelled" % arguments.get("order_id", "?")
    elif name == "create_ticket":
        text = "ticket opened: %s" % arguments.get("subject", "(no subject)")
    elif name == "get_status":
        text = "pipeline status: GREEN, 0 blocked stages"
    elif name == "get_metrics":
        text = "metrics: p50=12ms p99=88ms throughput=4.2k/s"
    elif name == "get_alerts":
        text = "active alerts: none"
    else:
        return None, jsonrpc.make_error(None, jsonrpc.METHOD_NOT_FOUND,
                                        "unknown tool: %s" % name)["error"]
    return {"content": [{"type": "text", "text": text}], "isError": False}, None


def _handle(msg):
    method = msg.get("method")
    if jsonrpc.is_notification(msg):
        return None  # nothing to answer (e.g. notifications/initialized)
    msg_id = msg.get("id")
    if method == "initialize":
        return jsonrpc.make_result(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "engram-demo-server", "version": "0.1.0"},
        })
    if method == "tools/list":
        return jsonrpc.make_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        if name not in TOOLS_BY_NAME:
            return jsonrpc.make_error(msg_id, jsonrpc.INVALID_PARAMS,
                                      "unknown tool: %s" % name)
        result, error = _run_tool(name, params.get("arguments") or {})
        if error is not None:
            return {"jsonrpc": jsonrpc.JSONRPC_VERSION, "id": msg_id, "error": error}
        return jsonrpc.make_result(msg_id, result)
    if method == "ping":
        return jsonrpc.make_result(msg_id, {})
    return jsonrpc.make_error(msg_id, jsonrpc.METHOD_NOT_FOUND, "unknown method: %s" % method)


def main():
    # A realistic MCP server handles concurrent calls — a network-backed tool
    # does not serialize unrelated requests behind one another. We mirror that
    # by handling each request on its own thread and guarding stdout with a
    # lock, so the per-call I/O sleeps overlap. This is what makes parallel
    # speculative prefetch meaningful (and is how production servers behave).
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    write_lock = threading.Lock()

    def handle_async(message):
        response = _handle(message)
        if response is not None:
            with write_lock:
                stdout.write(jsonrpc.encode(response))
                stdout.flush()

    threads = []
    for raw in iter(stdin.readline, b""):
        if not raw.strip():
            continue
        try:
            msg = jsonrpc.decode(raw)
        except ValueError:
            continue
        t = threading.Thread(target=handle_async, args=(msg,))
        t.daemon = True
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=5)


if __name__ == "__main__":
    main()
