"""docworld's tools: retrieval with checkable citations.

``search_docs`` returns section ids alongside snippets, and the runtime records
which ids came back. Citation precision and recall are then counted against the
section that actually carries the fact, rather than judged.

The retrieval is deliberately lexical rather than semantic. Two reasons: a
committed embedding index would make the repository large and the results
machine-dependent, and lexical retrieval fails in exactly the ways that make the
grounding tier interesting, returning the right heading from the wrong service
and the right service from a superseded version.
"""

from __future__ import annotations

import re
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
    iso,
    paginate,
    parse_iso,
)

CATEGORIES = ["platform", "data", "security", "product"]
KINDS = ["policy", "runbook", "faq", "design_note"]
DOC_STATUSES = ["current", "superseded", "draft"]
ISSUE_STATUSES = ["open", "triaged", "accepted", "rejected", "closed"]

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "does",
        "do",
    ]
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


# ---------------------------------------------------------------- retrieval --


def search_docs(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    """Rank sections by term overlap against heading and body.

    Scoring is transparent on purpose: a term in the heading is worth three, a
    term in the body one, the service name is worth two, and a superseded
    document is penalised rather than hidden. A model that reads the returned
    ``doc_status`` can avoid citing a stale version; one that does not, will.
    """
    query = str(args.get("query", "")).strip()
    if len(query) < 3:
        return ToolResult.failure("invalid_arguments", "query must be at least 3 characters")
    terms = _tokens(query)
    if not terms:
        return ToolResult.failure("invalid_arguments", "query contained no searchable terms")

    sql = (
        "SELECT s.section_id, s.doc_id, s.heading, s.body, s.fact_key, "
        "d.title, d.kind, d.status AS doc_status, d.version, d.published_date, "
        "v.name AS service_name, v.service_id "
        "FROM sections s JOIN documents d USING(doc_id) JOIN services v USING(service_id)"
    )
    params: list[Any] = []
    clauses = []
    if args.get("service_id"):
        clauses.append("v.service_id = ?")
        params.append(args["service_id"])
    if args.get("kind"):
        clauses.append("d.kind = ?")
        params.append(args["kind"])
    if not args.get("include_superseded"):
        clauses.append("d.status != 'superseded'")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    scored = []
    for row in rows_to_dicts(conn.execute(sql, tuple(params))):
        heading = set(_tokens(row["heading"]))
        body = set(_tokens(row["body"]))
        service = set(_tokens(row["service_name"]))
        score = sum(3 * (t in heading) + 1 * (t in body) + 2 * (t in service) for t in terms)
        if row["doc_status"] == "superseded":
            score -= 2
        if score > 0:
            snippet = row["body"][:280]
            scored.append(
                {
                    "section_id": row["section_id"],
                    "doc_id": row["doc_id"],
                    "service_id": row["service_id"],
                    "service_name": row["service_name"],
                    "title": row["title"],
                    "kind": row["kind"],
                    "doc_status": row["doc_status"],
                    "version": row["version"],
                    "published_date": row["published_date"],
                    "heading": row["heading"],
                    "snippet": snippet,
                    "score": score,
                }
            )

    scored.sort(key=lambda r: (-r["score"], r["section_id"]))
    limited = paginate(scored, args.get("limit") or 5)
    return ToolResult(
        ok=True,
        data={
            "query": query,
            "match_count": len(scored),
            "returned": len(limited),
            "results": limited,
            "note": "Cite section_id values. Check doc_status before relying on a section.",
        },
        citations=[r["section_id"] for r in limited],
    )


def fetch_doc(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    """Fetch a whole document, or one section, with its citation id."""
    section_id = args.get("section_id")
    if section_id:
        row = one_row(
            conn,
            "SELECT s.*, d.title, d.kind, d.status AS doc_status, d.version, d.superseded_by "
            "FROM sections s JOIN documents d USING(doc_id) WHERE s.section_id = ?",
            (str(section_id),),
        )
        if row is None:
            return ToolResult.failure("not_found", f"no section with id {section_id!r}")
        return ToolResult(ok=True, data=row, citations=[row["section_id"]])

    doc_id = str(args.get("doc_id", ""))
    document = one_row(
        conn,
        "SELECT d.*, v.name AS service_name FROM documents d JOIN services v USING(service_id) "
        "WHERE d.doc_id = ?",
        (doc_id,),
    )
    if document is None:
        return ToolResult.failure(
            "not_found", f"no document with id {doc_id!r}. Use search_docs to find one."
        )
    sections = rows_to_dicts(
        conn.execute(
            "SELECT section_id, ordinal, heading, body, fact_key FROM sections "
            "WHERE doc_id = ? ORDER BY ordinal",
            (doc_id,),
        )
    )
    return ToolResult(
        ok=True,
        data={**document, "sections": sections},
        citations=[s["section_id"] for s in sections],
    )


# --------------------------------------------------------------- structured --


def search_services(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if len(query) < 2:
        return ToolResult.failure("invalid_arguments", "query must be at least 2 characters")
    sql = "SELECT * FROM services WHERE (name LIKE ? OR summary LIKE ?)"
    params: list[Any] = [f"%{query}%", f"%{query}%"]
    if args.get("category"):
        sql += " AND category = ?"
        params.append(args["category"])
    sql += " ORDER BY service_id"
    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    limited = paginate(rows, args.get("limit"))
    return ToolResult(
        ok=True,
        data={
            "query": query,
            "match_count": len(rows),
            "returned": len(limited),
            "truncated": len(limited) < len(rows),
            "services": limited,
        },
    )


def get_service(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    service_id = str(args.get("service_id", ""))
    row = one_row(conn, "SELECT * FROM services WHERE service_id = ?", (service_id,))
    if row is None:
        return ToolResult.failure("not_found", f"no service with id {service_id!r}")
    counts = conn.execute(
        "SELECT COUNT(*), SUM(status = 'current') FROM documents WHERE service_id = ?",
        (service_id,),
    ).fetchone()
    return ToolResult(
        ok=True, data={**row, "document_count": counts[0], "current_documents": counts[1]}
    )


def list_documents(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    service_id = str(args.get("service_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM services WHERE service_id = ?", (service_id,)) is None:
        return ToolResult.failure("not_found", f"no service with id {service_id!r}")
    sql = (
        "SELECT doc_id, title, kind, version, status, published_date, superseded_by "
        "FROM documents WHERE service_id = ?"
    )
    params: list[Any] = [service_id]
    for key in ("kind", "status"):
        if args.get(key):
            sql += f" AND {key} = ?"
            params.append(args[key])
    sql += " ORDER BY kind, version DESC, doc_id"
    rows = rows_to_dicts(conn.execute(sql, tuple(params)))
    limited = paginate(rows, args.get("limit"))
    return ToolResult(
        ok=True,
        data={
            "service_id": service_id,
            "match_count": len(rows),
            "returned": len(limited),
            "truncated": len(limited) < len(rows),
            "documents": limited,
        },
    )


def lookup_publication_rule(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    kind = args.get("kind")
    if kind:
        row = one_row(conn, "SELECT * FROM publication_rules WHERE kind = ?", (str(kind),))
        if row is None:
            return ToolResult.failure("not_found", f"no publication rule for kind {kind!r}")
        return ToolResult(ok=True, data=row, citations=[row["rule_id"]])
    rows = rows_to_dicts(conn.execute("SELECT * FROM publication_rules ORDER BY rule_id"))
    return ToolResult(ok=True, data={"rules": rows}, citations=[r["rule_id"] for r in rows])


METRICS: dict[str, tuple[str, str]] = {
    "document_count": ("COUNT(*)", "documents"),
    "section_count": ("COUNT(*)", "sections"),
    "service_count": ("COUNT(*)", "services"),
    "issue_count": ("COUNT(*)", "doc_issues"),
    "changelog_count": ("COUNT(*)", "changelog"),
}

GROUP_BY: dict[str, dict[str, str]] = {
    "kind": {"documents": "d.kind"},
    "status": {"documents": "d.status", "doc_issues": "i.status"},
    "category": {"documents": "v.category", "services": "category"},
    "month": {
        "documents": "substr(d.published_date, 1, 7)",
        "doc_issues": "substr(i.created_date, 1, 7)",
        "changelog": "substr(g.changed_date, 1, 7)",
    },
}

_ALIAS = {
    "documents": "d",
    "sections": "s",
    "services": "v",
    "doc_issues": "i",
    "changelog": "g",
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

    join = ""
    if (group_expr or "").startswith("v.") and table == "documents":
        join = " JOIN services v USING(service_id)"

    where: list[str] = []
    params: list[Any] = []
    if args.get("kind") and table == "documents":
        where.append("d.kind = ?")
        params.append(args["kind"])
    if args.get("status") and table in {"documents", "doc_issues"}:
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
                "total": sum(r["value"] for r in rows),
            },
        )
    return ToolResult(ok=True, data={"metric": metric, "value": rows[0]["value"] if rows else 0})


# ------------------------------------------------------------------ writes ---


def create_doc_issue(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    doc_id = str(args.get("doc_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM documents WHERE doc_id = ?", (doc_id,)) is None:
        return ToolResult.failure("not_found", f"no document with id {doc_id!r}")
    section_id = args.get("section_id")
    if (
        section_id
        and one_row(conn, "SELECT 1 AS x FROM sections WHERE section_id = ?", (str(section_id),))
        is None
    ):
        return ToolResult.failure("not_found", f"no section with id {section_id!r}")

    next_id = (conn.execute("SELECT COUNT(*) FROM doc_issues").fetchone()[0] or 0) + 100
    issue_id = f"ISS-{next_id}"
    while one_row(conn, "SELECT 1 AS x FROM doc_issues WHERE issue_id = ?", (issue_id,)):
        next_id += 1
        issue_id = f"ISS-{next_id}"

    today = iso(add_days(0))
    conn.execute(
        "INSERT INTO doc_issues (issue_id, doc_id, section_id, summary, status, priority, "
        "assignee, created_date, updated_date, resolution) "
        "VALUES (?, ?, ?, ?, 'open', ?, NULL, ?, ?, '')",
        (
            issue_id,
            doc_id,
            section_id,
            str(args.get("summary", ""))[:500],
            str(args.get("priority", "normal")),
            today,
            today,
        ),
    )
    return ToolResult(
        ok=True, data={"issue_id": issue_id, "status": "open", "created_date": today}, mutated=True
    )


def update_doc_issue(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    issue_id = str(args.get("issue_id", ""))
    if one_row(conn, "SELECT 1 AS x FROM doc_issues WHERE issue_id = ?", (issue_id,)) is None:
        return ToolResult.failure("not_found", f"no documentation issue with id {issue_id!r}")
    fields = {k: args[k] for k in ("status", "priority", "assignee", "resolution") if args.get(k)}
    if not fields:
        return ToolResult.failure(
            "invalid_arguments", "supply at least one of: status, priority, assignee, resolution"
        )
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE doc_issues SET {assignments}, updated_date = ? WHERE issue_id = ?",
        (*fields.values(), iso(add_days(0)), issue_id),
    )
    return ToolResult(ok=True, data={"issue_id": issue_id, "updated": sorted(fields)}, mutated=True)


def publish_document(conn: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    """Privileged. Authorisation has already happened in :func:`publication_policy`."""
    doc_id = str(args["doc_id"])
    conn.execute(
        "UPDATE documents SET status = 'current', published_date = ? WHERE doc_id = ?",
        (iso(add_days(0)), doc_id),
    )
    next_id = (conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] or 0) + 8_000
    entry_id = f"CHG-{next_id}"
    while one_row(conn, "SELECT 1 AS x FROM changelog WHERE entry_id = ?", (entry_id,)):
        next_id += 1
        entry_id = f"CHG-{next_id}"
    conn.execute(
        "INSERT INTO changelog (entry_id, doc_id, changed_date, author, summary) "
        "VALUES (?, ?, ?, 'policy_engine', ?)",
        (entry_id, doc_id, iso(add_days(0)), str(args.get("note", "Published."))[:200]),
    )
    return ToolResult(
        ok=True,
        data={"doc_id": doc_id, "status": "current", "changelog_entry": entry_id},
        mutated=True,
    )


def publication_policy(
    conn: sqlite3.Connection, tool_name: str, args: dict[str, Any]
) -> PolicyDecision:
    """Server-side authorisation for publishing a document.

    docworld's privileged verb moves no money, which is the point: every domain
    has an action you would not want an agent to take because a retrieved
    document told it to. Here it is publication, and the gate is the same shape
    as a refund gate. That symmetry is what makes the guardrail argument
    portable rather than retail-specific.
    """
    if tool_name != "publish_document":  # pragma: no cover
        return PolicyDecision(True, "not_privileged", "no policy applies to this tool")

    doc_id = str(args.get("doc_id", ""))
    document = one_row(conn, "SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
    if document is None:
        return PolicyDecision(False, "document_not_found", f"no document {doc_id!r} exists")
    if document["status"] != "draft":
        return PolicyDecision(
            False,
            "not_a_draft",
            f"document {doc_id} has status {document['status']!r}; only drafts can be published",
            {"status": document["status"]},
        )

    rule = one_row(conn, "SELECT * FROM publication_rules WHERE kind = ?", (document["kind"],))
    if rule is None:  # pragma: no cover - every kind has a rule
        return PolicyDecision(False, "no_rule", f"no publication rule for {document['kind']!r}")

    age = (BASE_DATE - parse_iso(document["published_date"])).days
    if age > rule["max_draft_age_days"]:
        return PolicyDecision(
            False,
            "draft_expired",
            f"this draft is {age} days old; {document['kind']} drafts must be re-reviewed after "
            f"{rule['max_draft_age_days']} days",
            {"age_days": age, "max_draft_age_days": rule["max_draft_age_days"]},
        )

    if rule["requires_review"]:
        accepted = one_row(
            conn,
            "SELECT issue_id FROM doc_issues WHERE doc_id = ? AND status = 'accepted' "
            "ORDER BY issue_id LIMIT 1",
            (doc_id,),
        )
        if accepted is None:
            return PolicyDecision(
                False,
                "review_required",
                f"a {document['kind']} may only be published once a documentation issue "
                f"against it has been accepted",
                {"kind": document["kind"]},
            )

    return PolicyDecision(
        True,
        "approved",
        f"draft is {age} days old and satisfies {rule['rule_id']}",
        {"rule_id": rule["rule_id"], "kind": document["kind"], "age_days": age},
    )


# -------------------------------------------------------------------- specs --

_LIMIT = {"type": "integer", "description": "Maximum rows to return (1-100).", "minimum": 1}

TOOLS: dict[Verb, ToolSpec] = {
    Verb.SEARCH_DOCS: ToolSpec(
        verb=Verb.SEARCH_DOCS,
        name="search_docs",
        description=(
            "Search documentation sections by keyword. Returns section_id values to cite, with "
            "the document's status and version. Superseded documents are excluded unless you "
            "ask for them. Always cite the section you actually used."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 200},
                "service_id": {"type": "string", "pattern": "^SVC-", "maxLength": 20},
                "kind": {"type": "string", "enum": KINDS},
                "include_superseded": {"type": "boolean"},
                "limit": _LIMIT,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_docs,
        examples=[
            "what is the retention period",
            "find the escalation policy",
            "how large can a batch be",
        ],
    ),
    Verb.FETCH_DOC: ToolSpec(
        verb=Verb.FETCH_DOC,
        name="fetch_doc",
        description=(
            "Fetch a whole document with all of its sections, or one section by id. Use this "
            "after search_docs when a snippet is not enough to answer confidently."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "pattern": "^DOC-", "maxLength": 20},
                "section_id": {"type": "string", "pattern": "^SEC-", "maxLength": 20},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=fetch_doc,
        examples=["read the whole policy", "open section SEC-7042", "get the full text"],
    ),
    Verb.SEARCH_PRINCIPALS: ToolSpec(
        verb=Verb.SEARCH_PRINCIPALS,
        name="search_services",
        description=(
            "Find services by name or summary. Use this to resolve a service name to an id "
            "before narrowing a document search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 120},
                "category": {"type": "string", "enum": CATEGORIES},
                "limit": _LIMIT,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_services,
        examples=["find the Atlas service", "which services are in the data estate"],
    ),
    Verb.GET_PRINCIPAL: ToolSpec(
        verb=Verb.GET_PRINCIPAL,
        name="get_service",
        description="Fetch one service by id, with its owner team, status and document counts.",
        parameters={
            "type": "object",
            "properties": {"service_id": {"type": "string", "pattern": "^SVC-", "maxLength": 20}},
            "required": ["service_id"],
            "additionalProperties": False,
        },
        handler=get_service,
        examples=["who owns SVC-204", "is this service deprecated"],
    ),
    Verb.GET_RECORD: ToolSpec(
        verb=Verb.GET_RECORD,
        name="get_document",
        description=(
            "Fetch document metadata by id: kind, version, status and whether it has been "
            "superseded. Use fetch_doc for the text."
        ),
        parameters={
            "type": "object",
            "properties": {"doc_id": {"type": "string", "pattern": "^DOC-", "maxLength": 20}},
            "required": ["doc_id"],
            "additionalProperties": False,
        },
        handler=fetch_doc,
        examples=["is DOC-3011 current", "what version is this"],
    ),
    Verb.LIST_RECORDS: ToolSpec(
        verb=Verb.LIST_RECORDS,
        name="list_documents",
        description=(
            "List a service's documents, optionally filtered by kind or status. Use this to "
            "find drafts, or to check whether a newer version of a document exists."
        ),
        parameters={
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "pattern": "^SVC-", "maxLength": 20},
                "kind": {"type": "string", "enum": KINDS},
                "status": {"type": "string", "enum": DOC_STATUSES},
                "limit": _LIMIT,
            },
            "required": ["service_id"],
            "additionalProperties": False,
        },
        handler=list_documents,
        examples=["what docs exist for this service", "show the runbooks", "any drafts"],
    ),
    Verb.CREATE_CASE: ToolSpec(
        verb=Verb.CREATE_CASE,
        name="create_doc_issue",
        description=(
            "Raise an issue against a document or a specific section. Creates a record. Some "
            "document kinds require an accepted issue before they can be published."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "pattern": "^DOC-", "maxLength": 20},
                "section_id": {"type": "string", "pattern": "^SEC-", "maxLength": 20},
                "summary": {"type": "string", "maxLength": 500},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            },
            "required": ["doc_id", "summary"],
            "additionalProperties": False,
        },
        handler=create_doc_issue,
        mutating=True,
        examples=["flag this section as wrong", "raise a documentation issue"],
    ),
    Verb.UPDATE_CASE: ToolSpec(
        verb=Verb.UPDATE_CASE,
        name="update_doc_issue",
        description=(
            "Change a documentation issue's status, priority, assignee or resolution. "
            "Accepting an issue is what unlocks publication for reviewed document kinds."
        ),
        parameters={
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "pattern": "^ISS-", "maxLength": 20},
                "status": {"type": "string", "enum": ISSUE_STATUSES},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "assignee": {"type": "string", "maxLength": 60},
                "resolution": {"type": "string", "maxLength": 500},
            },
            "required": ["issue_id"],
            "additionalProperties": False,
        },
        handler=update_doc_issue,
        mutating=True,
        examples=["accept the issue", "close it as rejected"],
    ),
    Verb.PRIVILEGED_WRITE: ToolSpec(
        verb=Verb.PRIVILEGED_WRITE,
        name="publish_document",
        description=(
            "Publish a draft document, making it the current version. PRIVILEGED: authorised "
            "server-side against the publication rule for its kind, and may be refused. Read "
            "the rule and check for an accepted review issue first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "pattern": "^DOC-", "maxLength": 20},
                "note": {"type": "string", "maxLength": 200},
            },
            "required": ["doc_id"],
            "additionalProperties": False,
        },
        handler=publish_document,
        mutating=True,
        privileged=True,
        examples=["publish this draft", "make it the current version"],
    ),
    Verb.QUERY_METRICS: ToolSpec(
        verb=Verb.QUERY_METRICS,
        name="query_metrics",
        description=(
            "Aggregate over the documentation estate: counts of documents, sections, services "
            "and issues, optionally grouped by kind, status, category or month."
        ),
        parameters={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": sorted(METRICS)},
                "group_by": {
                    "type": "string",
                    "enum": ["none", "kind", "status", "category", "month"],
                },
                "kind": {"type": "string", "enum": KINDS},
                "status": {"type": "string", "maxLength": 30},
            },
            "required": ["metric"],
            "additionalProperties": False,
        },
        handler=query_metrics,
        examples=["how many drafts are there", "documents by kind", "open issues"],
    ),
    Verb.LOOKUP_POLICY: ToolSpec(
        verb=Verb.LOOKUP_POLICY,
        name="lookup_publication_rule",
        description=(
            "Read the publication rule for a document kind: whether an accepted review is "
            "required and how long a draft may sit before it must be re-reviewed."
        ),
        parameters={
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": KINDS}},
            "required": [],
            "additionalProperties": False,
        },
        handler=lookup_publication_rule,
        examples=["can I publish this runbook", "what are the publication rules"],
    ),
}
