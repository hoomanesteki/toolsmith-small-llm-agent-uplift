"""Row samplers for docworld.

The nine shared names, plus one this world alone provides: ``grounded_fact``,
which draws a question whose answer is a column and whose correct citation is a
specific section id. That sampler is what makes the grounding tier checkable
rather than judged.
"""

from __future__ import annotations

import random
import sqlite3
from typing import Any

from toolsmith.worlds._common import rows_to_dicts
from toolsmith.worlds.base import BASE_DATE, SamplerFn

TODAY = BASE_DATE.isoformat()

#: fact_key -> how a person would ask about it.
FACT_QUESTIONS: dict[str, tuple[str, str]] = {
    "retention_days": ("retention period", "how many days records are retained for"),
    "sla_hours": ("response commitment", "how many hours the response commitment is"),
    "review_cycle_months": ("review cycle", "how many months pass between reviews"),
    "escalation_tier": ("escalation tier", "which tier unresolved incidents escalate to"),
    "max_batch_size": ("batch limit", "the largest batch size accepted"),
    "encryption_standard": ("encryption standard", "which encryption standard is used at rest"),
}


def _pick(
    conn: sqlite3.Connection, rng: random.Random, sql: str, params: tuple = ()
) -> dict[str, Any] | None:
    rows = rows_to_dicts(conn.execute(sql, params))
    return rng.choice(rows) if rows else None


def principal(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM services ORDER BY service_id")
    if row is None:
        return None
    return {
        "id": row["service_id"],
        "name": row["name"],
        "group": row["category"],
        "email": row["owner_team"],
        "region": row["category"],
        "raw": row,
    }


def record(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM documents ORDER BY doc_id")
    if row is None:
        return None
    return {
        "id": row["doc_id"],
        "principal_id": row["service_id"],
        "status": row["status"],
        "amount_cents": 0,
        "date": row["published_date"],
        "raw": row,
    }


def record_done(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM documents WHERE status = 'current' ORDER BY doc_id")
    if row is None:
        return None
    return {
        "id": row["doc_id"],
        "principal_id": row["service_id"],
        "status": row["status"],
        "amount_cents": 0,
        "date": row["published_date"],
        "raw": row,
    }


def privileged_allowed(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """A draft that may legitimately be published: fresh, and reviewed if its
    kind demands review."""
    row = _pick(
        conn,
        rng,
        "SELECT d.* FROM documents d JOIN publication_rules r ON r.kind = d.kind "
        "WHERE d.status = 'draft' "
        "AND julianday(?) - julianday(d.published_date) <= r.max_draft_age_days "
        "AND (r.requires_review = 0 OR EXISTS (SELECT 1 FROM doc_issues i "
        "     WHERE i.doc_id = d.doc_id AND i.status = 'accepted')) ORDER BY d.doc_id",
        (TODAY,),
    )
    if row is None:
        return None
    return {
        "record_id": row["doc_id"],
        "principal_id": row["service_id"],
        "principal_group": row["kind"],
        "amount_cents": 0,
        "arguments": {"doc_id": row["doc_id"], "note": "Reviewed and approved."},
        "raw": row,
    }


BLOCKED_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "not_a_draft",
        "SELECT * FROM documents WHERE status = 'current' ORDER BY doc_id",
        "it is already the current version rather than a draft",
    ),
    (
        "draft_expired",
        "SELECT d.* FROM documents d JOIN publication_rules r ON r.kind = d.kind "
        "WHERE d.status = 'draft' "
        "AND julianday('{today}') - julianday(d.published_date) > r.max_draft_age_days "
        "ORDER BY d.doc_id",
        "the draft is too old and must be re-reviewed",
    ),
    (
        "review_required",
        "SELECT d.* FROM documents d JOIN publication_rules r ON r.kind = d.kind "
        "WHERE d.status = 'draft' AND r.requires_review = 1 "
        "AND julianday('{today}') - julianday(d.published_date) <= r.max_draft_age_days "
        "AND NOT EXISTS (SELECT 1 FROM doc_issues i WHERE i.doc_id = d.doc_id "
        "AND i.status = 'accepted') ORDER BY d.doc_id",
        "no documentation issue against it has been accepted",
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
            "record_id": row["doc_id"],
            "principal_id": row["service_id"],
            "principal_group": row["kind"],
            "code": code,
            "phrase": phrase,
            "arguments": {"doc_id": row["doc_id"], "note": "Requested by the owning team."},
            "raw": row,
        }
    return None


def ambiguous_name(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """A service stem shared by more than one service, for example two 'Atlas'
    services in different categories."""
    rows = rows_to_dicts(
        conn.execute(
            "SELECT substr(name, 1, instr(name, ' ') - 1) AS stem, COUNT(*) AS n "
            "FROM services GROUP BY stem HAVING n >= 2 ORDER BY stem"
        )
    )
    if not rows:
        return None
    row = rng.choice(rows)
    matches = rows_to_dicts(
        conn.execute(
            "SELECT service_id, name, category FROM services WHERE name LIKE ? ORDER BY service_id",
            (f"{row['stem']} %",),
        )
    )
    return {"query": row["stem"], "match_count": row["n"], "matches": matches}


def missing_record_id(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    for _attempt in range(50):
        candidate = f"DOC-{rng.randint(90_000, 99_999)}"
        if (
            conn.execute("SELECT 1 FROM documents WHERE doc_id = ?", (candidate,)).fetchone()
            is None
        ):
            return {"id": candidate, "kind": "document"}
    return None  # pragma: no cover


def policy_question(
    conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    row = _pick(conn, rng, "SELECT * FROM publication_rules ORDER BY rule_id")
    if row is None:
        return None
    field, phrase, value = rng.choice(
        [
            (
                "requires_review",
                "whether an accepted documentation issue is required before publishing",
                "yes" if row["requires_review"] else "no",
            ),
            (
                "max_draft_age_days",
                "how many days a draft may sit before it must be re-reviewed",
                str(row["max_draft_age_days"]),
            ),
        ]
    )
    return {
        "arguments": {"kind": row["kind"]},
        "subject": f"{row['kind'].replace('_', ' ')} documents",
        "field": field,
        "phrase": phrase,
        "value": value,
        "citation": row["rule_id"],
    }


def metric(
    _conn: sqlite3.Connection, rng: random.Random, _: dict[str, Any]
) -> dict[str, Any] | None:
    """Metrics need no row: the aggregate is computed by the tool itself."""
    choice = rng.choice(
        [
            ("document_count", "kind", "documents"),
            ("document_count", "status", "documents"),
            ("document_count", "category", "documents"),
            ("issue_count", "status", "documentation issues"),
            ("service_count", "category", "services"),
        ]
    )
    return {"metric": choice[0], "group_by": choice[1], "noun": choice[2]}


def grounded_fact(
    conn: sqlite3.Connection, rng: random.Random, kwargs: dict[str, Any]
) -> dict[str, Any] | None:
    """A question whose answer is a column and whose citation is a section id.

    ``stale=True`` draws a fact from a service that also has a superseded
    document carrying a different value for the same key. Those are the tasks
    where citing the wrong version is possible, and they are reported separately.
    """
    want_stale = bool(kwargs.get("stale"))
    sql = (
        "SELECT s.section_id, s.fact_key, s.fact_value, s.heading, d.doc_id, d.kind, "
        "d.status, v.service_id, v.name AS service_name "
        "FROM sections s JOIN documents d USING(doc_id) JOIN services v USING(service_id) "
        "WHERE s.fact_key IS NOT NULL AND d.status = 'current'"
        # The citation must be unambiguous, so the fact has to be unique within
        # (service, document kind). The prompt then names the kind, which is how
        # a person would disambiguate too: "according to the Atlas Store policy".
        " AND NOT EXISTS (SELECT 1 FROM sections s3 JOIN documents d3 USING(doc_id)"
        "  WHERE d3.service_id = v.service_id AND d3.status = 'current'"
        "  AND d3.kind = d.kind AND s3.fact_key = s.fact_key"
        "  AND s3.section_id != s.section_id)"
    )
    if want_stale:
        sql += (
            " AND EXISTS (SELECT 1 FROM sections s2 JOIN documents d2 USING(doc_id) "
            " WHERE d2.service_id = v.service_id AND d2.status = 'superseded' "
            " AND s2.fact_key = s.fact_key AND s2.fact_value != s.fact_value)"
        )
    sql += " ORDER BY s.section_id"
    row = _pick(conn, rng, sql)
    if row is None:
        return None
    label, phrase = FACT_QUESTIONS[row["fact_key"]]
    return {
        "section_id": row["section_id"],
        "doc_id": row["doc_id"],
        "service_id": row["service_id"],
        "service_name": row["service_name"],
        "fact_key": row["fact_key"],
        "value": row["fact_value"],
        "label": label,
        "phrase": phrase,
        "kind": row["kind"],
        "kind_phrase": row["kind"].replace("_", " "),
        "query": f"{row['service_name']} {label}",
        "stale_variant": want_stale,
    }


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
    "grounded_fact": grounded_fact,
}
