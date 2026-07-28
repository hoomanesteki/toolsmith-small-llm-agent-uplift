-- clinicworld: outpatient scheduling and billing for a four-site practice.
--
-- Same verb grammar as opsworld. Different nouns, different columns,
-- different rules, and a policy table whose shape deliberately does not match
-- opsworld's: coverage is keyed by plan AND service band, not by a single tier.
--
-- That mismatch is the point. A model that learned "look up the tier row" in
-- training has to actually read this schema rather than pattern-match it, which
-- is what makes the ops-to-clinic gap a generalisation measurement instead of a
-- memorisation one.
--
-- Money is INTEGER cents. Dates are ISO offsets from BASE_DATE (2026-01-01).
-- No real patient data exists or could exist here: every row is generated from
-- a seeded RNG over the word pools in worlds/_words.py.

PRAGMA foreign_keys = ON;

CREATE TABLE patients (
    patient_id      TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    email           TEXT    NOT NULL UNIQUE,
    site            TEXT    NOT NULL,          -- Ashgrove | Belmont | Carrow | Dunmore
    coverage_plan   TEXT    NOT NULL,          -- basic | standard | extended | public
    registered_date TEXT    NOT NULL,
    balance_cents   INTEGER NOT NULL DEFAULT 0,
    notes           TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE providers (
    provider_id  TEXT    PRIMARY KEY,
    name         TEXT    NOT NULL,
    specialty    TEXT    NOT NULL,
    site         TEXT    NOT NULL,
    accepting    INTEGER NOT NULL DEFAULT 1     -- 0 or 1
);

CREATE TABLE appointments (
    appointment_id TEXT    PRIMARY KEY,
    patient_id     TEXT    NOT NULL REFERENCES patients(patient_id),
    provider_id    TEXT    NOT NULL REFERENCES providers(provider_id),
    scheduled_date TEXT    NOT NULL,
    status         TEXT    NOT NULL,            -- scheduled | checked_in | completed | cancelled | no_show
    reason         TEXT    NOT NULL,
    service_band   TEXT    NOT NULL,            -- routine | specialist | procedure
    charge_cents   INTEGER NOT NULL DEFAULT 0,
    covered_cents  INTEGER NOT NULL DEFAULT 0,
    notes          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE referrals (
    referral_id    TEXT    PRIMARY KEY,
    patient_id     TEXT    NOT NULL REFERENCES patients(patient_id),
    appointment_id TEXT             REFERENCES appointments(appointment_id),
    specialty      TEXT    NOT NULL,
    summary        TEXT    NOT NULL,
    status         TEXT    NOT NULL,            -- open | awaiting_triage | accepted | declined | closed
    urgency        TEXT    NOT NULL,            -- low | normal | high | urgent
    assignee       TEXT,
    created_date   TEXT    NOT NULL,
    updated_date   TEXT    NOT NULL,
    outcome        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE adjustments (
    adjustment_id  TEXT    PRIMARY KEY,
    appointment_id TEXT    NOT NULL REFERENCES appointments(appointment_id),
    patient_id     TEXT    NOT NULL REFERENCES patients(patient_id),
    amount_cents   INTEGER NOT NULL,
    reason         TEXT    NOT NULL,
    issued_date    TEXT    NOT NULL,
    approved_by    TEXT    NOT NULL DEFAULT 'policy_engine'
);

-- Coverage is keyed by (plan, service_band). Two dimensions, unlike opsworld's
-- one. A referral is required for the specialist and procedure bands on some
-- plans and not others.
CREATE TABLE coverage_policies (
    policy_id         TEXT    PRIMARY KEY,
    coverage_plan     TEXT    NOT NULL,
    service_band      TEXT    NOT NULL,
    covered_percent   INTEGER NOT NULL,
    claim_window_days INTEGER NOT NULL,
    requires_referral INTEGER NOT NULL,        -- 0 or 1
    body              TEXT    NOT NULL,
    UNIQUE (coverage_plan, service_band)
);

CREATE INDEX idx_appt_patient  ON appointments(patient_id);
CREATE INDEX idx_appt_status   ON appointments(status);
CREATE INDEX idx_referral_pat  ON referrals(patient_id);
CREATE INDEX idx_adj_appt      ON adjustments(appointment_id);
