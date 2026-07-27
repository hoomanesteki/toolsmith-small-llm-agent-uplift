"""Populate opsworld deterministically.

Every value here comes from ``random.Random(seed)`` and the literal word pools
in ``worlds/_words.py``. No network, no ``faker``, no wall clock. Build twice
and the digest is identical, which is the precondition for treating anything
downstream as ground truth.

The distributions are chosen so the world is interesting rather than uniform:
a realistic tier mix, a long tail of order values, some customers with many
orders and most with few, and a deliberate scattering of edge cases (orders on
the exact refund-window boundary, customers with identical first names, tickets
with no order attached) because those are where cheap models fail and where the
harness earns its keep.
"""

from __future__ import annotations

import json
import random
import sqlite3

from toolsmith.worlds._words import (
    AGENTS,
    CARRIERS,
    FAMILY_NAMES,
    GIVEN_NAMES,
    ORDER_STATUSES,
    PRIORITIES,
    PRODUCTS,
    REGIONS,
    TICKET_STATUSES,
    TICKET_SUBJECTS,
    TIERS,
)
from toolsmith.worlds.base import add_days, cents, iso

N_CUSTOMERS = 320
N_ORDERS = 1_400
N_TICKETS = 420
N_REFUNDS = 90

#: Tier mix. Most customers are on the free plan; enterprise is rare and
#: expensive, which is what makes tier-conditional policy questions non-trivial.
TIER_WEIGHTS = (0.42, 0.34, 0.18, 0.06)

#: Status mix. Most orders complete; the tail is where the work is.
STATUS_WEIGHTS = (0.06, 0.09, 0.14, 0.58, 0.08, 0.05)

REFUND_POLICIES = [
    (
        "POL-FREE",
        "free",
        14,
        50,
        1,
        "Free-tier orders may be refunded within 14 days of the delivery date, up to 50 "
        "percent of the order total, and only when an open support ticket references the "
        "order. Shipping is never refundable on this tier.",
    ),
    (
        "POL-STD",
        "standard",
        30,
        100,
        1,
        "Standard-tier orders may be refunded in full within 30 days of the delivery date, "
        "provided a support ticket references the order. Shipping is refundable only when "
        "the order arrived damaged.",
    ),
    (
        "POL-PREM",
        "premium",
        60,
        100,
        0,
        "Premium-tier orders may be refunded in full within 60 days of the delivery date. "
        "No support ticket is required. Shipping is always refundable.",
    ),
    (
        "POL-ENT",
        "enterprise",
        90,
        100,
        0,
        "Enterprise agreements permit a full refund within 90 days of the delivery date, "
        "including shipping. Refunds above 100,000 cents are reported to the account team "
        "but are not blocked.",
    ),
]


def _weighted(rng: random.Random, options: tuple[str, ...], weights: tuple[float, ...]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def seed_ops(conn: sqlite3.Connection, seed: int) -> None:
    rng = random.Random(seed)

    # -- policies -----------------------------------------------------------
    conn.executemany(
        "INSERT INTO refund_policies (policy_id, tier, window_days, max_percent, "
        "requires_ticket, body) VALUES (?, ?, ?, ?, ?, ?)",
        REFUND_POLICIES,
    )

    # -- customers ----------------------------------------------------------
    customers: list[tuple] = []
    used_emails: set[str] = set()
    for i in range(N_CUSTOMERS):
        given = rng.choice(GIVEN_NAMES)
        family = rng.choice(FAMILY_NAMES)
        name = f"{given} {family}"
        stem = f"{given}.{family}".lower()
        email = f"{stem}@example.net"
        suffix = 2
        while email in used_emails:
            email = f"{stem}{suffix}@example.net"
            suffix += 1
        used_emails.add(email)

        customers.append(
            (
                f"CUS-{1000 + i}",
                name,
                email,
                rng.choice(REGIONS),
                _weighted(rng, TIERS, TIER_WEIGHTS),
                iso(add_days(-rng.randint(30, 1_400))),
                cents(rng.lognormvariate(5.6, 0.9)),
                "",
            )
        )
    conn.executemany(
        "INSERT INTO customers (customer_id, name, email, region, tier, signup_date, "
        "lifetime_value_cents, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        customers,
    )
    customer_ids = [c[0] for c in customers]

    # A power-law-ish assignment: a few customers place many orders.
    weights = [rng.paretovariate(1.4) for _ in customer_ids]

    # -- orders -------------------------------------------------------------
    orders: list[tuple] = []
    for i in range(N_ORDERS):
        customer_id = rng.choices(customer_ids, weights=weights, k=1)[0]
        placed_offset = -rng.randint(1, 365)
        status = _weighted(rng, ORDER_STATUSES, STATUS_WEIGHTS)
        item_count = rng.choices([1, 2, 3, 4, 5], weights=[0.45, 0.25, 0.15, 0.10, 0.05])[0]
        items = rng.sample(PRODUCTS, item_count)
        subtotal = sum(cents(rng.lognormvariate(3.4, 0.7)) for _ in items)
        shipping = 0 if subtotal > 7_500 else cents(rng.choice([4.95, 6.95, 9.95]))
        delivered = None
        carrier = None
        if status in {"shipped", "delivered", "returned"}:
            carrier = rng.choice(CARRIERS)
        if status in {"delivered", "returned"}:
            # Clamped: nothing in a world may be dated after its own clock.
            delivered = iso(add_days(min(0, placed_offset + rng.randint(2, 11))))

        orders.append(
            (
                f"ORD-{5000 + i}",
                customer_id,
                iso(add_days(placed_offset)),
                status,
                subtotal + shipping,
                shipping,
                item_count,
                json.dumps(items),
                carrier,
                delivered,
                "",
            )
        )
    conn.executemany(
        "INSERT INTO orders (order_id, customer_id, placed_date, status, total_cents, "
        "shipping_cents, item_count, items, carrier, delivered_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        orders,
    )
    delivered_orders = [o for o in orders if o[3] == "delivered"]

    # -- tickets ------------------------------------------------------------
    tickets: list[tuple] = []
    for i in range(N_TICKETS):
        # 80% of tickets reference an order; the rest are general enquiries, which
        # is the case that trips models into inventing an order id.
        if rng.random() < 0.80 and orders:
            order = rng.choice(orders)
            order_id, customer_id = order[0], order[1]
        else:
            order_id, customer_id = None, rng.choice(customer_ids)
        created_offset = -rng.randint(0, 200)
        status = rng.choice(TICKET_STATUSES)
        tickets.append(
            (
                f"TIC-{2000 + i}",
                customer_id,
                order_id,
                rng.choice(TICKET_SUBJECTS),
                "Customer contacted support through the web form.",
                status,
                rng.choice(PRIORITIES),
                rng.choice(AGENTS) if status != "open" else None,
                iso(add_days(created_offset)),
                iso(add_days(min(0, created_offset + rng.randint(0, 14)))),
                "Resolved by agent." if status in {"resolved", "closed"} else "",
            )
        )
    conn.executemany(
        "INSERT INTO tickets (ticket_id, customer_id, order_id, subject, body, status, "
        "priority, assignee, created_date, updated_date, resolution) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tickets,
    )

    # -- historical refunds -------------------------------------------------
    # Some orders are already fully refunded. A model that does not check will
    # cheerfully refund them twice, which the state diff catches and no judge would.
    refunds: list[tuple] = []
    for i, order in enumerate(rng.sample(delivered_orders, min(N_REFUNDS, len(delivered_orders)))):
        order_id, customer_id, _, _, total, shipping, *_ = order
        amount = total if rng.random() < 0.6 else total // 2
        refunds.append(
            (
                f"REF-{700 + i}",
                order_id,
                customer_id,
                amount,
                rng.choice(["damaged on arrival", "wrong item", "late delivery", "goodwill"]),
                iso(add_days(-rng.randint(1, 120))),
                "policy_engine",
            )
        )
    conn.executemany(
        "INSERT INTO refunds (refund_id, order_id, customer_id, amount_cents, reason, "
        "issued_date, approved_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        refunds,
    )
