"""clinicworld's tools and its policy function.

Same eleven verbs as opsworld, bound to clinical nouns, with one structural
difference that is the entire point of this world: coverage is keyed by
``(plan, service_band)`` rather than by a single tier. An agent that learned
"read the tier row" on opsworld must actually read this schema.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from toolsmith.worlds._common import one_row, rows_to_dicts
from toolsmith.worlds.base import (
    BASE_DATE,
    PolicyDecision,
    ToolResult,
    ToolSpec,
    Verb,
    add_days,
    format_money,
    iso,
    paginate,
    parse_iso,
)

SITES = ["Ashgrove", "Belmont", "Carrow", "Dunmore"]
PLANS = ["basic", "standard", "extended", "public"]
BANDS = ["routine", "specialist", "procedure"]
APPT_STATUSES = ["scheduled", "checked_in", "completed", "cancelled", "no_show"]
REF_STATUSES = ["open", "awaiting_triage", "accepted", "declined", "closed"]


# ---------------------------------------------------------------- read tools --


def search_patients(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if len(query) < 2:
        return ToolResult.failure("invalid_arguments", "query must be at least 2 characters")
    sql = (
        "SELECT patient_id, name, email, site, coverage_plan, registered_date, balance_cents "
        "FROM patients WHERE (name LIKE ? OR email LIKE ?)"
    )
    params: list[Any] = [f"%{query}%", f"%{query}%"]
    if args.get("site"):
        sql += " AND site = ?"
        params.append(args["site"])
    if args.get("coverage_plan"):
        sql += " AND coverage_plan = ?"
        params.append(args["coverage_plan"])
    sql += " ORDER BY patient_id"
    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    limited = paginate(rows, args.get("limit"))
    return ToolResult(
        ok=True,
        data={
            "query": query,
            "match_count": len(rows),
            "returned": len(limited),
            "truncated": len(limited) < len(rows),
            "patients": limited,
        },
    )


def get_patient(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    patient_id = str(args.get("patient_id", ""))
    row = one_row(conn, "SELECT * FROM patients WHERE patient_id = ?", (patient_id,))
    if row is None:
        return ToolResult.failure(
            "not_found",
            f"no patient with id {patient_id!r}. Use search_patients to find one by name.",
        )
    counts = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(charge_cents), 0) FROM appointments WHERE patient_id = ?",
        (patient_id,),
    ).fetchone()
    return ToolResult(
        ok=True, data={**row, "appointment_count": counts[0], "charged_cents": counts[1]}
    )


def get_appointment(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    appointment_id = str(args.get("appointment_id", ""))
    row = one_row(
        conn,
        "SELECT a.*, p.name AS provider_name, p.specialty FROM appointments a "
        "JOIN providers p USING(provider_id) WHERE a.appointment_id = ?",
        (appointment_id,),
    )
    if row is None:
        return ToolResult.failure("not_found", f"no appointment with id {appointment_id!r}")
    adjusted = (
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM adjustments WHERE appointment_id = ?",
            (appointment_id,),
        ).fetchone()[0]
        or 0
    )
    return ToolResult(
        ok=True,
        data={
            **row,
            "adjusted_cents": adjusted,
            "outstanding_cents": max(0, row["charge_cents"] - row["covered_cents"] - adjusted),
        },
    )


def list_appointments(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    patient_id = str(args.get("patient_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM patients WHERE patient_id = ?", (patient_id,)) is None:
        return ToolResult.failure("not_found", f"no patient with id {patient_id!r}")
    sql = (
        "SELECT appointment_id, scheduled_date, status, reason, service_band, charge_cents, "
        "covered_cents, provider_id FROM appointments WHERE patient_id = ?"
    )
    params: list[Any] = [patient_id]
    for key, clause in (("status", "status = ?"), ("service_band", "service_band = ?")):
        if args.get(key):
            sql += f" AND {clause}"
            params.append(args[key])
    if args.get("since"):
        sql += " AND scheduled_date >= ?"
        params.append(args["since"])
    if args.get("until"):
        sql += " AND scheduled_date <= ?"
        params.append(args["until"])
    sql += " ORDER BY scheduled_date DESC, appointment_id"
    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    limited = paginate(rows, args.get("limit"))
    return ToolResult(
        ok=True,
        data={
            "patient_id": patient_id,
            "match_count": len(rows),
            "returned": len(limited),
            "truncated": len(limited) < len(rows),
            "charge_total_cents": sum(r["charge_cents"] for r in rows),
            "appointments": limited,
        },
    )


def lookup_coverage_policy(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    plan, band = args.get("coverage_plan"), args.get("service_band")
    sql = "SELECT * FROM coverage_policies"
    params: list[Any] = []
    clauses = []
    if plan:
        clauses.append("coverage_plan = ?")
        params.append(plan)
    if band:
        clauses.append("service_band = ?")
        params.append(band)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY policy_id"
    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    if not rows:
        return ToolResult.failure(
            "not_found", f"no coverage policy for plan={plan!r} band={band!r}"
        )
    citations = [r["policy_id"] for r in rows]
    if len(rows) == 1:
        return ToolResult(ok=True, data=rows[0], citations=citations)
    return ToolResult(ok=True, data={"policies": rows}, citations=citations)


METRICS: dict[str, tuple[str, str]] = {
    "appointment_count": ("COUNT(*)", "appointments"),
    "charge_cents": ("COALESCE(SUM(charge_cents), 0)", "appointments"),
    "covered_cents": ("COALESCE(SUM(covered_cents), 0)", "appointments"),
    "avg_charge_cents": ("CAST(COALESCE(AVG(charge_cents), 0) AS INTEGER)", "appointments"),
    "adjustment_total_cents": ("COALESCE(SUM(amount_cents), 0)", "adjustments"),
    "referral_count": ("COUNT(*)", "referrals"),
    "patient_count": ("COUNT(*)", "patients"),
}

GROUP_BY: dict[str, dict[str, str]] = {
    "site": {"appointments": "p.site", "patients": "site", "referrals": "p.site"},
    "coverage_plan": {
        "appointments": "p.coverage_plan",
        "patients": "coverage_plan",
        "referrals": "p.coverage_plan",
    },
    "service_band": {"appointments": "a.service_band"},
    "status": {"appointments": "a.status", "referrals": "r.status"},
    "month": {
        "appointments": "substr(a.scheduled_date, 1, 7)",
        "referrals": "substr(r.created_date, 1, 7)",
        "adjustments": "substr(d.issued_date, 1, 7)",
    },
}

_ALIAS = {"appointments": "a", "patients": "p", "referrals": "r", "adjustments": "d"}
_DATE = {
    "appointments": "a.scheduled_date",
    "referrals": "r.created_date",
    "adjustments": "d.issued_date",
    "patients": "p.registered_date",
}


def query_metrics(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    metric = str(args.get("metric", ""))
    if metric not in METRICS:
        return ToolResult.failure(
            "unknown_metric", f"{metric!r} is not a metric. Available: {', '.join(sorted(METRICS))}"
        )
    aggregate, table = METRICS[metric]
    alias = _ALIAS[table]

    group_by = args.get("group_by") or "none"
    group_expr: str | None = None
    if group_by != "none":
        options = GROUP_BY.get(group_by, {})
        if table not in options:
            return ToolResult.failure(
                "invalid_group_by",
                f"metric {metric!r} cannot be grouped by {group_by!r}. Try: "
                f"{', '.join(sorted(k for k, v in GROUP_BY.items() if table in v))}",
            )
        group_expr = options[table]

    needs_patient = (group_expr or "").startswith("p.") or bool(
        args.get("site") or args.get("coverage_plan")
    )
    join = ""
    if table == "patients":
        alias = "p"
    elif needs_patient:
        join = f" JOIN patients p ON p.patient_id = {alias}.patient_id"

    where: list[str] = []
    params: list[Any] = []
    if args.get("since"):
        where.append(f"{_DATE[table]} >= ?")
        params.append(args["since"])
    if args.get("until"):
        where.append(f"{_DATE[table]} <= ?")
        params.append(args["until"])
    if args.get("site"):
        where.append("p.site = ?")
        params.append(args["site"])
    if args.get("coverage_plan"):
        where.append("p.coverage_plan = ?")
        params.append(args["coverage_plan"])
    if args.get("service_band") and table == "appointments":
        where.append("a.service_band = ?")
        params.append(args["service_band"])

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
                "total": sum(r["value"] for r in rows) if metric != "avg_charge_cents" else None,
            },
        )
    return ToolResult(ok=True, data={"metric": metric, "value": rows[0]["value"] if rows else 0})


# --------------------------------------------------------------- write tools --


def create_referral(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    patient_id = str(args.get("patient_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM patients WHERE patient_id = ?", (patient_id,)) is None:
        return ToolResult.failure("not_found", f"no patient with id {patient_id!r}")
    appointment_id = args.get("appointment_id")
    if (
        appointment_id
        and one_row(
            conn, "SELECT 1 AS x FROM appointments WHERE appointment_id = ?", (str(appointment_id),)
        )
        is None
    ):
        return ToolResult.failure("not_found", f"no appointment with id {appointment_id!r}")

    next_id = (conn.execute("SELECT COUNT(*) FROM referrals").fetchone()[0] or 0) + 6_000
    referral_id = f"REF-{next_id}"
    while one_row(conn, "SELECT 1 AS x FROM referrals WHERE referral_id = ?", (referral_id,)):
        next_id += 1
        referral_id = f"REF-{next_id}"

    today = iso(add_days(0))
    conn.execute(
        "INSERT INTO referrals (referral_id, patient_id, appointment_id, specialty, summary, "
        "status, urgency, assignee, created_date, updated_date, outcome) "
        "VALUES (?, ?, ?, ?, ?, 'open', ?, NULL, ?, ?, '')",
        (
            referral_id,
            patient_id,
            appointment_id,
            str(args.get("specialty", "")),
            str(args.get("summary", ""))[:2000],
            str(args.get("urgency", "normal")),
            today,
            today,
        ),
    )
    return ToolResult(
        ok=True,
        data={"referral_id": referral_id, "status": "open", "created_date": today},
        mutated=True,
    )


def update_referral(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    referral_id = str(args.get("referral_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM referrals WHERE referral_id = ?", (referral_id,)) is None:
        return ToolResult.failure("not_found", f"no referral with id {referral_id!r}")
    fields = {k: args[k] for k in ("status", "urgency", "assignee", "outcome") if args.get(k)}
    if not fields:
        return ToolResult.failure(
            "invalid_arguments", "supply at least one of: status, urgency, assignee, outcome"
        )
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE referrals SET {assignments}, updated_date = ? WHERE referral_id = ?",
        (*fields.values(), iso(add_days(0)), referral_id),
    )
    return ToolResult(
        ok=True, data={"referral_id": referral_id, "updated": sorted(fields)}, mutated=True
    )


def issue_billing_adjustment(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    """Privileged. Authorisation has already happened in :func:`coverage_policy`."""
    appointment_id = str(args["appointment_id"])
    amount = int(args["amount_cents"])
    appointment = one_row(
        conn, "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
    )
    if appointment is None:  # pragma: no cover - policy already checked
        return ToolResult.failure("not_found", f"no appointment {appointment_id!r}")

    next_id = (conn.execute("SELECT COUNT(*) FROM adjustments").fetchone()[0] or 0) + 500
    adjustment_id = f"ADJ-{next_id}"
    while one_row(conn, "SELECT 1 AS x FROM adjustments WHERE adjustment_id = ?", (adjustment_id,)):
        next_id += 1
        adjustment_id = f"ADJ-{next_id}"

    conn.execute(
        "INSERT INTO adjustments (adjustment_id, appointment_id, patient_id, amount_cents, "
        "reason, issued_date, approved_by) VALUES (?, ?, ?, ?, ?, ?, 'policy_engine')",
        (
            adjustment_id,
            appointment_id,
            appointment["patient_id"],
            amount,
            str(args.get("reason", "unspecified"))[:200],
            iso(add_days(0)),
        ),
    )
    return ToolResult(
        ok=True,
        data={
            "adjustment_id": adjustment_id,
            "appointment_id": appointment_id,
            "amount_cents": amount,
            "issued_date": iso(add_days(0)),
        },
        mutated=True,
    )


# ------------------------------------------------------------------- policy --


def coverage_policy(
    conn: sqlite3.Connection, tool_name: str, args: dict[str, Any]
) -> PolicyDecision:
    """Server-side authorisation for a billing adjustment.

    Structurally harder than opsworld's refund gate: the governing row is keyed
    by two dimensions, the referral requirement depends on the band rather than
    the plan, and the ceiling is the patient's outstanding balance on that
    appointment rather than a percentage of a headline total.
    """
    if tool_name != "issue_billing_adjustment":  # pragma: no cover
        return PolicyDecision(True, "not_privileged", "no policy applies to this tool")

    appointment_id = str(args.get("appointment_id", ""))
    amount = int(args.get("amount_cents", 0) or 0)

    appointment = one_row(
        conn, "SELECT * FROM appointments WHERE appointment_id = ?", (appointment_id,)
    )
    if appointment is None:
        return PolicyDecision(
            False, "appointment_not_found", f"no appointment {appointment_id!r} exists"
        )
    if amount <= 0:
        return PolicyDecision(False, "invalid_amount", "amount must be a positive number of cents")
    if appointment["status"] != "completed":
        return PolicyDecision(
            False,
            "appointment_not_billable",
            f"appointment {appointment_id} has status {appointment['status']!r}; only completed "
            "appointments can be adjusted",
            {"status": appointment["status"]},
        )

    patient = one_row(
        conn, "SELECT * FROM patients WHERE patient_id = ?", (appointment["patient_id"],)
    )
    if patient is None:  # pragma: no cover - foreign key
        return PolicyDecision(False, "patient_not_found", "the appointment has no patient")

    policy = one_row(
        conn,
        "SELECT * FROM coverage_policies WHERE coverage_plan = ? AND service_band = ?",
        (patient["coverage_plan"], appointment["service_band"]),
    )
    if policy is None:  # pragma: no cover - the matrix is complete
        return PolicyDecision(
            False,
            "no_policy",
            f"no coverage policy for plan {patient['coverage_plan']!r} and band "
            f"{appointment['service_band']!r}",
        )

    age_days = (BASE_DATE - parse_iso(appointment["scheduled_date"])).days
    if age_days > policy["claim_window_days"]:
        return PolicyDecision(
            False,
            "outside_claim_window",
            f"the appointment was {age_days} days ago; the {patient['coverage_plan']} plan "
            f"allows {policy['claim_window_days']} days on the {appointment['service_band']} band",
            {"age_days": age_days, "claim_window_days": policy["claim_window_days"]},
        )

    if policy["requires_referral"]:
        referral = one_row(
            conn,
            "SELECT referral_id FROM referrals WHERE appointment_id = ? ORDER BY referral_id LIMIT 1",
            (appointment_id,),
        )
        if referral is None:
            return PolicyDecision(
                False,
                "referral_required",
                f"the {appointment['service_band']} band on the {patient['coverage_plan']} plan "
                f"requires a referral linked to {appointment_id}",
                {"service_band": appointment["service_band"]},
            )

    already = (
        conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM adjustments WHERE appointment_id = ?",
            (appointment_id,),
        ).fetchone()[0]
        or 0
    )
    outstanding = appointment["charge_cents"] - appointment["covered_cents"] - already
    if outstanding <= 0:
        return PolicyDecision(
            False,
            "nothing_outstanding",
            f"appointment {appointment_id} has no outstanding balance to adjust",
            {"already_adjusted_cents": already},
        )
    if amount > outstanding:
        return PolicyDecision(
            False,
            "exceeds_outstanding",
            f"{format_money(amount)} exceeds the outstanding balance of "
            f"{format_money(outstanding)}",
            {"requested_cents": amount, "outstanding_cents": outstanding},
        )

    return PolicyDecision(
        True,
        "approved",
        f"within the {policy['claim_window_days']}-day claim window and the "
        f"{format_money(outstanding)} outstanding balance",
        {
            "coverage_plan": patient["coverage_plan"],
            "service_band": appointment["service_band"],
            "policy_id": policy["policy_id"],
            "outstanding_cents": outstanding,
        },
    )


# ---------------------------------------------------------------- specs ------

_LIMIT = {"type": "integer", "description": "Maximum rows to return (1-100).", "minimum": 1}
_DATE_ARG = {"type": "string", "description": "ISO date, YYYY-MM-DD.", "maxLength": 10}

TOOLS: dict[Verb, ToolSpec] = {
    Verb.SEARCH_PRINCIPALS: ToolSpec(
        verb=Verb.SEARCH_PRINCIPALS,
        name="search_patients",
        description=(
            "Find patients by partial name or email. Use this first when the user names a "
            "person rather than giving a patient id."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 120},
                "site": {"type": "string", "enum": SITES},
                "coverage_plan": {"type": "string", "enum": PLANS},
                "limit": _LIMIT,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_patients,
        examples=["find the patient called Mira", "patients at Belmont", "who is yusuf.chen"],
    ),
    Verb.GET_PRINCIPAL: ToolSpec(
        verb=Verb.GET_PRINCIPAL,
        name="get_patient",
        description=(
            "Fetch one patient by id, including coverage plan, home site and outstanding "
            "balance. Coverage decisions need both the plan and the appointment's service band."
        ),
        parameters={
            "type": "object",
            "properties": {"patient_id": {"type": "string", "pattern": "^PAT-", "maxLength": 20}},
            "required": ["patient_id"],
            "additionalProperties": False,
        },
        handler=get_patient,
        examples=["what plan is PAT-4102 on", "patient record", "outstanding balance"],
    ),
    Verb.GET_RECORD: ToolSpec(
        verb=Verb.GET_RECORD,
        name="get_appointment",
        description=(
            "Fetch one appointment: date, status, service band, charge and covered amounts in "
            "cents, provider, and any adjustments already applied. Check adjusted_cents before "
            "proposing another adjustment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "appointment_id": {"type": "string", "pattern": "^APT-", "maxLength": 20}
            },
            "required": ["appointment_id"],
            "additionalProperties": False,
        },
        handler=get_appointment,
        examples=["details of APT-9120", "what was charged", "who was the provider"],
    ),
    Verb.LIST_RECORDS: ToolSpec(
        verb=Verb.LIST_RECORDS,
        name="list_appointments",
        description=(
            "List a patient's appointments, newest first, optionally filtered by status, "
            "service band or date range."
        ),
        parameters={
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "pattern": "^PAT-", "maxLength": 20},
                "status": {"type": "string", "enum": APPT_STATUSES},
                "service_band": {"type": "string", "enum": BANDS},
                "since": _DATE_ARG,
                "until": _DATE_ARG,
                "limit": _LIMIT,
            },
            "required": ["patient_id"],
            "additionalProperties": False,
        },
        handler=list_appointments,
        examples=["their recent visits", "completed procedures", "appointments since October"],
    ),
    Verb.CREATE_CASE: ToolSpec(
        verb=Verb.CREATE_CASE,
        name="create_referral",
        description=(
            "Open a referral for a patient, optionally linked to an appointment. Creates a "
            "record. Some coverage bands require one before billing can be adjusted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "pattern": "^PAT-", "maxLength": 20},
                "specialty": {"type": "string", "maxLength": 60},
                "summary": {"type": "string", "maxLength": 2000},
                "urgency": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "appointment_id": {"type": "string", "pattern": "^APT-", "maxLength": 20},
            },
            "required": ["patient_id", "specialty"],
            "additionalProperties": False,
        },
        handler=create_referral,
        mutating=True,
        examples=["refer them to cardiology", "raise a referral for this visit"],
    ),
    Verb.UPDATE_CASE: ToolSpec(
        verb=Verb.UPDATE_CASE,
        name="update_referral",
        description=(
            "Change a referral's status, urgency, assignee or outcome. Supply only the "
            "fields you intend to change; anything omitted is left alone."
        ),
        parameters={
            "type": "object",
            "properties": {
                "referral_id": {"type": "string", "pattern": "^REF-", "maxLength": 20},
                "status": {"type": "string", "enum": REF_STATUSES},
                "urgency": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "assignee": {"type": "string", "maxLength": 60},
                "outcome": {"type": "string", "maxLength": 500},
            },
            "required": ["referral_id"],
            "additionalProperties": False,
        },
        handler=update_referral,
        mutating=True,
        examples=["accept the referral", "mark it declined", "raise the urgency"],
    ),
    Verb.PRIVILEGED_WRITE: ToolSpec(
        verb=Verb.PRIVILEGED_WRITE,
        name="issue_billing_adjustment",
        description=(
            "Reduce a patient's outstanding balance on a completed appointment, in cents. "
            "PRIVILEGED: authorised server-side against the coverage policy for the patient's "
            "plan AND the appointment's service band, and may be refused. Read the policy and "
            "the appointment's outstanding balance first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "appointment_id": {"type": "string", "pattern": "^APT-", "maxLength": 20},
                "amount_cents": {"type": "integer", "minimum": 1},
                "reason": {"type": "string", "maxLength": 200},
            },
            "required": ["appointment_id", "amount_cents"],
            "additionalProperties": False,
        },
        handler=issue_billing_adjustment,
        mutating=True,
        privileged=True,
        examples=["waive the balance", "write off the duplicate charge", "apply a hardship credit"],
    ),
    Verb.QUERY_METRICS: ToolSpec(
        verb=Verb.QUERY_METRICS,
        name="query_metrics",
        description=(
            "Aggregate over the practice: counts and sums, optionally grouped by site, "
            "coverage plan, service band, status or month."
        ),
        parameters={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": sorted(METRICS)},
                "group_by": {
                    "type": "string",
                    "enum": ["none", "site", "coverage_plan", "service_band", "status", "month"],
                },
                "since": _DATE_ARG,
                "until": _DATE_ARG,
                "site": {"type": "string", "enum": SITES},
                "coverage_plan": {"type": "string", "enum": PLANS},
                "service_band": {"type": "string", "enum": BANDS},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
        handler=query_metrics,
        examples=["appointments by site", "total charges last month", "average charge by band"],
    ),
    Verb.LOOKUP_POLICY: ToolSpec(
        verb=Verb.LOOKUP_POLICY,
        name="lookup_coverage_policy",
        description=(
            "Read coverage rules. Keyed by BOTH the patient's plan and the appointment's "
            "service band: covered percentage, claim window in days, and whether a referral is "
            "required. Omit either argument to list the matching rows."
        ),
        parameters={
            "type": "object",
            "properties": {
                "coverage_plan": {"type": "string", "enum": PLANS},
                "service_band": {"type": "string", "enum": BANDS},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=lookup_coverage_policy,
        examples=[
            "what does the basic plan cover for a procedure",
            "claim window for specialist visits",
            "is a referral required",
        ],
    ),
}
