"""opsworld: customer operations for a direct-to-consumer retailer.

The primary world. Tasks are generated, prompts are compiled and routing
thresholds are tuned here, and the results are reported here. Its counterpart,
clinicworld, is never trained or tuned on, so the gap between them is the
generalisation number.

Why retail operations: it has the four properties an evaluation domain needs
and most synthetic worlds lack.

* **A privileged action with real consequences.** Refunds move money, so
  authorisation is not decorative.
* **Rules that live in data.** The refund policy is a table, so a policy
  question has a checkable answer instead of a memorised one.
* **Natural multi-hop structure.** Name to customer to orders to policy to
  decision is four hops that a human would also take.
* **Genuine ambiguity.** Two customers share a first name; an order was already
  half refunded; a ticket references no order. These are the cases that separate
  a model that checks from a model that guesses.
"""

from __future__ import annotations

from pathlib import Path

from toolsmith.worlds._common import CALCULATOR_TOOL, TODAY_TOOL
from toolsmith.worlds.base import Entity, Verb, WorldSpec
from toolsmith.worlds.ops.policies import refund_policy
from toolsmith.worlds.ops.seed import seed_ops
from toolsmith.worlds.ops.tools import TOOLS

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

ENTITIES = [
    Entity(
        table="customers",
        label="Customer",
        description="A buying account. Tier drives every refund decision.",
        primary_key="customer_id",
        columns={
            "customer_id": "CUS-#### identifier",
            "name": "Full name; first names repeat, on purpose",
            "email": "Unique",
            "region": "EMEA, AMER, APAC or LATAM",
            "tier": "free, standard, premium or enterprise",
            "signup_date": "ISO date",
            "lifetime_value_cents": "Integer cents",
        },
    ),
    Entity(
        table="orders",
        label="Order",
        description="One purchase. Only delivered or returned orders are refundable.",
        primary_key="order_id",
        columns={
            "order_id": "ORD-#### identifier",
            "customer_id": "Owner",
            "placed_date": "ISO date",
            "status": "pending, processing, shipped, delivered, cancelled or returned",
            "total_cents": "Integer cents, including shipping",
            "shipping_cents": "Integer cents",
            "item_count": "Number of line items",
            "items": "JSON array of product names",
            "delivered_date": "ISO date, or null if not delivered",
        },
    ),
    Entity(
        table="tickets",
        label="Support ticket",
        description="A customer contact. Some tiers require one before a refund.",
        primary_key="ticket_id",
        columns={
            "ticket_id": "TIC-#### identifier",
            "customer_id": "Owner",
            "order_id": "Related order, or null for a general enquiry",
            "status": "open, pending_customer, escalated, resolved or closed",
            "priority": "low, normal, high or urgent",
        },
    ),
    Entity(
        table="refunds",
        label="Refund",
        description="A money-moving record. Several orders are already partly refunded.",
        primary_key="refund_id",
        columns={
            "refund_id": "REF-### identifier",
            "order_id": "Refunded order",
            "amount_cents": "Integer cents",
            "issued_date": "ISO date",
        },
    ),
    Entity(
        table="refund_policies",
        label="Refund policy",
        description="The rules, as data. One row per tier.",
        primary_key="policy_id",
        columns={
            "tier": "Which customers it governs",
            "window_days": "Days after delivery in which a refund is allowed",
            "max_percent": "Cap as a percentage of the order total",
            "requires_ticket": "1 if a support ticket must reference the order first",
        },
    ),
]

WORLD = WorldSpec(
    key="ops",
    title="opsworld",
    tagline="Customer operations for a direct-to-consumer retailer.",
    role="primary",
    schema_sql=SCHEMA_SQL,
    seed=seed_ops,
    entities=ENTITIES,
    tools={**TOOLS, Verb.CALCULATOR: CALCULATOR_TOOL, Verb.TODAY: TODAY_TOOL},
    policy=refund_policy,
    default_seed=20260101,
    notes=(
        "Primary domain. Trained on, tuned on, reported on. Its privileged verb is "
        "issue_refund, authorised server-side against the tier policy table."
    ),
)

__all__ = ["WORLD"]
