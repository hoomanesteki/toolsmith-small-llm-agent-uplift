-- opsworld: customer operations for a mid-size direct-to-consumer retailer.
--
-- Two conventions run through every world and both exist to keep ground truth
-- reproducible:
--
--   Money is INTEGER cents. Never a float. A float does not survive a state
--   diff intact, and a metric that disagrees with itself on the third decimal
--   place cannot be an oracle.
--
--   Dates are TEXT in ISO form, always computed as an offset from BASE_DATE
--   (2026-01-01). No wall clock appears anywhere, so a task generated today
--   has the same answer in a year.

PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id           TEXT    PRIMARY KEY,
    name                  TEXT    NOT NULL,
    email                 TEXT    NOT NULL UNIQUE,
    region                TEXT    NOT NULL,      -- EMEA | AMER | APAC | LATAM
    tier                  TEXT    NOT NULL,      -- free | standard | premium | enterprise
    signup_date           TEXT    NOT NULL,
    lifetime_value_cents  INTEGER NOT NULL DEFAULT 0,
    notes                 TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE orders (
    order_id        TEXT    PRIMARY KEY,
    customer_id     TEXT    NOT NULL REFERENCES customers(customer_id),
    placed_date     TEXT    NOT NULL,
    status          TEXT    NOT NULL,            -- pending | processing | shipped | delivered | cancelled | returned
    total_cents     INTEGER NOT NULL,
    shipping_cents  INTEGER NOT NULL DEFAULT 0,
    item_count      INTEGER NOT NULL DEFAULT 1,
    items           TEXT    NOT NULL DEFAULT '[]',
    carrier         TEXT,
    delivered_date  TEXT,
    notes           TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE tickets (
    ticket_id     TEXT    PRIMARY KEY,
    customer_id   TEXT    NOT NULL REFERENCES customers(customer_id),
    order_id      TEXT             REFERENCES orders(order_id),
    subject       TEXT    NOT NULL,
    body          TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL,              -- open | pending_customer | escalated | resolved | closed
    priority      TEXT    NOT NULL,              -- low | normal | high | urgent
    assignee      TEXT,
    created_date  TEXT    NOT NULL,
    updated_date  TEXT    NOT NULL,
    resolution    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE refunds (
    refund_id     TEXT    PRIMARY KEY,
    order_id      TEXT    NOT NULL REFERENCES orders(order_id),
    customer_id   TEXT    NOT NULL REFERENCES customers(customer_id),
    amount_cents  INTEGER NOT NULL,
    reason        TEXT    NOT NULL,
    issued_date   TEXT    NOT NULL,
    approved_by   TEXT    NOT NULL DEFAULT 'policy_engine'
);

-- The domain's own rules, readable by the agent. A policy question is only
-- answerable if the policy is data rather than something baked into a prompt.
CREATE TABLE refund_policies (
    policy_id       TEXT    PRIMARY KEY,
    tier            TEXT    NOT NULL UNIQUE,
    window_days     INTEGER NOT NULL,
    max_percent     INTEGER NOT NULL,            -- of order total
    requires_ticket INTEGER NOT NULL,            -- 0 or 1
    body            TEXT    NOT NULL
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_status   ON orders(status);
CREATE INDEX idx_tickets_customer ON tickets(customer_id);
CREATE INDEX idx_refunds_order   ON refunds(order_id);
