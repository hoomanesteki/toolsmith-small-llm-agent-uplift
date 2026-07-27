"""docworld: an internal documentation estate. The grounding domain.

Retrieval evaluations usually judge faithfulness, which means a model is
grading plausibility. docworld avoids that by generating the document from the
fact rather than the fact from the document: every section carries a structured
``(fact_key, fact_value)`` pair and its prose is rendered from that pair.

The consequences are worth stating plainly.

* **Correctness is executed.** The right answer is a column.
* **Citations are counted.** The right citation is a specific ``section_id``,
  so precision and recall are arithmetic rather than opinion.
* **Staleness is a distinct failure.** Roughly a third of policies exist in two
  versions with different values, so citing the superseded one is detectable and
  is reported separately from getting the number wrong.
* **Lexical retrieval fails informatively.** Headings repeat across services, so
  keyword search alone returns the right heading from the wrong service. An
  agent that does not narrow by service will confidently answer about Atlas when
  asked about Beacon.

It also carries a privileged verb that moves no money. Publishing a draft is
gated exactly like a refund, which is the point: every domain has an action you
would not want an agent to take because a retrieved document told it to.
"""

from __future__ import annotations

from pathlib import Path

from toolsmith.worlds._common import CALCULATOR_TOOL, TODAY_TOOL
from toolsmith.worlds.base import Entity, Verb, WorldSpec
from toolsmith.worlds.doc.samplers import SAMPLERS
from toolsmith.worlds.doc.seed import seed_doc
from toolsmith.worlds.doc.tools import TOOLS, publication_policy

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

ENTITIES = [
    Entity(
        table="services",
        label="Service",
        description="What the documentation is about. Names repeat their stems on purpose.",
        primary_key="service_id",
        columns={
            "service_id": "SVC-### identifier",
            "name": "Two-word name; stems recur across categories",
            "category": "platform, data, security or product",
            "owner_team": "Who answers questions",
            "status": "active, deprecated or planned",
        },
    ),
    Entity(
        table="documents",
        label="Document",
        description="A policy, runbook, FAQ or design note. Some exist in two versions.",
        primary_key="doc_id",
        columns={
            "doc_id": "DOC-#### identifier",
            "kind": "policy, runbook, faq or design_note",
            "version": "1 or 2",
            "status": "current, superseded or draft",
            "superseded_by": "The newer document, if this one is stale",
        },
    ),
    Entity(
        table="sections",
        label="Section",
        description="The citation unit. Carries the fact the prose was rendered from.",
        primary_key="section_id",
        columns={
            "section_id": "SEC-#### identifier. This is what a correct answer cites.",
            "heading": "Repeats across services, which is what makes retrieval hard",
            "body": "Rendered from fact_key and fact_value",
            "fact_key": "retention_days, sla_hours, review_cycle_months, and so on",
            "fact_value": "The executable ground truth",
        },
    ),
    Entity(
        table="doc_issues",
        label="Documentation issue",
        description="A work item against a document. Some kinds need one accepted before publication.",
        primary_key="issue_id",
        columns={
            "issue_id": "ISS-### identifier",
            "status": "open, triaged, accepted, rejected or closed",
        },
    ),
    Entity(
        table="changelog",
        label="Changelog entry",
        description="Publication history, appended on every publish.",
        primary_key="entry_id",
        columns={"entry_id": "CHG-#### identifier", "changed_date": "ISO date"},
    ),
    Entity(
        table="publication_rules",
        label="Publication rule",
        description="The rules, as data. One row per document kind.",
        primary_key="rule_id",
        columns={
            "kind": "Which documents it governs",
            "requires_review": "1 if an accepted issue must exist before publishing",
            "max_draft_age_days": "How long a draft may sit before re-review",
        },
    ),
]

WORLD = WorldSpec(
    key="doc",
    title="docworld",
    tagline="An internal documentation estate, where citations are checkable.",
    role="grounding",
    schema_sql=SCHEMA_SQL,
    seed=seed_doc,
    entities=ENTITIES,
    tools={**TOOLS, Verb.CALCULATOR: CALCULATOR_TOOL, Verb.TODAY: TODAY_TOOL},
    samplers=SAMPLERS,
    lexicon={
        "principal": "service",
        "principal_plural": "services",
        "principal_id": "service_id",
        "record": "document",
        "record_plural": "documents",
        "record_id": "doc_id",
        "case": "documentation issue",
        "case_plural": "documentation issues",
        "privileged_action": "publication",
        "policy_noun": "publication rule",
        "principal_group": "category",
        "record_status_done": "current",
    },
    policy=publication_policy,
    default_seed=20260303,
    notes=(
        "Grounding domain. Carries the retrieval tier: citation precision and recall, "
        "faithfulness, and stale-version detection. Its privileged verb is publish_document."
    ),
)

__all__ = ["WORLD"]
