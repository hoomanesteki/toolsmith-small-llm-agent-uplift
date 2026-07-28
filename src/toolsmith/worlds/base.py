"""The world contract: what a domain must provide to be evaluable.

THE DESIGN PROBLEM
------------------
One synthetic world is a toy-world attack surface. Three worlds sharing a tool
grammar turn that attack into a generalisation result: train and tune on one,
measure on a second whose schema was never seen, and report the transfer drop
honestly.

But that only works if "same grammar, different nouns" is real rather than
aspirational. So the grammar is the primary abstraction. A world does not
declare arbitrary tools; it binds canonical :class:`Verb` slots to
its own nouns. ``SEARCH_PRINCIPALS`` is ``search_customers`` in operations and
``search_patients`` in the clinic. An oracle program is written in verbs, so the
same task template generates correctly for any world, including one added
tomorrow.

ADDING A FOURTH DOMAIN
----------------------
Implement :class:`WorldSpec`: a schema, a seeded builder, and a handler for each
verb you support. Drop it in a package under ``worlds/`` and register it. No
runtime, harness, task-generator or UI code changes. ``tests/test_worlds.py``
runs its conformance suite against every registered world automatically, so a
new domain is tested the moment it exists.

GROUND TRUTH BY CONSTRUCTION
----------------------------
Every task carries an oracle program: the exact gold call sequence. Running it
in a fresh sandbox produces the answer and the state diff. Ground truth is
computed, never written by hand and never judged by a model.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
import json
import random
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

#: Every world's clock. No wall-clock time appears anywhere in a world, a task
#: or an oracle, so a task generated today has the same answer in a year.
BASE_DATE = dt.date(2026, 1, 1)


class Verb(enum.StrEnum):
    """The shared tool grammar.

    Twelve slots. A world binds the ones it has and omits the rest; the task
    generator only emits programs using verbs the target world binds.
    """

    SEARCH_PRINCIPALS = "search_principals"
    """Fuzzy lookup over the domain's main actor. Customers. Patients."""

    GET_PRINCIPAL = "get_principal"
    """Fetch one actor by id."""

    GET_RECORD = "get_record"
    """Fetch one transaction by id. An order. An appointment."""

    LIST_RECORDS = "list_records"
    """List an actor's transactions, filtered and sorted."""

    CREATE_CASE = "create_case"
    """Open a work item. A support ticket. A referral."""

    UPDATE_CASE = "update_case"
    """Mutate a work item's status, priority or assignee."""

    PRIVILEGED_WRITE = "privileged_write"
    """The consequential verb: the one with a blast radius. A refund, a billing
    adjustment, a document publication. Always gated server-side by a
    deterministic policy function, never by the model's own judgement.

    Named for the property that matters rather than for money, because the
    property is what generalises: any domain has exactly one action you would
    not want an agent to take because a retrieved document told it to."""

    QUERY_METRICS = "query_metrics"
    """Aggregate over the domain. Counts, sums, averages, grouped."""

    LOOKUP_POLICY = "lookup_policy"
    """Read the domain's own rules, so a policy question is answerable."""

    SEARCH_DOCS = "search_docs"
    """Retrieval over unstructured text. The grounding surface."""

    FETCH_DOC = "fetch_doc"
    """Fetch a document or a specific span by id."""

    CALCULATOR = "calculator"
    """Arithmetic, because models should not do arithmetic."""

    TODAY = "today"
    """The world's current date. Never the wall clock."""


#: Verbs every world must bind. A domain without these cannot express a
#: multi-hop task, which is the bulk of the suite.
REQUIRED_VERBS: frozenset[Verb] = frozenset(
    {
        Verb.SEARCH_PRINCIPALS,
        Verb.GET_PRINCIPAL,
        Verb.GET_RECORD,
        Verb.LIST_RECORDS,
        Verb.QUERY_METRICS,
        Verb.CALCULATOR,
        Verb.TODAY,
    }
)


# ------------------------------------------------------------------- results --


@dataclass(slots=True)
class ToolResult:
    """What a tool hands back to the executor.

    ``ok`` false is a normal outcome, not an exception. A model asking for a
    customer that does not exist should receive a clean "not found" and be
    expected to abstain, which is precisely what tier T4 measures.
    """

    ok: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None
    mutated: bool = False
    citations: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
            payload["error_code"] = self.error_code
        if self.citations:
            payload["citations"] = self.citations
        return json.dumps(payload, sort_keys=True, default=str)

    @classmethod
    def failure(cls, code: str, message: str) -> ToolResult:
        return cls(ok=False, error=message, error_code=code)


@dataclass(slots=True)
class PolicyDecision:
    """The server-side verdict on a privileged call.

    The model's opinion about whether a refund is allowed is an input to logging
    and never to authorisation. This object is produced by code, after the model
    has asked, and it is the only thing that can permit the mutation.
    """

    allowed: bool
    code: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[[sqlite3.Connection, dict[str, Any]], ToolResult]


@dataclass(slots=True)
class ToolSpec:
    """One concrete tool: a verb bound to a world's nouns."""

    verb: Verb
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    mutating: bool = False
    privileged: bool = False
    """Privileged tools are gated by the world's policy function and trigger a
    human-in-the-loop interrupt in the runtime."""

    examples: list[str] = field(default_factory=list)
    """Short natural-language uses. Feeds tool-search ranking and the skill card."""

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def search_text(self) -> str:
        return " ".join([self.name, self.description, *self.examples]).lower()


# -------------------------------------------------------------- state diffs --

DiffKind = Literal["added", "removed", "changed"]


@dataclass(slots=True)
class RowChange:
    table: str
    kind: DiffKind
    key: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    def signature(self) -> str:
        """Order-independent identity, used to compare against the oracle."""
        payload = {
            "table": self.table,
            "kind": self.kind,
            "key": self.key,
            "after": _normalise(self.after),
            "before": _normalise(self.before),
        }
        return json.dumps(payload, sort_keys=True, default=str)


@dataclass(slots=True)
class StateDiff:
    """Everything a trajectory changed in the world.

    This is half of the headline metric. An answer that reads perfectly while
    the trajectory issued a refund it should not have is a failure, and only a
    state diff can see that.
    """

    changes: list[RowChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes

    def signature(self) -> str:
        return json.dumps(sorted(c.signature() for c in self.changes))

    def matches(self, other: StateDiff) -> bool:
        return self.signature() == other.signature()

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for change in self.changes:
            key = f"{change.table}.{change.kind}"
            out[key] = out.get(key, 0) + 1
        return out


#: Columns excluded from diffs and digests because they record when a row was
#: written rather than what it says. Including them would make an identical
#: trajectory look different on a second run.
VOLATILE_COLUMNS: frozenset[str] = frozenset({"created_at_wall", "updated_at_wall"})


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: v for k, v in sorted(row.items()) if k not in VOLATILE_COLUMNS}


# ----------------------------------------------------------------- the world --


@dataclass(slots=True)
class Entity:
    """One table, described for humans and for the UI's schema view."""

    table: str
    label: str
    description: str
    primary_key: str
    columns: dict[str, str]


SeedFn = Callable[[sqlite3.Connection, int], None]
PolicyFn = Callable[[sqlite3.Connection, str, dict[str, Any]], PolicyDecision]
SamplerFn = Callable[[sqlite3.Connection, "random.Random", dict[str, Any]], "dict[str, Any] | None"]

#: Keys every world must supply so that task templates can write natural
#: prompts without knowing which domain they are generating for. This is the
#: other half of the verb grammar: verbs make the PROGRAMS portable, the
#: lexicon makes the QUESTIONS readable.
#: Named row samplers a world must provide. Picking an *interesting* row
#: genuinely needs domain knowledge (which order is refundable? which name is
#: ambiguous?), so rather than pretend otherwise, the world declares it. Task
#: templates then stay domain-free: they ask for "a record the policy will
#: refuse" and phrase the question with the lexicon.
REQUIRED_SAMPLERS: frozenset[str] = frozenset(
    {
        "principal",  # any actor
        "record",  # any transaction
        "record_done",  # a transaction in its terminal, actionable state
        "privileged_allowed",  # arguments the policy function will approve
        "privileged_blocked",  # arguments the policy function will refuse
        "ambiguous_name",  # a query matching two or more principals
        "missing_record_id",  # an id that does not exist
        "policy_question",  # a policy lookup with a known correct answer
        "metric",  # a metric worth aggregating
    }
)

REQUIRED_LEXICON: frozenset[str] = frozenset(
    {
        "principal",
        "principal_plural",
        "principal_id",
        "record",
        "record_plural",
        "record_id",
        "case",
        "case_plural",
        "privileged_action",
        "policy_noun",
    }
)


@dataclass(slots=True)
class WorldSpec:
    """A domain. Everything ToolSmith needs to evaluate against it."""

    key: str
    title: str
    tagline: str
    role: Literal["primary", "transfer", "grounding"]
    """``primary`` is trained and tuned on. ``transfer`` is never trained on and
    exists to measure generalisation. ``grounding`` carries the retrieval tier."""

    schema_sql: str
    seed: SeedFn
    entities: list[Entity]
    tools: dict[Verb, ToolSpec]
    policy: PolicyFn | None = None
    lexicon: dict[str, str] = field(default_factory=dict)
    samplers: dict[str, SamplerFn] = field(default_factory=dict)
    default_seed: int = 20260101
    notes: str = ""

    def __post_init__(self) -> None:
        missing = REQUIRED_VERBS - set(self.tools)
        if missing:
            raise ValueError(
                f"world {self.key!r} does not bind required verbs: "
                f"{', '.join(sorted(missing))}. See worlds/base.REQUIRED_VERBS."
            )
        for verb, tool in self.tools.items():
            if tool.verb is not verb:
                raise ValueError(f"world {self.key!r} binds {verb} to a tool declaring {tool.verb}")
        missing_samplers = REQUIRED_SAMPLERS - set(self.samplers)
        if missing_samplers:
            raise ValueError(
                f"world {self.key!r} is missing samplers: "
                f"{', '.join(sorted(missing_samplers))}. See worlds/base.REQUIRED_SAMPLERS."
            )
        missing_words = REQUIRED_LEXICON - set(self.lexicon)
        if missing_words:
            raise ValueError(
                f"world {self.key!r} is missing lexicon entries: "
                f"{', '.join(sorted(missing_words))}. Task templates need them to phrase "
                "prompts in this domain's language."
            )
        if any(t.privileged for t in self.tools.values()) and self.policy is None:
            raise ValueError(
                f"world {self.key!r} exposes a privileged tool but declares no policy "
                "function. Privileged calls must be authorised server-side."
            )

    # -- lookups ------------------------------------------------------------

    @property
    def verbs(self) -> list[Verb]:
        return sorted(self.tools, key=lambda v: v.value)

    @property
    def tool_names(self) -> list[str]:
        return sorted(t.name for t in self.tools.values())

    def tool(self, name_or_verb: str | Verb) -> ToolSpec:
        if isinstance(name_or_verb, Verb):
            return self.tools[name_or_verb]
        for tool in self.tools.values():
            if tool.name == name_or_verb:
                return tool
        raise KeyError(
            f"{self.key} has no tool {name_or_verb!r}. Available: {', '.join(self.tool_names)}"
        )

    def has(self, verb: Verb) -> bool:
        return verb in self.tools

    def resolve(self, verb: Verb) -> str:
        """Verb to this world's tool name. The whole point of the grammar."""
        return self.tools[verb].name

    def privileged_tools(self) -> list[str]:
        return sorted(t.name for t in self.tools.values() if t.privileged)

    def sample(
        self, name: str, conn: sqlite3.Connection, rng: random.Random, **kwargs: Any
    ) -> dict[str, Any] | None:
        """Draw one interesting row. Returns None when the seed has none."""
        try:
            sampler = self.samplers[name]
        except KeyError:
            raise KeyError(
                f"world {self.key!r} has no sampler {name!r}; "
                f"it defines {', '.join(sorted(self.samplers))}"
            ) from None
        return sampler(conn, rng, kwargs)

    def word(self, key: str) -> str:
        """This domain's word for a shared concept. See REQUIRED_LEXICON."""
        try:
            return self.lexicon[key]
        except KeyError:
            raise KeyError(
                f"world {self.key!r} has no lexicon entry {key!r}; "
                f"it defines {', '.join(sorted(self.lexicon))}"
            ) from None


# ------------------------------------------------------------------ digests --


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, Any]]]:
    """Every row in the database, keyed by table then primary key."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for table in table_names(conn):
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cursor.description]
        pk = columns[0]
        rows: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            record = dict(zip(columns, row, strict=True))
            rows[str(record[pk])] = record
        out[table] = rows
    return out


def diff_snapshots(
    before: dict[str, dict[str, dict[str, Any]]],
    after: dict[str, dict[str, dict[str, Any]]],
) -> StateDiff:
    changes: list[RowChange] = []
    for table in sorted(set(before) | set(after)):
        old = before.get(table, {})
        new = after.get(table, {})
        for key in sorted(set(new) - set(old)):
            changes.append(RowChange(table, "added", key, after=new[key]))
        for key in sorted(set(old) - set(new)):
            changes.append(RowChange(table, "removed", key, before=old[key]))
        for key in sorted(set(old) & set(new)):
            if _normalise(old[key]) != _normalise(new[key]):
                changes.append(RowChange(table, "changed", key, before=old[key], after=new[key]))
    return StateDiff(changes)


def db_digest(conn: sqlite3.Connection) -> str:
    """SHA-256 over every row, ordered by (table, primary key).

    Build the same world twice and this must be identical, or the determinism
    test fails. A world that is not byte-reproducible cannot be ground truth.
    """
    hasher = hashlib.sha256()
    for table in table_names(conn):
        hasher.update(f"\n##{table}\n".encode())
        cursor = conn.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cursor.description]
        hasher.update(("|".join(columns) + "\n").encode())
        rows = [dict(zip(columns, r, strict=True)) for r in cursor.fetchall()]
        rows.sort(key=lambda r: str(r[columns[0]]))
        for row in rows:
            hasher.update(json.dumps(_normalise(row), sort_keys=True, default=str).encode() + b"\n")
    return hasher.hexdigest()


# ----------------------------------------------------------------- currency --


def cents(amount: float) -> int:
    """Money is integer cents everywhere. Floats do not survive a state diff."""
    return round(amount * 100)


def format_money(value: int) -> str:
    return f"${value / 100:,.2f}"


def add_days(days: int, base: dt.date = BASE_DATE) -> dt.date:
    return base + dt.timedelta(days=days)


def iso(date: dt.date) -> str:
    return date.isoformat()


def parse_iso(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def paginate(rows: Sequence[Any], limit: int | None, offset: int = 0) -> list[Any]:
    limit = 20 if limit is None else max(1, min(int(limit), 100))
    return list(rows[offset : offset + limit])
