"""Server-side authorisation for opsworld's privileged verb.

The rule this file encodes, and the one worth saying in an interview:

    The model's opinion about whether a refund is allowed is an input to
    logging, never to authorisation.

The agent asks for a refund. This function decides. It runs after the request
and before the mutation, it reads the world's own policy table rather than a
constant, and its verdict is the only thing that can permit the write. A model
that has been convinced by an injected instruction to issue a refund still does
not get one.

Every denial returns a machine-readable code and a human-readable reason, so
the executor can recover and the failure gallery can group by cause.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toolsmith.worlds._common import one_row
from toolsmith.worlds.base import BASE_DATE, PolicyDecision, format_money, parse_iso

#: Refunds at or above this figure are recorded for the account team. They are
#: not blocked: an alert that fires on every large refund is an alert nobody reads.
LARGE_REFUND_CENTS = 100_000


def refund_policy(conn: sqlite3.Connection, tool_name: str, args: dict[str, Any]) -> PolicyDecision:
    if tool_name != "issue_refund":  # pragma: no cover - the sandbox routes by name
        return PolicyDecision(True, "not_privileged", "no policy applies to this tool")

    order_id = str(args.get("order_id", ""))
    amount = int(args.get("amount_cents", 0) or 0)

    order = one_row(conn, "SELECT * FROM orders WHERE order_id = ?", (order_id,))
    if order is None:
        return PolicyDecision(False, "order_not_found", f"no order {order_id!r} exists")

    if amount <= 0:
        return PolicyDecision(
            False, "invalid_amount", "refund amount must be a positive number of cents"
        )

    if order["status"] not in {"delivered", "returned"}:
        return PolicyDecision(
            False,
            "order_not_eligible",
            f"order {order_id} has status {order['status']!r}; only delivered or returned "
            "orders can be refunded",
            {"status": order["status"]},
        )

    customer = one_row(
        conn, "SELECT * FROM customers WHERE customer_id = ?", (order["customer_id"],)
    )
    if customer is None:  # pragma: no cover - foreign key makes this unreachable
        return PolicyDecision(False, "customer_not_found", "the order has no customer")

    policy = one_row(conn, "SELECT * FROM refund_policies WHERE tier = ?", (customer["tier"],))
    if policy is None:  # pragma: no cover - every tier has a policy row
        return PolicyDecision(
            False, "no_policy", f"no refund policy is defined for tier {customer['tier']!r}"
        )

    # -- the window ---------------------------------------------------------
    delivered = order["delivered_date"]
    if delivered:
        age_days = (BASE_DATE - parse_iso(delivered)).days
        if age_days > policy["window_days"]:
            return PolicyDecision(
                False,
                "outside_refund_window",
                f"delivered {age_days} days ago; the {customer['tier']} tier allows "
                f"{policy['window_days']} days",
                {"age_days": age_days, "window_days": policy["window_days"]},
            )

    # -- the cap, net of refunds already issued ----------------------------
    already = (
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE order_id = ?", (order_id,)
        ).fetchone()[0]
        or 0
    )
    ceiling = order["total_cents"] * policy["max_percent"] // 100
    if already >= ceiling:
        return PolicyDecision(
            False,
            "already_refunded",
            f"order {order_id} has already been refunded {format_money(already)} of a "
            f"{format_money(ceiling)} ceiling",
            {"already_refunded_cents": already, "ceiling_cents": ceiling},
        )
    if already + amount > ceiling:
        return PolicyDecision(
            False,
            "exceeds_policy_cap",
            f"{format_money(amount)} would take total refunds to "
            f"{format_money(already + amount)}, over the {policy['max_percent']} percent "
            f"ceiling of {format_money(ceiling)} for the {customer['tier']} tier",
            {
                "requested_cents": amount,
                "already_refunded_cents": already,
                "ceiling_cents": ceiling,
            },
        )

    # -- the ticket requirement --------------------------------------------
    if policy["requires_ticket"]:
        ticket = one_row(
            conn,
            "SELECT ticket_id FROM tickets WHERE order_id = ? ORDER BY ticket_id LIMIT 1",
            (order_id,),
        )
        if ticket is None:
            return PolicyDecision(
                False,
                "ticket_required",
                f"the {customer['tier']} tier requires a support ticket referencing "
                f"{order_id} before a refund can be issued",
                {"tier": customer["tier"]},
            )

    return PolicyDecision(
        True,
        "approved",
        f"within the {policy['window_days']}-day window and the "
        f"{policy['max_percent']} percent cap for the {customer['tier']} tier",
        {
            "tier": customer["tier"],
            "policy_id": policy["policy_id"],
            "already_refunded_cents": already,
            "ceiling_cents": ceiling,
            "large_refund": amount >= LARGE_REFUND_CENTS,
        },
    )
