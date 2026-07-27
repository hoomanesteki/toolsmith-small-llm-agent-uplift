"""Populate clinicworld deterministically.

The same construction discipline as opsworld: seeded RNG, literal word pools,
integer cents, dates as offsets from BASE_DATE.

The distributions differ on purpose. Appointments cluster into a working
schedule rather than a purchase tail, charges are banded by service type rather
than log-normal, and coverage depends on two keys instead of one. A model that
generalised will handle that; a model that memorised opsworld's shape will
confidently look up the wrong row.
"""

from __future__ import annotations

import random
import sqlite3

from toolsmith.worlds._words import (
    AGENTS,
    APPOINTMENT_STATUSES,
    CLINIC_SITES,
    COVERAGE_PLANS,
    FAMILY_NAMES,
    GIVEN_NAMES,
    PRIORITIES,
    REFERRAL_STATUSES,
    SPECIALTIES,
    VISIT_REASONS,
)
from toolsmith.worlds.base import add_days, cents, iso

N_PATIENTS = 260
N_PROVIDERS = 40
N_APPOINTMENTS = 1_100
N_REFERRALS = 300
N_ADJUSTMENTS = 70

PLAN_WEIGHTS = (0.30, 0.36, 0.16, 0.18)
STATUS_WEIGHTS = (0.14, 0.05, 0.63, 0.11, 0.07)
SERVICE_BANDS = ("routine", "specialist", "procedure")
BAND_WEIGHTS = (0.58, 0.30, 0.12)

#: Typical charge in cents per service band, before coverage.
BAND_CHARGE = {
    "routine": (6_000, 14_000),
    "specialist": (18_000, 42_000),
    "procedure": (60_000, 240_000),
}

#: (plan, band) -> covered percent, claim window days, referral required
COVERAGE_MATRIX: dict[tuple[str, str], tuple[int, int, int]] = {
    ("basic", "routine"): (60, 45, 0),
    ("basic", "specialist"): (40, 45, 1),
    ("basic", "procedure"): (25, 30, 1),
    ("standard", "routine"): (80, 60, 0),
    ("standard", "specialist"): (70, 60, 1),
    ("standard", "procedure"): (55, 45, 1),
    ("extended", "routine"): (100, 90, 0),
    ("extended", "specialist"): (90, 90, 0),
    ("extended", "procedure"): (80, 90, 1),
    ("public", "routine"): (100, 180, 0),
    ("public", "specialist"): (100, 180, 1),
    ("public", "procedure"): (100, 180, 1),
}

_BODY = (
    "Claims on the {band} band under the {plan} plan are covered at {pct} percent and must be "
    "submitted within {days} days of the appointment date. A referral is {req} for this band."
)


def _weighted(rng: random.Random, options: tuple[str, ...], weights: tuple[float, ...]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def seed_clinic(conn: sqlite3.Connection, seed: int) -> None:
    rng = random.Random(seed)

    # -- coverage policies --------------------------------------------------
    policies = []
    for i, ((plan, band), (pct, days, referral)) in enumerate(sorted(COVERAGE_MATRIX.items())):
        policies.append(
            (
                f"COV-{100 + i}",
                plan,
                band,
                pct,
                days,
                referral,
                _BODY.format(
                    band=band,
                    plan=plan,
                    pct=pct,
                    days=days,
                    req="required" if referral else "not required",
                ),
            )
        )
    conn.executemany(
        "INSERT INTO coverage_policies (policy_id, coverage_plan, service_band, "
        "covered_percent, claim_window_days, requires_referral, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        policies,
    )

    # -- providers ----------------------------------------------------------
    providers = [
        (
            f"PRV-{300 + i}",
            f"Dr {rng.choice(GIVEN_NAMES)} {rng.choice(FAMILY_NAMES)}",
            rng.choice(SPECIALTIES),
            rng.choice(CLINIC_SITES),
            int(rng.random() < 0.82),
        )
        for i in range(N_PROVIDERS)
    ]
    conn.executemany(
        "INSERT INTO providers (provider_id, name, specialty, site, accepting) VALUES (?, ?, ?, ?, ?)",
        providers,
    )
    provider_ids = [p[0] for p in providers]

    # -- patients -----------------------------------------------------------
    patients = []
    used: set[str] = set()
    for i in range(N_PATIENTS):
        given, family = rng.choice(GIVEN_NAMES), rng.choice(FAMILY_NAMES)
        stem = f"{given}.{family}".lower()
        email = f"{stem}@patients.example"
        n = 2
        while email in used:
            email = f"{stem}{n}@patients.example"
            n += 1
        used.add(email)
        patients.append(
            (
                f"PAT-{4000 + i}",
                f"{given} {family}",
                email,
                rng.choice(CLINIC_SITES),
                _weighted(rng, COVERAGE_PLANS, PLAN_WEIGHTS),
                iso(add_days(-rng.randint(60, 2_000))),
                cents(max(0.0, rng.gauss(40, 90))),
                "",
            )
        )
    conn.executemany(
        "INSERT INTO patients (patient_id, name, email, site, coverage_plan, "
        "registered_date, balance_cents, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        patients,
    )
    patient_ids = [p[0] for p in patients]
    plan_by_patient = {p[0]: p[4] for p in patients}

    # -- appointments -------------------------------------------------------
    appointments = []
    for i in range(N_APPOINTMENTS):
        patient_id = rng.choice(patient_ids)
        band = _weighted(rng, SERVICE_BANDS, BAND_WEIGHTS)
        low, high = BAND_CHARGE[band]
        charge = rng.randrange(low, high, 25)
        status = _weighted(rng, APPOINTMENT_STATUSES, STATUS_WEIGHTS)
        pct = COVERAGE_MATRIX[(plan_by_patient[patient_id], band)][0]
        covered = charge * pct // 100 if status == "completed" else 0
        appointments.append(
            (
                f"APT-{9000 + i}",
                patient_id,
                rng.choice(provider_ids),
                iso(add_days(-rng.randint(0, 300))),
                status,
                rng.choice(VISIT_REASONS),
                band,
                charge,
                covered,
                "",
            )
        )
    conn.executemany(
        "INSERT INTO appointments (appointment_id, patient_id, provider_id, scheduled_date, "
        "status, reason, service_band, charge_cents, covered_cents, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        appointments,
    )
    completed = [a for a in appointments if a[4] == "completed"]

    # -- referrals ----------------------------------------------------------
    referrals = []
    for i in range(N_REFERRALS):
        if rng.random() < 0.75 and appointments:
            appointment = rng.choice(appointments)
            appointment_id, patient_id = appointment[0], appointment[1]
        else:
            appointment_id, patient_id = None, rng.choice(patient_ids)
        created = -rng.randint(0, 240)
        status = rng.choice(REFERRAL_STATUSES)
        referrals.append(
            (
                f"REF-{6000 + i}",
                patient_id,
                appointment_id,
                rng.choice(SPECIALTIES),
                f"Referral for {rng.choice(VISIT_REASONS)}.",
                status,
                rng.choice(PRIORITIES),
                rng.choice(AGENTS) if status != "open" else None,
                iso(add_days(created)),
                iso(add_days(min(0, created + rng.randint(0, 21)))),
                "Triaged." if status in {"accepted", "declined", "closed"} else "",
            )
        )
    conn.executemany(
        "INSERT INTO referrals (referral_id, patient_id, appointment_id, specialty, summary, "
        "status, urgency, assignee, created_date, updated_date, outcome) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        referrals,
    )

    # -- historical adjustments --------------------------------------------
    adjustments = []
    for i, appointment in enumerate(rng.sample(completed, min(N_ADJUSTMENTS, len(completed)))):
        appointment_id, patient_id, _, _, _, _, _, charge, covered, _ = appointment
        outstanding = max(1, charge - covered)
        adjustments.append(
            (
                f"ADJ-{500 + i}",
                appointment_id,
                patient_id,
                outstanding if rng.random() < 0.5 else outstanding // 2,
                rng.choice(["billing error", "hardship waiver", "duplicate charge", "goodwill"]),
                iso(add_days(-rng.randint(1, 150))),
                "policy_engine",
            )
        )
    conn.executemany(
        "INSERT INTO adjustments (adjustment_id, appointment_id, patient_id, amount_cents, "
        "reason, issued_date, approved_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        adjustments,
    )
