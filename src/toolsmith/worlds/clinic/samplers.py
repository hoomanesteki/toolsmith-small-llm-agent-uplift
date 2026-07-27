"""Row samplers for clinicworld.

Same nine names as opsworld, different domain knowledge behind them. A blocked
adjustment here is an appointment outside its (plan, band) claim window, or one
whose band requires a referral that does not exist, or one with nothing left
outstanding. None of those concepts exist in the retail world, which is exactly
why the sampler contract is per-domain and the task templates are not.
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
    row = _pick(conn, rng, "SELECT * FROM patients ORDER BY patient_id")
    if row is None:
        return None
    return {
        "id": row["patient_id"],
        "name": row["name"],
        "group": row["coverage_plan"],
        "email": row["email"],
        "region": row["site"],
        "raw": row,
    }


def record(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM appointments ORDER BY appointment_id")
    if row is None:
        return None
    return {
        "id": row["appointment_id"],
        "principal_id": row["patient_id"],
        "status": row["status"],
        "amount_cents": row["charge_cents"],
        "date": row["scheduled_date"],
        "raw": row,
    }


def record_done(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(
        conn, rng, "SELECT * FROM appointments WHERE status = 'completed' ORDER BY appointment_id"
    )
    if row is None:
        return None
    return {
        "id": row["appointment_id"],
        "principal_id": row["patient_id"],
        "status": row["status"],
        "amount_cents": row["charge_cents"],
        "date": row["scheduled_date"],
        "raw": row,
    }


def privileged_allowed(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(
        conn,
        rng,
        "SELECT a.*, p.coverage_plan, "
        "(a.charge_cents - a.covered_cents - COALESCE("
        "  (SELECT SUM(d.amount_cents) FROM adjustments d "
        "   WHERE d.appointment_id = a.appointment_id), 0)) AS outstanding "
        "FROM appointments a JOIN patients p USING(patient_id) "
        "JOIN coverage_policies c ON c.coverage_plan = p.coverage_plan "
        "AND c.service_band = a.service_band "
        "WHERE a.status = 'completed' AND c.requires_referral = 0 "
        "AND julianday(?) - julianday(a.scheduled_date) <= c.claim_window_days "
        "AND outstanding > 0 ORDER BY a.appointment_id",
        (TODAY,),
    )
    if row is None:
        return None
    return {
        "record_id": row["appointment_id"],
        "principal_id": row["patient_id"],
        "principal_group": row["coverage_plan"],
        "amount_cents": row["outstanding"],
        "arguments": {
            "appointment_id": row["appointment_id"],
            "amount_cents": row["outstanding"],
            "reason": "billing error",
        },
        "raw": row,
    }


BLOCKED_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "appointment_not_billable",
        "SELECT a.*, p.coverage_plan FROM appointments a JOIN patients p USING(patient_id) "
        "WHERE a.status IN ('scheduled', 'cancelled', 'no_show') ORDER BY a.appointment_id",
        "the appointment was never completed",
    ),
    (
        "outside_claim_window",
        "SELECT a.*, p.coverage_plan FROM appointments a JOIN patients p USING(patient_id) "
        "JOIN coverage_policies c ON c.coverage_plan = p.coverage_plan "
        "AND c.service_band = a.service_band WHERE a.status = 'completed' "
        "AND julianday('{today}') - julianday(a.scheduled_date) > c.claim_window_days "
        "ORDER BY a.appointment_id",
        "the claim window has closed",
    ),
    (
        "referral_required",
        "SELECT a.*, p.coverage_plan FROM appointments a JOIN patients p USING(patient_id) "
        "JOIN coverage_policies c ON c.coverage_plan = p.coverage_plan "
        "AND c.service_band = a.service_band WHERE a.status = 'completed' "
        "AND c.requires_referral = 1 "
        "AND julianday('{today}') - julianday(a.scheduled_date) <= c.claim_window_days "
        "AND NOT EXISTS (SELECT 1 FROM referrals r WHERE r.appointment_id = a.appointment_id) "
        "ORDER BY a.appointment_id",
        "the service band requires a referral and none is linked",
    ),
    (
        "nothing_outstanding",
        "SELECT a.*, p.coverage_plan FROM appointments a JOIN patients p USING(patient_id) "
        "WHERE a.status = 'completed' AND a.charge_cents <= a.covered_cents "
        "ORDER BY a.appointment_id",
        "there is no outstanding balance left to adjust",
    ),
)


def privileged_blocked(
    conn: sqlite3.Connection, rng: random.Random, kwargs: dict[str, Any]
) -> dict[str, Any] | None:
    wanted = kwargs.get("code")
    cases = [c for c in BLOCKED_CASES if wanted is None or c[0] == wanted]
    rng.shuffle(cases)
    for code, sql, phrase in cases:
        row = _pick(conn, rng, sql.format(today=TODAY))
        if row is None:
            continue
        return {
            "record_id": row["appointment_id"],
            "principal_id": row["patient_id"],
            "principal_group": row["coverage_plan"],
            "code": code,
            "phrase": phrase,
            "arguments": {
                "appointment_id": row["appointment_id"],
                "amount_cents": max(1, row["charge_cents"]),
                "reason": "patient requested",
            },
            "raw": row,
        }
    return None


def ambiguous_name(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    rows = rows_to_dicts(
        conn.execute(
            "SELECT substr(name, 1, instr(name, ' ') - 1) AS given, COUNT(*) AS n "
            "FROM patients GROUP BY given HAVING n >= 2 ORDER BY given"
        )
    )
    if not rows:
        return None
    row = rng.choice(rows)
    matches = rows_to_dicts(
        conn.execute(
            "SELECT patient_id, name, coverage_plan FROM patients WHERE name LIKE ? "
            "ORDER BY patient_id",
            (f"{row['given']} %",),
        )
    )
    return {"query": row["given"], "match_count": row["n"], "matches": matches}


def missing_record_id(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    for _attempt in range(50):
        candidate = f"APT-{rng.randint(90_000, 99_999)}"
        if (
            conn.execute(
                "SELECT 1 FROM appointments WHERE appointment_id = ?", (candidate,)
            ).fetchone()
            is None
        ):
            return {"id": candidate, "kind": "appointment"}
    return None  # pragma: no cover


def policy_question(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM coverage_policies ORDER BY policy_id")
    if row is None:
        return None
    field, phrase, value = rng.choice(
        [
            (
                "covered_percent",
                "what percentage of the charge is covered",
                str(row["covered_percent"]),
            ),
            (
                "claim_window_days",
                "how many days there are to submit a claim",
                str(row["claim_window_days"]),
            ),
            (
                "requires_referral",
                "whether a referral is required",
                "yes" if row["requires_referral"] else "no",
            ),
        ]
    )
    return {
        "arguments": {"coverage_plan": row["coverage_plan"], "service_band": row["service_band"]},
        "subject": f"{row['coverage_plan']} plan on the {row['service_band']} band",
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
            ("appointment_count", "site", "appointments"),
            ("appointment_count", "status", "appointments"),
            ("appointment_count", "service_band", "appointments"),
            ("charge_cents", "coverage_plan", "charges in cents"),
            ("charge_cents", "site", "charges in cents"),
            ("patient_count", "coverage_plan", "patients"),
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
