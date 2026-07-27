"""clinicworld: outpatient scheduling and billing. The transfer probe.

Nothing is ever trained, compiled or tuned on this world. Prompts are optimised
against opsworld and routing thresholds are learned on opsworld's validation
split; clinicworld is then run once, cold, and the drop is reported.

Why the drop is worth measuring rather than avoiding: an agent harness that only
works on the domain it was tuned for is a demo. The transferable artifact here
is the harness, not the world, and the only way to say that credibly is to show
the number on a schema the system has never seen.

The schema is deliberately not a relabelling of opsworld. Coverage is keyed by
two dimensions rather than one, the referral requirement depends on the service
band rather than the customer, and the adjustment ceiling is an outstanding
balance rather than a percentage of a headline total. A model that pattern
-matched "read the tier row" will confidently read the wrong row.
"""

from __future__ import annotations

from pathlib import Path

from toolsmith.worlds._common import CALCULATOR_TOOL, TODAY_TOOL
from toolsmith.worlds.base import Entity, Verb, WorldSpec
from toolsmith.worlds.clinic.samplers import SAMPLERS
from toolsmith.worlds.clinic.seed import seed_clinic
from toolsmith.worlds.clinic.tools import TOOLS, coverage_policy

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

ENTITIES = [
    Entity(
        table="patients",
        label="Patient",
        description="A registered person. The coverage plan is only half of a coverage decision.",
        primary_key="patient_id",
        columns={
            "patient_id": "PAT-#### identifier",
            "name": "Full name",
            "site": "Ashgrove, Belmont, Carrow or Dunmore",
            "coverage_plan": "basic, standard, extended or public",
            "balance_cents": "Integer cents outstanding across all appointments",
        },
    ),
    Entity(
        table="providers",
        label="Provider",
        description="A clinician. Some are not accepting new referrals.",
        primary_key="provider_id",
        columns={
            "provider_id": "PRV-### identifier",
            "specialty": "One of ten specialties",
            "accepting": "1 if taking new referrals",
        },
    ),
    Entity(
        table="appointments",
        label="Appointment",
        description="One visit. Only completed appointments can be billed or adjusted.",
        primary_key="appointment_id",
        columns={
            "appointment_id": "APT-#### identifier",
            "status": "scheduled, checked_in, completed, cancelled or no_show",
            "service_band": "routine, specialist or procedure. The other half of coverage.",
            "charge_cents": "Integer cents billed",
            "covered_cents": "Integer cents already met by coverage",
        },
    ),
    Entity(
        table="referrals",
        label="Referral",
        description="A routing record. Required before adjusting some service bands.",
        primary_key="referral_id",
        columns={
            "referral_id": "REF-#### identifier",
            "status": "open, awaiting_triage, accepted, declined or closed",
            "appointment_id": "Linked appointment, or null",
        },
    ),
    Entity(
        table="adjustments",
        label="Billing adjustment",
        description="A money-moving record against an appointment's outstanding balance.",
        primary_key="adjustment_id",
        columns={"adjustment_id": "ADJ-### identifier", "amount_cents": "Integer cents"},
    ),
    Entity(
        table="coverage_policies",
        label="Coverage policy",
        description="The rules, as data. Keyed by (plan, service_band): twelve rows, not four.",
        primary_key="policy_id",
        columns={
            "coverage_plan": "Which patients it governs",
            "service_band": "Which visits it governs",
            "covered_percent": "Share of the charge met by coverage",
            "claim_window_days": "Days after the appointment in which a claim is allowed",
            "requires_referral": "1 if a referral must be linked to the appointment",
        },
    ),
]

WORLD = WorldSpec(
    key="clinic",
    title="clinicworld",
    tagline="Outpatient scheduling and billing across four sites.",
    role="transfer",
    schema_sql=SCHEMA_SQL,
    seed=seed_clinic,
    entities=ENTITIES,
    tools={**TOOLS, Verb.CALCULATOR: CALCULATOR_TOOL, Verb.TODAY: TODAY_TOOL},
    samplers=SAMPLERS,
    lexicon={
        "principal": "patient",
        "principal_plural": "patients",
        "principal_id": "patient_id",
        "record": "appointment",
        "record_plural": "appointments",
        "record_id": "appointment_id",
        "case": "referral",
        "case_plural": "referrals",
        "privileged_action": "billing adjustment",
        "policy_noun": "coverage policy",
        "amount_field": "amount_cents",
        "principal_group": "coverage plan",
        "record_status_done": "completed",
    },
    policy=coverage_policy,
    default_seed=20260202,
    notes=(
        "Transfer domain. Never trained or tuned on. The ops-to-clinic gap is the "
        "generalisation result. Fully synthetic: no real patient data exists here."
    ),
)

__all__ = ["WORLD"]
