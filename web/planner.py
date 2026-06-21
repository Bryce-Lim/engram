"""Turn a free-text prompt into an agent plan (reasoning + tool calls).

This is a DETERMINISTIC stand-in for a real LLM: in production the model's
reasoning stream and tool calls drive Engram, but there is no live model in this
environment. The planner inspects the prompt for entities (customer names, order
ids, SKUs, intent keywords) and emits:

  * ``reasoning`` — a narrated plan, phrased so the chain-of-thought oracle's
    intent rules can recognize each call (this mirrors what a model narrates).
  * ``calls`` — the ordered tool calls the agent then makes.
  * ``rules`` — the intent rules the oracle uses to predict those calls.

The IMPORTANT honesty point: the planner only decides *what calls happen*. The
with/without-Engram timings measured downstream are real — the planner does not
fabricate any speedup.

Design goals (demo robustness):
  * NEVER raise — any string in produces a valid plan out.
  * NEVER return zero calls — there is always a sensible investigation.
  * Recognize a broad range of intents (support, commerce, ops, docs) so an
    arbitrary prompt yields a believable multi-step plan.
  * Every read-only call is covered by an intent rule so the oracle can predict
    it; side-effecting calls are intentionally NOT predicted (safety gate).
"""

import re
from typing import Any, Dict, List

# Read-only tools the planner may emit (all predictable / speculatable).
READ_ONLY_TOOLS = {
    "search", "get_orders", "get_customer", "fetch_invoice", "get_status",
    "get_metrics", "get_alerts", "get_shipping", "get_refund_policy",
    "list_tickets", "search_kb", "get_inventory", "get_payment", "get_logs",
}
# Side-effecting tools (never speculated — the safety gate blocks them).
WRITE_TOOLS = {"send_email", "issue_refund", "cancel_order", "create_ticket"}

# Intent rules the oracle uses to recognize each read-only call from the
# narrated reasoning. The reasoning sentences below are written to match these.
INTENT_RULES = [
    {"pattern": r"search the knowledge base for (?P<query>[\w-]+)",
     "tool": "search_kb", "args": {"query": "query"}, "flags": "i"},
    {"pattern": r"search(?:ing)?(?: for)?(?: the keyword)? (?P<query>[\w-]+)",
     "tool": "search", "args": {"query": "query"}, "flags": "i"},
    {"pattern": r"orders for (?P<customer>\w+)",
     "tool": "get_orders", "args": {"customer": "customer"}, "flags": "i"},
    {"pattern": r"profile for (?P<customer>\w+)",
     "tool": "get_customer", "args": {"customer": "customer"}, "flags": "i"},
    {"pattern": r"tickets for (?P<customer>\w+)",
     "tool": "list_tickets", "args": {"customer": "customer"}, "flags": "i"},
    {"pattern": r"invoice for (?P<order_id>[\w-]+)",
     "tool": "fetch_invoice", "args": {"order_id": "order_id"}, "flags": "i"},
    {"pattern": r"shipping for (?P<order_id>[\w-]+)",
     "tool": "get_shipping", "args": {"order_id": "order_id"}, "flags": "i"},
    {"pattern": r"payment for (?P<order_id>[\w-]+)",
     "tool": "get_payment", "args": {"order_id": "order_id"}, "flags": "i"},
    {"pattern": r"refund policy for (?P<topic>[\w-]+)",
     "tool": "get_refund_policy", "args": {"topic": "topic"}, "flags": "i"},
    {"pattern": r"inventory for (?P<sku>[\w-]+)",
     "tool": "get_inventory", "args": {"sku": "sku"}, "flags": "i"},
    {"pattern": r"logs for (?P<component>[\w-]+)",
     "tool": "get_logs", "args": {"component": "component"}, "flags": "i"},
]

_ORDER_RE = re.compile(r"\b(ORD-\d+|order\s+#?\d+)\b", re.I)
_SKU_RE = re.compile(r"\b(SKU-?\w+|sku\s+\w+)\b", re.I)

_NAME_CLUSTER_RE = re.compile(
    r"\b(?:customer|customers|user|users|account|client|for|of)\s+"
    r"((?:[A-Za-z]{3,})(?:\s*(?:,|and|&)\s*[A-Za-z]{3,})*)\b",
    re.I,
)

_STOP = {
    "the", "and", "for", "with", "their", "orders", "order", "invoice",
    "profile", "profiles", "status", "metrics", "alerts", "email", "customer",
    "customers", "check", "send", "get", "pull", "fetch", "want", "need",
    "look", "into", "issue", "ticket", "tickets", "refund", "charge", "about",
    "what", "this", "that", "should", "would", "from", "they", "them", "then",
    "also", "while", "resolution", "system", "health", "dashboard", "tier",
    "tiers", "recent", "disputed", "charges", "now", "shipping", "payment",
    "inventory", "policy", "logs", "knowledge", "base", "everything", "please",
    "investigate", "review", "account", "client", "user", "users", "sku",
    "everyone", "anyone", "someone", "stock", "package", "order", "incident",
    "outage", "everybody", "all", "any", "both", "each", "them", "track",
}


def _find_customers(prompt: str) -> List[str]:
    """Heuristically extract customer names mentioned in the prompt."""
    found = []  # type: List[str]
    try:
        for m in _NAME_CLUSTER_RE.finditer(prompt):
            cluster = m.group(1)
            for tok in re.split(r"\s*(?:,|and|&)\s*", cluster):
                name = tok.strip().lower()
                if name and name not in _STOP and name not in found and name.isalpha():
                    found.append(name)
        if not found:
            for m in re.finditer(r"\b([A-Z][a-z]{2,})\b", prompt):
                name = m.group(1).lower()
                if name not in _STOP and name not in found:
                    found.append(name)
    except Exception:
        pass
    return found[:5]


def _find_orders(prompt: str) -> List[str]:
    orders = []  # type: List[str]
    try:
        for m in _ORDER_RE.finditer(prompt):
            raw = (m.group(1).upper().replace("ORDER", "ORD-")
                   .replace(" ", "").replace("#", ""))
            if not raw.startswith("ORD-"):
                raw = "ORD-" + re.sub(r"\D", "", raw)
            if raw not in orders:
                orders.append(raw)
    except Exception:
        pass
    return orders[:5]


def _find_skus(prompt: str) -> List[str]:
    skus = []  # type: List[str]
    try:
        for m in _SKU_RE.finditer(prompt):
            raw = m.group(1).upper().replace("SKU ", "SKU-").replace(" ", "")
            if not raw.startswith("SKU-"):
                raw = "SKU-" + re.sub(r"[^0-9A-Z]", "", raw)
            if raw not in skus:
                skus.append(raw)
    except Exception:
        pass
    return skus[:3]


def _has(low: str, *words: str) -> bool:
    return any(w in low for w in words)


def plan(prompt: str) -> Dict[str, Any]:
    """Build a reasoning narration + ordered tool calls from the prompt.

    Robust by construction: any input yields a valid, non-empty plan and this
    function never raises (a catch-all fallback covers unexpected input).
    """
    try:
        return _plan_inner(prompt or "")
    except Exception:
        # Absolute last-resort fallback — should never be hit, but guarantees a
        # demo never dies on a pathological prompt.
        return {
            "reasoning": ("Here's my plan. Let me check the current status, the "
                          "metrics, and any active alerts. Let me gather all of that."),
            "calls": [
                {"name": "get_status", "arguments": {}},
                {"name": "get_metrics", "arguments": {}},
                {"name": "get_alerts", "arguments": {}},
            ],
            "rules": INTENT_RULES,
        }


def _plan_inner(prompt: str) -> Dict[str, Any]:
    p = prompt.strip()
    low = p.lower()
    customers = _find_customers(p)
    orders = _find_orders(p)
    skus = _find_skus(p)

    calls = []           # type: List[Dict[str, Any]]
    bits = []            # type: List[str]

    def add(name, args=None):
        calls.append({"name": name, "arguments": args or {}})

    # --- 1. Knowledge-base / documentation search -----------------------
    if _has(low, "knowledge base", "kb", "docs", "documentation", "article", "how do", "how to"):
        kw = _keyword(low, ["refund", "shipping", "return", "billing", "account"], "help")
        bits.append("First I'll search the knowledge base for %s." % kw)
        add("search_kb", {"query": kw})

    # --- 2. Generic search to scope an investigation --------------------
    if _has(low, "search", "find", "look up", "scope", "investigate", "dispute"):
        kw = _keyword(low, ["refund", "issue", "dispute", "charge", "delay"], "account")
        bits.append("Let me search for %s to scope this." % kw)
        add("search", {"query": kw})

    # --- 3. Per-customer reads (orders, profile, tickets) ---------------
    for c in customers:
        bits.append("Let me pull the orders for %s." % c)
        add("get_orders", {"customer": c})
    if customers and _has(low, "profile", "tier", "account", "who", "vip", "loyal",
                          "history", "lifetime", "value"):
        for c in customers:
            bits.append("I want the profile for %s to check their tier." % c)
            add("get_customer", {"customer": c})
    if customers and _has(low, "ticket", "support", "complaint", "case", "contacted"):
        for c in customers:
            bits.append("Let me list the tickets for %s." % c)
            add("list_tickets", {"customer": c})

    # --- 4. Per-order reads (invoice, payment, shipping) ----------------
    for o in orders:
        bits.append("I'll fetch the invoice for %s." % o)
        add("fetch_invoice", {"order_id": o})
    if orders and _has(low, "payment", "charge", "card", "paid", "billing", "charged"):
        for o in orders:
            bits.append("Let me check the payment for %s." % o)
            add("get_payment", {"order_id": o})
    if orders and _has(low, "ship", "deliver", "track", "arrive", "transit", "package"):
        for o in orders:
            bits.append("Let me check the shipping for %s." % o)
            add("get_shipping", {"order_id": o})

    # --- 5. Refund policy lookup ----------------------------------------
    if _has(low, "refund", "return", "policy", "eligible", "money back"):
        topic = _keyword(low, ["electronics", "apparel", "digital", "general"], "general")
        bits.append("Let me look up the refund policy for %s." % topic)
        add("get_refund_policy", {"topic": topic})

    # --- 6. Inventory ----------------------------------------------------
    if skus:
        for s in skus:
            bits.append("Let me check the inventory for %s." % s)
            add("get_inventory", {"sku": s})
    elif _has(low, "inventory", "stock", "in stock", "availability", "restock"):
        bits.append("Let me check the inventory for SKU-1001.")
        add("get_inventory", {"sku": "SKU-1001"})

    # --- 7. System health / ops -----------------------------------------
    if _has(low, "health", "status", "metrics", "alert", "system", "pipeline",
            "dashboard", "monitor", "incident", "outage", "latency", "uptime"):
        bits.append("Let me also check the current status, the metrics, and any alerts.")
        add("get_status")
        add("get_metrics")
        add("get_alerts")
        if _has(low, "log", "error", "trace", "debug", "incident", "outage"):
            comp = _keyword(low, ["api", "worker", "db", "gateway"], "api")
            bits.append("And pull the logs for %s." % comp)
            add("get_logs", {"component": comp})

    # --- 8. Side-effecting actions (NEVER speculated; safety-gate demo) --
    if _has(low, "refund", "money back", "reimburse") and orders and \
            _has(low, "issue", "process", "give", "approve", "grant"):
        bits.append("If everything checks out I'll issue the refund for %s." % orders[0])
        add("issue_refund", {"order_id": orders[0], "amount": 42.0})
    if _has(low, "cancel") and orders:
        bits.append("I may cancel order %s." % orders[0])
        add("cancel_order", {"order_id": orders[0]})
    if _has(low, "open a ticket", "create a ticket", "file a ticket", "escalate"):
        bits.append("I'll open a support ticket to track this.")
        add("create_ticket", {"subject": "Follow-up from investigation"})
    if _has(low, "email", "notify", "reply", "respond", "let them know",
            "follow up", "resolution", "inform"):
        target = (customers[0] + "@example.com") if customers else "customer@example.com"
        bits.append("Finally I'll decide whether to send an email to the customer.")
        add("send_email", {"to": target, "body": "Following up on your request."})

    # --- Fallback: ensure a non-trivial, sensible plan always exists ----
    if not calls:
        # Use any names found, else a default health-check investigation.
        if customers:
            for c in customers:
                bits.append("Let me pull the orders for %s." % c)
                add("get_orders", {"customer": c})
                bits.append("And the profile for %s." % c)
                add("get_customer", {"customer": c})
        else:
            kw = _first_keyword(low) or "account"
            bits.append("Let me search for %s, then review system status, metrics, "
                        "and alerts." % kw)
            add("search", {"query": kw})
            add("get_status")
            add("get_metrics")
            add("get_alerts")

    # Cap the plan so the race stays watchable even on a huge prompt.
    if len(calls) > 16:
        calls = calls[:16]

    reasoning = "Here's my plan. " + " ".join(bits) + " Let me gather all of that."

    return {"reasoning": reasoning, "calls": calls, "rules": INTENT_RULES}


def _keyword(low: str, candidates: List[str], default: str) -> str:
    for c in candidates:
        if c in low:
            return c
    return default


def _first_keyword(low: str) -> str:
    """Pick the first 'contentful' word from the prompt as a search keyword."""
    for tok in re.findall(r"[a-z]{4,}", low):
        if tok not in _STOP:
            return tok
    return ""
