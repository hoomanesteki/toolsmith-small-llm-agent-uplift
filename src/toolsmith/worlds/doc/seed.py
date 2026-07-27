"""Populate docworld deterministically.

The generation runs backwards from the answer. A fact is drawn first,
``("retention_days", "90")``, and the prose is rendered from it. That is what
makes a retrieval answer checkable by code: the correct value is a column and
the correct citation is a specific ``section_id``.

Two deliberate hazards are planted, because a retrieval evaluation with neither
is measuring the easy case:

* **Superseded documents keep their old values.** Version 1 says 30 days,
  version 2 says 90. Citing v1 is a distinct, detectable failure from getting
  the number wrong.
* **Near-duplicate headings across services.** "Retention" appears in a dozen
  documents, so lexical search alone returns the wrong service's answer.
"""

from __future__ import annotations

import random
import sqlite3

from toolsmith.worlds._words import AGENTS, FAMILY_NAMES, GIVEN_NAMES, PRIORITIES
from toolsmith.worlds.base import add_days, iso

#: Deliberately larger than the stem pool, so a dozen stems repeat with a
#: different suffix. Two 'Atlas' services is what makes a question about "the
#: Atlas retention period" genuinely ambiguous rather than merely underspecified.
N_SERVICES = 96
N_ISSUES = 280

CATEGORIES = ("platform", "data", "security", "product")
KINDS = ("policy", "runbook", "faq", "design_note")
DOC_STATUSES = ("current", "superseded", "draft")
ISSUE_STATUSES = ("open", "triaged", "accepted", "rejected", "closed")

SERVICE_STEMS = (
    "Atlas",
    "Beacon",
    "Cinder",
    "Drift",
    "Ember",
    "Fathom",
    "Glint",
    "Harbour",
    "Inlet",
    "Juno",
    "Keel",
    "Lantern",
    "Mosaic",
    "Nimbus",
    "Orchard",
    "Pylon",
    "Quarry",
    "Ridge",
    "Sable",
    "Tidal",
    "Umber",
    "Vault",
    "Willow",
    "Xenon",
    "Yarrow",
    "Zephyr",
    "Anvil",
    "Basalt",
    "Cobalt",
    "Dune",
    "Etch",
    "Fjord",
    "Granite",
    "Hollow",
    "Ivory",
    "Jetty",
)
SERVICE_SUFFIXES = ("Gateway", "Store", "Pipeline", "Registry", "Broker", "Index")

#: fact_key -> (heading, sentence template, value sampler)
FACTS: dict[str, tuple[str, str]] = {
    "retention_days": (
        "Data retention",
        "Records handled by {service} are retained for {value} days, after which they are "
        "purged automatically. Extensions require an accepted documentation issue.",
    ),
    "sla_hours": (
        "Service level",
        "{service} carries a {value}-hour response commitment for incidents raised through "
        "the on-call rota.",
    ),
    "review_cycle_months": (
        "Review cycle",
        "This document is reviewed every {value} months by the owning team.",
    ),
    "escalation_tier": (
        "Escalation",
        "Unresolved incidents on {service} escalate to tier {value} after the response "
        "commitment lapses.",
    ),
    "max_batch_size": (
        "Batch limits",
        "{service} accepts batches of at most {value} records per submission.",
    ),
    "encryption_standard": (
        "Encryption",
        "Data at rest in {service} is encrypted using {value}.",
    ),
}

FACT_VALUES: dict[str, tuple[str, ...]] = {
    "retention_days": ("7", "30", "90", "180", "365", "1095"),
    "sla_hours": ("1", "4", "8", "24", "72"),
    "review_cycle_months": ("3", "6", "12", "24"),
    "escalation_tier": ("1", "2", "3"),
    "max_batch_size": ("500", "1000", "5000", "10000", "50000"),
    "encryption_standard": ("AES-256-GCM", "ChaCha20-Poly1305", "AES-128-CBC"),
}

PUBLICATION_RULES = [
    (
        "PUB-POLICY",
        "policy",
        1,
        30,
        "A policy document may only be published once a documentation issue against it has "
        "been accepted. Drafts older than 30 days must be re-reviewed before publication.",
    ),
    (
        "PUB-RUNBOOK",
        "runbook",
        1,
        14,
        "A runbook may only be published once a documentation issue against it has been "
        "accepted. Drafts older than 14 days must be re-reviewed.",
    ),
    (
        "PUB-FAQ",
        "faq",
        0,
        90,
        "FAQ entries may be published without review. Drafts older than 90 days must be "
        "re-reviewed before publication.",
    ),
    (
        "PUB-DESIGN",
        "design_note",
        0,
        180,
        "Design notes may be published without review and do not expire while in draft.",
    ),
]


def seed_doc(conn: sqlite3.Connection, seed: int) -> None:
    rng = random.Random(seed)

    conn.executemany(
        "INSERT INTO publication_rules (rule_id, kind, requires_review, max_draft_age_days, body) "
        "VALUES (?, ?, ?, ?, ?)",
        PUBLICATION_RULES,
    )

    # -- services -----------------------------------------------------------
    names: list[str] = []
    while len(names) < N_SERVICES:
        candidate = (
            f"{SERVICE_STEMS[len(names) % len(SERVICE_STEMS)]} {rng.choice(SERVICE_SUFFIXES)}"
        )
        if candidate not in names:
            names.append(candidate)

    services = [
        (
            f"SVC-{200 + i}",
            name,
            rng.choice(CATEGORIES),
            f"team-{rng.choice(FAMILY_NAMES).lower()}",
            rng.choices(("active", "deprecated", "planned"), weights=[0.74, 0.16, 0.10])[0],
            f"{name} handles {rng.choice(('ingest', 'routing', 'storage', 'authorisation', 'indexing'))} "
            f"for the {rng.choice(CATEGORIES)} estate.",
        )
        for i, name in enumerate(names)
    ]
    conn.executemany(
        "INSERT INTO services (service_id, name, category, owner_team, status, summary) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        services,
    )

    documents: list[tuple] = []
    supersessions: list[tuple[str, str]] = []
    sections: list[tuple] = []
    changelog: list[tuple] = []
    doc_counter = 0
    section_counter = 0
    entry_counter = 0

    for service_id, service_name, _category, owner, _status, _summary in services:
        # Each service gets two to four documents, and roughly a third of the
        # policies exist in two versions so a stale citation is possible.
        for kind in rng.sample(KINDS, rng.randint(2, 4)):
            versions = 2 if (kind == "policy" and rng.random() < 0.35) else 1
            previous_id: str | None = None
            for version in range(1, versions + 1):
                doc_counter += 1
                doc_id = f"DOC-{3000 + doc_counter}"
                is_latest = version == versions
                if is_latest:
                    status = "draft" if rng.random() < 0.12 else "current"
                else:
                    status = "superseded"
                published = -rng.randint(10, 700) if not is_latest else -rng.randint(0, 300)

                documents.append(
                    (
                        doc_id,
                        service_id,
                        f"{service_name} {kind.replace('_', ' ')}",
                        kind,
                        version,
                        status,
                        iso(add_days(published)),
                        None,
                        f"Version {version} of the {kind.replace('_', ' ')} for {service_name}.",
                    )
                )
                if previous_id is not None:
                    # Recorded, not written yet: the forward reference cannot be
                    # inserted before its target exists.
                    supersessions.append((previous_id, doc_id))

                keys = rng.sample(sorted(FACTS), rng.randint(2, 4))
                for ordinal, fact_key in enumerate(keys, start=1):
                    section_counter += 1
                    heading, template = FACTS[fact_key]
                    value = rng.choice(FACT_VALUES[fact_key])
                    sections.append(
                        (
                            f"SEC-{7000 + section_counter}",
                            doc_id,
                            ordinal,
                            heading,
                            template.format(service=service_name, value=value),
                            fact_key,
                            value,
                        )
                    )
                # A closing section with no fact, so not every section is an answer.
                section_counter += 1
                sections.append(
                    (
                        f"SEC-{7000 + section_counter}",
                        doc_id,
                        len(keys) + 1,
                        "Contacts",
                        f"Questions about {service_name} go to {owner} through the usual channel.",
                        None,
                        None,
                    )
                )

                entry_counter += 1
                changelog.append(
                    (
                        f"CHG-{8000 + entry_counter}",
                        doc_id,
                        iso(add_days(published)),
                        f"{rng.choice(GIVEN_NAMES).lower()}.{rng.choice(FAMILY_NAMES).lower()}",
                        "Initial publication." if version == 1 else "Revised after review.",
                    )
                )
                previous_id = doc_id

    conn.executemany(
        "INSERT INTO documents (doc_id, service_id, title, kind, version, status, "
        "published_date, superseded_by, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        documents,
    )
    conn.executemany(
        "UPDATE documents SET superseded_by = ? WHERE doc_id = ?",
        [(new_id, old_id) for old_id, new_id in supersessions],
    )
    conn.executemany(
        "INSERT INTO sections (section_id, doc_id, ordinal, heading, body, fact_key, fact_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        sections,
    )
    conn.executemany(
        "INSERT INTO changelog (entry_id, doc_id, changed_date, author, summary) "
        "VALUES (?, ?, ?, ?, ?)",
        changelog,
    )

    # -- documentation issues ----------------------------------------------
    doc_ids = [d[0] for d in documents]
    sections_by_doc: dict[str, list[str]] = {}
    for section in sections:
        sections_by_doc.setdefault(section[1], []).append(section[0])

    issues = []
    for i in range(N_ISSUES):
        doc_id = rng.choice(doc_ids)
        section_id = rng.choice(sections_by_doc[doc_id]) if rng.random() < 0.7 else None
        created = -rng.randint(0, 260)
        status = rng.choice(ISSUE_STATUSES)
        issues.append(
            (
                f"ISS-{100 + i}",
                doc_id,
                section_id,
                rng.choice(
                    [
                        "Value contradicts the runbook",
                        "Section is out of date",
                        "Missing escalation contact",
                        "Ambiguous wording",
                        "Broken cross-reference",
                    ]
                ),
                status,
                rng.choice(PRIORITIES),
                rng.choice(AGENTS) if status != "open" else None,
                iso(add_days(created)),
                iso(add_days(min(0, created + rng.randint(0, 30)))),
                "Reviewed." if status in {"accepted", "rejected", "closed"} else "",
            )
        )
    conn.executemany(
        "INSERT INTO doc_issues (issue_id, doc_id, section_id, summary, status, priority, "
        "assignee, created_date, updated_date, resolution) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        issues,
    )
