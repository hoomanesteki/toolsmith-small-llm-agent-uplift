-- docworld: an internal documentation estate. The grounding domain.
--
-- The design problem with retrieval evaluation is that "is this answer
-- faithful?" is usually judged, and a judge grading faithfulness is grading
-- plausibility. So docworld inverts it: every section carries a structured
-- (fact_key, fact_value) pair, and the prose in `body` is RENDERED from that
-- pair. The document is generated from the fact, not the other way around.
--
-- That gives three things no judge can:
--   * the correct answer is a column, so correctness is executed
--   * the correct citation is a specific section_id, so citation precision and
--     recall are counted rather than estimated
--   * a superseded document can carry a stale value on purpose, so citing the
--     wrong version is a detectable, distinct failure mode
--
-- Dates are ISO offsets from BASE_DATE (2026-01-01).

PRAGMA foreign_keys = ON;

CREATE TABLE services (
    service_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    category      TEXT NOT NULL,          -- platform | data | security | product
    owner_team    TEXT NOT NULL,
    status        TEXT NOT NULL,          -- active | deprecated | planned
    summary       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE documents (
    doc_id         TEXT    PRIMARY KEY,
    service_id     TEXT    NOT NULL REFERENCES services(service_id),
    title          TEXT    NOT NULL,
    kind           TEXT    NOT NULL,      -- policy | runbook | faq | design_note
    version        INTEGER NOT NULL DEFAULT 1,
    status         TEXT    NOT NULL,      -- current | superseded | draft
    published_date TEXT    NOT NULL,
    superseded_by  TEXT             REFERENCES documents(doc_id),
    summary        TEXT    NOT NULL DEFAULT ''
);

-- The citation unit. A correct answer cites the section that carries the fact.
CREATE TABLE sections (
    section_id  TEXT    PRIMARY KEY,
    doc_id      TEXT    NOT NULL REFERENCES documents(doc_id),
    ordinal     INTEGER NOT NULL,
    heading     TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    fact_key    TEXT,                     -- retention_days, sla_hours, owner_team, ...
    fact_value  TEXT                      -- the executable ground truth
);

CREATE TABLE changelog (
    entry_id     TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents(doc_id),
    changed_date TEXT NOT NULL,
    author       TEXT NOT NULL,
    summary      TEXT NOT NULL
);

-- Work items raised against the documentation itself.
CREATE TABLE doc_issues (
    issue_id     TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents(doc_id),
    section_id   TEXT          REFERENCES sections(section_id),
    summary      TEXT NOT NULL,
    status       TEXT NOT NULL,           -- open | triaged | accepted | rejected | closed
    priority     TEXT NOT NULL,
    assignee     TEXT,
    created_date TEXT NOT NULL,
    updated_date TEXT NOT NULL,
    resolution   TEXT NOT NULL DEFAULT ''
);

-- Rules about the documents themselves. Publishing is the privileged action in
-- this world, and these rows are what authorise it.
CREATE TABLE publication_rules (
    rule_id            TEXT    PRIMARY KEY,
    kind               TEXT    NOT NULL UNIQUE,
    requires_review    INTEGER NOT NULL,  -- 1 if an accepted doc_issue must exist
    max_draft_age_days INTEGER NOT NULL,
    body               TEXT    NOT NULL
);

CREATE INDEX idx_sections_doc   ON sections(doc_id);
CREATE INDEX idx_sections_fact  ON sections(fact_key);
CREATE INDEX idx_documents_svc  ON documents(service_id);
CREATE INDEX idx_issues_doc     ON doc_issues(doc_id);
