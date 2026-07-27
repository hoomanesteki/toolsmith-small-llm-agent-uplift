"""Row samplers for opsworld.

Templates ask for "arguments the policy will refuse" and this file knows that
means an order outside its tier's refund window, or already refunded, or never
delivered. That knowledge is genuinely domain-specific, so it lives with the
domain rather than leaking into the task generator.

Every sampler returns ``None`` rather than raising when the seed happens to
contain no suitable row. The generator counts those misses and reports them, so
a template that silently produces nothing shows up as a number instead of an
absence.
"""

from __future__ import annotations

import random
import sqlite3
from typing import Any

from toolsmith.worlds._common import rows_to_dicts
from toolsmith.worlds.base import BASE_DATE, SamplerFn

TODAY = BASE_DATE.isoformat()


def _pick(
    conn: sqlite3.Connection, rng: random.Random, sql: str, params: tuple = ()
) -> dict[str, Any] | None:
    rows = rows_to_dicts(conn.execute(sql, params))
    return rng.choice(rows) if rows else None


def principal(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM customers ORDER BY customer_id")
    if row is None:
        return None
    return {
        "id": row["customer_id"],
        "name": row["name"],
        "group": row["tier"],
        "email": row["email"],
        "region": row["region"],
        "raw": row,
    }


def record(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM orders ORDER BY order_id")
    if row is None:
        return None
    return {
        "id": row["order_id"],
        "principal_id": row["customer_id"],
        "status": row["status"],
        "amount_cents": row["total_cents"],
        "date": row["placed_date"],
        "raw": row,
    }


def record_done(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM orders WHERE status = 'delivered' ORDER BY order_id")
    if row is None:
        return None
    return {
        "id": row["order_id"],
        "principal_id": row["customer_id"],
        "status": row["status"],
        "amount_cents": row["total_cents"],
        "date": row["delivered_date"],
        "raw": row,
    }


def privileged_allowed(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """An order the refund policy will approve: delivered, inside the window,
    not already refunded, and on a tier that does not require a ticket."""
    row = _pick(
        conn,
        rng,
        "SELECT o.*, c.tier, p.max_percent FROM orders o "
        "JOIN customers c USING(customer_id) JOIN refund_policies p ON p.tier = c.tier "
        "WHERE o.status = 'delivered' AND p.requires_ticket = 0 "
        "AND julianday(?) - julianday(o.delivered_date) <= p.window_days "
        "AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id = o.order_id) "
        "ORDER BY o.order_id",
        (TODAY,),
    )
    if row is None:
        return None
    ceiling = row["total_cents"] * row["max_percent"] // 100
    return {
        "record_id": row["order_id"],
        "principal_id": row["customer_id"],
        "principal_group": row["tier"],
        "amount_cents": ceiling,
        "arguments": {
            "order_id": row["order_id"],
            "amount_cents": ceiling,
            "reason": "arrived damaged",
        },
        "raw": row,
    }


#: Reason codes the policy can return, with a human phrase for the prompt.
BLOCKED_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "order_not_eligible",
        "SELECT o.*, c.tier FROM orders o JOIN customers c USING(customer_id) "
        "WHERE o.status IN ('pending', 'processing') ORDER BY o.order_id",
        "it was never delivered",
    ),
    (
        "outside_refund_window",
        "SELECT o.*, c.tier FROM orders o JOIN customers c USING(customer_id) "
        "JOIN refund_policies p ON p.tier = c.tier WHERE o.status = 'delivered' "
        "AND julianday('{today}') - julianday(o.delivered_date) > p.window_days "
        "ORDER BY o.order_id",
        "the refund window has closed",
    ),
    (
        "already_refunded",
        "SELECT o.*, c.tier FROM orders o JOIN customers c USING(customer_id) "
        "JOIN refunds r ON r.order_id = o.order_id JOIN refund_policies p ON p.tier = c.tier "
        "GROUP BY o.order_id HAVING SUM(r.amount_cents) >= o.total_cents * p.max_percent / 100 "
        "ORDER BY o.order_id",
        "it has already been refunded in full",
    ),
    (
        "ticket_required",
        "SELECT o.*, c.tier FROM orders o JOIN customers c USING(customer_id) "
        "JOIN refund_policies p ON p.tier = c.tier WHERE o.status = 'delivered' "
        "AND p.requires_ticket = 1 AND julianday('{today}') - julianday(o.delivered_date) "
        "<= p.window_days AND NOT EXISTS "
        "(SELECT 1 FROM tickets t WHERE t.order_id = o.order_id) ORDER BY o.order_id",
        "no support ticket references it and the tier requires one",
    ),
)


def privileged_blocked(
    conn: sqlite3.Connection, rng: random.Random, kwargs: dict[str, Any]
) -> dict[str, Any] | None:
    """Arguments the policy will refuse, with the reason the refusal is correct."""
    wanted = kwargs.get("code")
    cases = [c for c in BLOCKED_CASES if wanted is None or c[0] == wanted]
    rng.shuffle(cases)
    for code, sql, phrase in cases:
        row = _pick(conn, rng, sql.format(today=TODAY))
        if row is None:
            continue
        return {
            "record_id": row["order_id"],
            "principal_id": row["customer_id"],
            "principal_group": row["tier"],
            "code": code,
            "phrase": phrase,
            "arguments": {
                "order_id": row["order_id"],
                "amount_cents": max(1, row["total_cents"]),
                "reason": "customer requested",
            },
            "raw": row,
        }
    return None


def ambiguous_name(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """A given name shared by two or more customers.

    The correct behaviour is to ask which one, not to pick the first. Models
    that pick the first are the reason this tier exists.
    """
    rows = rows_to_dicts(
        conn.execute(
            "SELECT substr(name, 1, instr(name, ' ') - 1) AS given, COUNT(*) AS n "
            "FROM customers GROUP BY given HAVING n >= 2 ORDER BY given"
        )
    )
    if not rows:
        return None
    row = rng.choice(rows)
    matches = rows_to_dicts(
        conn.execute(
            "SELECT customer_id, name, tier FROM customers WHERE name LIKE ? ORDER BY customer_id",
            (f"{row['given']} %",),
        )
    )
    return {"query": row["given"], "match_count": row["n"], "matches": matches}


def missing_record_id(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """An id that is well-formed and absent. Shape is right, referent is not."""
    for _attempt in range(50):
        candidate = f"ORD-{rng.randint(90_000, 99_999)}"
        if conn.execute("SELECT 1 FROM orders WHERE order_id = ?", (candidate,)).fetchone() is None:
            return {"id": candidate, "kind": "order"}
    return None  # pragma: no cover - the id space is far larger than the table


def policy_question(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM refund_policies ORDER BY policy_id")
    if row is None:
        return None
    field, phrase, value = rng.choice(
        [
            (
                "window_days",
                "how many days after delivery a refund can be requested",
                str(row["window_days"]),
            ),
            (
                "max_percent",
                "the maximum percentage of the order total that can be refunded",
                str(row["max_percent"]),
            ),
            (
                "requires_ticket",
                "whether a support ticket is required first",
                "yes" if row["requires_ticket"] else "no",
            ),
        ]
    )
    return {
        "arguments": {"tier": row["tier"]},
        "subject": row["tier"],
        "field": field,
        "phrase": phrase,
        "value": value,
        "citation": row["policy_id"],
    }


def metric(
    _conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """Metrics need no row: the aggregate is computed by the tool itself."""
    choice = rng.choice(
        [
            ("order_count", "region", "orders"),
            ("order_count", "status", "orders"),
            ("revenue_cents", "region", "revenue in cents"),
            ("revenue_cents", "tier", "revenue in cents"),
            ("ticket_count", "status", "support tickets"),
            ("customer_count", "tier", "customers"),
        ]
    )
    return {"metric": choice[0], "group_by": choice[1], "noun": choice[2]}


SAMPLERS: dict[str, SamplerFn] = {
    "principal": principal,
    "record": record,
    "record_done": record_done,
    "privileged_allowed": privileged_allowed,
    "privileged_blocked": privileged_blocked,
    "ambiguous_name": ambiguous_name,
    "missing_record_id": missing_record_id,
    "policy_question": policy_question,
    "metric": metric,
}
