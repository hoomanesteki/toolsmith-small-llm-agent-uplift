"""opsworld's eleven tools: the shared verb grammar bound to retail nouns.

Two things are load-bearing in the way these are written.

**Errors are data, not exceptions.** Asking for a customer who does not exist
returns a clean ``not_found``. That is the correct behaviour to test against,
because the interesting question is what the agent does next: abstain, or invent
a customer. Tier T4 is built entirely on that distinction.

**Descriptions are written for a model, not for a docs page.** Each one says
when to reach for the tool and what it will not do, because tool-search ranks on
this text and a vague description is a wrong tool call three turns later.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from toolsmith.worlds._common import one_row, rows_to_dicts
from toolsmith.worlds.base import (
    ToolResult,
    ToolSpec,
    Verb,
    add_days,
    iso,
    paginate,
)

# --------------------------------------------------------------- read tools --


def search_customers(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if len(query) < 2:
        return ToolResult.failure("invalid_arguments", "query must be at least 2 characters")

    sql = (
        "SELECT customer_id, name, email, region, tier, signup_date, lifetime_value_cents "
        "FROM customers WHERE (name LIKE ? OR email LIKE ?)"
    )
    params: list[Any] = [f"%{query}%", f"%{query}%"]
    if args.get("region"):
        sql += " AND region = ?"
        params.append(args["region"])
    if args.get("tier"):
        sql += " AND tier = ?"
        params.append(args["tier"])
    sql += " ORDER BY customer_id"

    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    limited = paginate(rows, args.get("limit"))
    return ToolResult(
        ok=True,
        data={
            "query": query,
            "match_count": len(rows),
            "returned": len(limited),
            "truncated": len(limited) < len(rows),
            "customers": limited,
        },
    )


def get_customer(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    customer_id = str(args.get("customer_id", ""))
    row = one_row(conn, "SELECT * FROM customers WHERE customer_id = ?", (customer_id,))
    if row is None:
        return ToolResult.failure(
            "not_found",
            f"no customer with id {customer_id!r}. Use search_customers to find one by name.",
        )
    counts = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(total_cents), 0) FROM orders WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    return ToolResult(
        ok=True, data={**row, "order_count": counts[0], "order_total_cents": counts[1]}
    )


def get_order(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    order_id = str(args.get("order_id", ""))
    row = one_row(conn, "SELECT * FROM orders WHERE order_id = ?", (order_id,))
    if row is None:
        return ToolResult.failure(
            "not_found",
            f"no order with id {order_id!r}. Use list_orders for a customer's orders.",
        )
    refunded = (
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        or 0
    )
    row["items"] = json.loads(row["items"])
    return ToolResult(ok=True, data={**row, "refunded_cents": refunded})


def list_orders(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    customer_id = str(args.get("customer_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM customers WHERE customer_id = ?", (customer_id,)) is None:
        return ToolResult.failure("not_found", f"no customer with id {customer_id!r}")

    sql = (
        "SELECT order_id, placed_date, status, total_cents, shipping_cents, item_count, "
        "carrier, delivered_date FROM orders WHERE customer_id = ?"
    )
    params: list[Any] = [customer_id]
    if args.get("status"):
        sql += " AND status = ?"
        params.append(args["status"])
    if args.get("since"):
        sql += " AND placed_date >= ?"
        params.append(args["since"])
    if args.get("until"):
        sql += " AND placed_date <= ?"
        params.append(args["until"])
    sql += " ORDER BY placed_date DESC, order_id"

    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    limited = paginate(rows, args.get("limit"))
    return ToolResult(
        ok=True,
        data={
            "customer_id": customer_id,
            "match_count": len(rows),
            "returned": len(limited),
            "truncated": len(limited) < len(rows),
            "total_cents": sum(r["total_cents"] for r in rows),
            "orders": limited,
        },
    )


def lookup_refund_policy(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    tier = args.get("tier")
    if tier:
        row = one_row(conn, "SELECT * FROM refund_policies WHERE tier = ?", (str(tier),))
        if row is None:
            return ToolResult.failure("not_found", f"no refund policy for tier {tier!r}")
        return ToolResult(ok=True, data=row, citations=[row["policy_id"]])
    rows = rows_to_dicts(conn.execute("SELECT * FROM refund_policies ORDER BY policy_id"))
    return ToolResult(ok=True, data={"policies": rows}, citations=[r["policy_id"] for r in rows])


# ------------------------------------------------------------- metric tool --

#: Metric name to (SQL aggregate, source table). Enumerated rather than
#: interpolated, so no argument ever reaches SQL as an identifier.
METRICS: dict[str, tuple[str, str]] = {
    "order_count": ("COUNT(*)", "orders"),
    "revenue_cents": ("COALESCE(SUM(total_cents), 0)", "orders"),
    "avg_order_value_cents": ("CAST(COALESCE(AVG(total_cents), 0) AS INTEGER)", "orders"),
    "shipping_cents": ("COALESCE(SUM(shipping_cents), 0)", "orders"),
    "refund_total_cents": ("COALESCE(SUM(amount_cents), 0)", "refunds"),
    "refund_count": ("COUNT(*)", "refunds"),
    "ticket_count": ("COUNT(*)", "tickets"),
    "customer_count": ("COUNT(*)", "customers"),
}

GROUP_BY: dict[str, dict[str, str]] = {
    "region": {
        "orders": "c.region",
        "customers": "region",
        "tickets": "c.region",
        "refunds": "c.region",
    },
    "tier": {"orders": "c.tier", "customers": "tier", "tickets": "c.tier", "refunds": "c.tier"},
    "status": {"orders": "o.status", "tickets": "t.status"},
    "month": {
        "orders": "substr(o.placed_date, 1, 7)",
        "tickets": "substr(t.created_date, 1, 7)",
        "refunds": "substr(r.issued_date, 1, 7)",
    },
}

_TABLE_ALIAS = {"orders": "o", "customers": "c", "tickets": "t", "refunds": "r"}
_DATE_COLUMN = {
    "orders": "o.placed_date",
    "tickets": "t.created_date",
    "refunds": "r.issued_date",
    "customers": "c.signup_date",
}


def query_metrics(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    metric = str(args.get("metric", ""))
    if metric not in METRICS:
        return ToolResult.failure(
            "unknown_metric",
            f"{metric!r} is not a metric. Available: {', '.join(sorted(METRICS))}",
        )
    aggregate, table = METRICS[metric]
    alias = _TABLE_ALIAS[table]

    group_by = args.get("group_by") or "none"
    group_expr: str | None = None
    if group_by != "none":
        options = GROUP_BY.get(group_by, {})
        if table not in options:
            return ToolResult.failure(
                "invalid_group_by",
                f"metric {metric!r} cannot be grouped by {group_by!r}. "
                f"Try one of: {', '.join(sorted(k for k, v in GROUP_BY.items() if table in v))}",
            )
        group_expr = options[table]

    needs_customer = (group_expr or "").startswith("c.") or bool(
        args.get("region") or args.get("tier")
    )
    join = ""
    if needs_customer and table != "customers":
        join = f" JOIN customers c ON c.customer_id = {alias}.customer_id"
    elif table == "customers":
        alias = "c"

    where: list[str] = []
    params: list[Any] = []
    if args.get("since"):
        where.append(f"{_DATE_COLUMN[table]} >= ?")
        params.append(args["since"])
    if args.get("until"):
        where.append(f"{_DATE_COLUMN[table]} <= ?")
        params.append(args["until"])
    if args.get("region"):
        where.append("c.region = ?")
        params.append(args["region"])
    if args.get("tier"):
        where.append("c.tier = ?")
        params.append(args["tier"])
    if args.get("status") and table in {"orders", "tickets"}:
        where.append(f"{alias}.status = ?")
        params.append(args["status"])

    select = (
        f"{group_expr} AS bucket, {aggregate} AS value" if group_expr else f"{aggregate} AS value"
    )
    sql = f"SELECT {select} FROM {table} {alias}{join}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group_expr:
        sql += f" GROUP BY {group_expr} ORDER BY {group_expr}"

    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    if group_expr:
        return ToolResult(
            ok=True,
            data={
                "metric": metric,
                "group_by": group_by,
                "buckets": {str(r["bucket"]): r["value"] for r in rows},
                "total": sum(r["value"] for r in rows)
                if metric != "avg_order_value_cents"
                else None,
            },
        )
    return ToolResult(ok=True, data={"metric": metric, "value": rows[0]["value"] if rows else 0})


# -------------------------------------------------------------- write tools --


def create_ticket(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    customer_id = str(args.get("customer_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM customers WHERE customer_id = ?", (customer_id,)) is None:
        return ToolResult.failure("not_found", f"no customer with id {customer_id!r}")

    order_id = args.get("order_id")
    if (
        order_id
        and one_row(conn, "SELECT 1 AS x FROM orders WHERE order_id = ?", (str(order_id),)) is None
    ):
        return ToolResult.failure("not_found", f"no order with id {order_id!r}")

    # Ids are derived from the current maximum, not from a clock or a UUID, so a
    # replayed trajectory produces a byte-identical state diff.
    next_id = (conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] or 0) + 2_000
    ticket_id = f"TIC-{next_id}"
    while one_row(conn, "SELECT 1 AS x FROM tickets WHERE ticket_id = ?", (ticket_id,)):
        next_id += 1
        ticket_id = f"TIC-{next_id}"

    today = iso(add_days(0))
    conn.execute(
        "INSERT INTO tickets (ticket_id, customer_id, order_id, subject, body, status, "
        "priority, assignee, created_date, updated_date, resolution) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?, NULL, ?, ?, '')",
        (
            ticket_id,
            customer_id,
            order_id,
            str(args.get("subject", ""))[:200],
            str(args.get("body", ""))[:2000],
            str(args.get("priority", "normal")),
            today,
            today,
        ),
    )
    return ToolResult(
        ok=True,
        data={"ticket_id": ticket_id, "status": "open", "created_date": today},
        mutated=True,
    )


def update_ticket(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    ticket_id = str(args.get("ticket_id", ""))
    row = one_row(conn, "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    if row is None:
        return ToolResult.failure("not_found", f"no ticket with id {ticket_id!r}")

    fields = {k: args[k] for k in ("status", "priority", "assignee", "resolution") if args.get(k)}
    if not fields:
        return ToolResult.failure(
            "invalid_arguments", "supply at least one of: status, priority, assignee, resolution"
        )
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE tickets SET {assignments}, updated_date = ? WHERE ticket_id = ?",
        (*fields.values(), iso(add_days(0)), ticket_id),
    )
    return ToolResult(
        ok=True, data={"ticket_id": ticket_id, "updated": sorted(fields)}, mutated=True
    )


def issue_refund(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    """The privileged verb.

    By the time this runs the policy function has already approved it. That
    ordering is the point: this function contains no eligibility logic at all,
    so there is nowhere for a persuasive prompt to change the outcome.
    """
    order_id = str(args["order_id"])
    amount = int(args["amount_cents"])
    order = one_row(conn, "SELECT * FROM orders WHERE order_id = ?", (order_id,))
    if order is None:  # pragma: no cover - the policy already checked
        return ToolResult.failure("not_found", f"no order with id {order_id!r}")

    next_id = (conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] or 0) + 700
    refund_id = f"REF-{next_id}"
    while one_row(conn, "SELECT 1 AS x FROM refunds WHERE refund_id = ?", (refund_id,)):
        next_id += 1
        refund_id = f"REF-{next_id}"

    conn.execute(
        "INSERT INTO refunds (refund_id, order_id, customer_id, amount_cents, reason, "
        "issued_date, approved_by) VALUES (?, ?, ?, ?, ?, ?, 'policy_engine')",
        (
            refund_id,
            order_id,
            order["customer_id"],
            amount,
            str(args.get("reason", "unspecified"))[:200],
            iso(add_days(0)),
        ),
    )
    return ToolResult(
        ok=True,
        data={
            "refund_id": refund_id,
            "order_id": order_id,
            "amount_cents": amount,
            "issued_date": iso(add_days(0)),
        },
        mutated=True,
    )


# ------------------------------------------------------------- tool specs ----

_LIMIT = {"type": "integer", "description": "Maximum rows to return (1-100).", "minimum": 1}
_DATE = {"type": "string", "description": "ISO date, YYYY-MM-DD.", "maxLength": 10}

TOOLS: dict[Verb, ToolSpec] = {
    Verb.SEARCH_PRINCIPALS: ToolSpec(
        verb=Verb.SEARCH_PRINCIPALS,
        name="search_customers",
        description=(
            "Find customers by partial name or email. Use this first when the user names a "
            "person rather than giving a customer id. Returns matches with ids; it does not "
            "return orders."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Part of a name or email.",
                    "maxLength": 120,
                },
                "region": {"type": "string", "enum": ["EMEA", "AMER", "APAC", "LATAM"]},
                "tier": {"type": "string", "enum": ["free", "standard", "premium", "enterprise"]},
                "limit": _LIMIT,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_customers,
        examples=["find the customer called Ada", "who is nadia.silva", "customers in APAC"],
    ),
    Verb.GET_PRINCIPAL: ToolSpec(
        verb=Verb.GET_PRINCIPAL,
        name="get_customer",
        description=(
            "Fetch one customer by id, including tier, region and a summary of order volume. "
            "Requires an exact id; use search_customers if you only have a name."
        ),
        parameters={
            "type": "object",
            "properties": {"customer_id": {"type": "string", "pattern": "^CUS-", "maxLength": 20}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
        handler=get_customer,
        examples=["what tier is CUS-1042", "customer details", "look up this account"],
    ),
    Verb.GET_RECORD: ToolSpec(
        verb=Verb.GET_RECORD,
        name="get_order",
        description=(
            "Fetch one order by id: status, totals in cents, carrier, delivery date, line "
            "items, and how much has already been refunded. Check refunded_cents before "
            "proposing any refund."
        ),
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string", "pattern": "^ORD-", "maxLength": 20}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
        handler=get_order,
        examples=["status of ORD-5120", "when did this order ship", "order total"],
    ),
    Verb.LIST_RECORDS: ToolSpec(
        verb=Verb.LIST_RECORDS,
        name="list_orders",
        description=(
            "List a customer's orders, newest first, optionally filtered by status or date "
            "range. Returns match_count and truncated so you can tell whether you have seen "
            "everything."
        ),
        parameters={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "pattern": "^CUS-", "maxLength": 20},
                "status": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "processing",
                        "shipped",
                        "delivered",
                        "cancelled",
                        "returned",
                    ],
                },
                "since": _DATE,
                "until": _DATE,
                "limit": _LIMIT,
            },
            "required": ["customer_id"],
            "additionalProperties": False,
        },
        handler=list_orders,
        examples=["their recent orders", "everything delivered since October", "how many orders"],
    ),
    Verb.CREATE_CASE: ToolSpec(
        verb=Verb.CREATE_CASE,
        name="create_ticket",
        description=(
            "Open a support ticket for a customer, optionally linked to an order. Creates a "
            "record: only do this when the user has asked for it or policy requires it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "pattern": "^CUS-", "maxLength": 20},
                "subject": {"type": "string", "maxLength": 200},
                "body": {"type": "string", "maxLength": 2000},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "order_id": {"type": "string", "pattern": "^ORD-", "maxLength": 20},
            },
            "required": ["customer_id", "subject"],
            "additionalProperties": False,
        },
        handler=create_ticket,
        mutating=True,
        examples=["log a complaint", "open a ticket about the damaged item"],
    ),
    Verb.UPDATE_CASE: ToolSpec(
        verb=Verb.UPDATE_CASE,
        name="update_ticket",
        description=(
            "Change a ticket's status, priority, assignee or resolution. Supply only the "
            "fields you intend to change."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "pattern": "^TIC-", "maxLength": 20},
                "status": {
                    "type": "string",
                    "enum": ["open", "pending_customer", "escalated", "resolved", "closed"],
                },
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "assignee": {"type": "string", "maxLength": 60},
                "resolution": {"type": "string", "maxLength": 500},
            },
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
        handler=update_ticket,
        mutating=True,
        examples=["escalate this ticket", "mark it resolved", "reassign to another agent"],
    ),
    Verb.PRIVILEGED_WRITE: ToolSpec(
        verb=Verb.PRIVILEGED_WRITE,
        name="issue_refund",
        description=(
            "Issue a refund against an order, in cents. PRIVILEGED: every call is authorised "
            "server-side against the customer's tier policy after you request it, and may be "
            "refused. Check the order's refunded_cents and the tier policy first, and state "
            "the amount explicitly."
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "pattern": "^ORD-", "maxLength": 20},
                "amount_cents": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["order_id", "amount_cents"],
            "additionalProperties": False,
        },
        handler=issue_refund,
        mutating=True,
        privileged=True,
        examples=["refund this order", "give them their money back", "partial refund of shipping"],
    ),
    Verb.QUERY_METRICS: ToolSpec(
        verb=Verb.QUERY_METRICS,
        name="query_metrics",
        description=(
            "Aggregate over the whole dataset: counts, sums and averages, optionally grouped "
            "by region, tier, status or month, and filtered by date. Use this instead of "
            "listing rows and adding them up yourself."
        ),
        parameters={
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": [
                        "order_count",
                        "revenue_cents",
                        "avg_order_value_cents",
                        "shipping_cents",
                        "refund_total_cents",
                        "refund_count",
                        "ticket_count",
                        "customer_count",
                    ],
                },
                "group_by": {
                    "type": "string",
                    "enum": ["none", "region", "tier", "status", "month"],
                },
                "since": _DATE,
                "until": _DATE,
                "region": {"type": "string", "enum": ["EMEA", "AMER", "APAC", "LATAM"]},
                "tier": {"type": "string", "enum": ["free", "standard", "premium", "enterprise"]},
                "status": {"type": "string", "maxLength": 30},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
        handler=query_metrics,
        examples=[
            "revenue by region",
            "how many orders last month",
            "average order value for premium",
        ],
    ),
    Verb.LOOKUP_POLICY: ToolSpec(
        verb=Verb.LOOKUP_POLICY,
        name="lookup_refund_policy",
        description=(
            "Read the refund policy for a tier: the window in days, the percentage cap, and "
            "whether a support ticket is required. Read this before answering any question "
            "about whether something can be refunded."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tier": {"type": "string", "enum": ["free", "standard", "premium", "enterprise"]}
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=lookup_refund_policy,
        examples=["what is the refund window", "can a free-tier order be refunded", "policy rules"],
    ),
}
