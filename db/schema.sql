-- Loyalty points ledger schema (PostgreSQL 16).
--
-- Constraints enforce STRUCTURE only: types, keys, references, allowed values, signs.
-- Business rules (return window, available balance, expire-once, tier thresholds) are
-- checked by validation SQL instead, so labelled anomalies can still be loaded and detected.
--
-- Re-runnable: drops our own objects first.

DROP SCHEMA IF EXISTS eval CASCADE;
DROP FUNCTION IF EXISTS lot_status(date);
DROP TABLE IF EXISTS
    redemption_lot, points_transaction, redemption, reward, expiry_batch,
    line_return, order_line, orders, tier_change, loyalty_account, customer, rule, tier
    CASCADE;

-- ---------------------------------------------------------------- master data

CREATE TABLE tier (
    tier_id              TEXT PRIMARY KEY CHECK (tier_id ~ '^TIER-[A-Z]+$'),
    code                 TEXT NOT NULL UNIQUE,
    rank                 INT  NOT NULL UNIQUE CHECK (rank >= 1),
    min_qualifying_spend NUMERIC(12,2) NOT NULL CHECK (min_qualifying_spend >= 0),
    earn_rate            NUMERIC(5,2)  NOT NULL CHECK (earn_rate > 0),   -- points per 100 INR
    CHECK (tier_id = 'TIER-' || code)
);

CREATE TABLE rule (
    rule_id        TEXT PRIMARY KEY CHECK (rule_id ~ '^RULE-[0-9]{4}$'),
    rule_type      TEXT NOT NULL CHECK (rule_type IN
                       ('EARN_RATE', 'CATEGORY_EXCLUSION', 'LOT_EXPIRY', 'RETURN_WINDOW')),
    tier_id        TEXT REFERENCES tier,          -- EARN_RATE only
    category       TEXT,                          -- CATEGORY_EXCLUSION only
    value          NUMERIC(12,2),
    unit           TEXT,                          -- e.g. POINTS_PER_100_INR, DAYS
    description    TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to   DATE,                          -- NULL = open-ended
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CHECK (rule_type <> 'EARN_RATE' OR tier_id IS NOT NULL),
    CHECK (rule_type <> 'CATEGORY_EXCLUSION' OR category IS NOT NULL)
);

CREATE TABLE customer (
    customer_id TEXT PRIMARY KEY CHECK (customer_id ~ '^CUST-[0-9]{6}$'),
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    city        TEXT NOT NULL,
    signup_date DATE NOT NULL
);

CREATE TABLE loyalty_account (
    loy_id          TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL UNIQUE REFERENCES customer,   -- 1:1 with customer
    tier_id         TEXT NOT NULL REFERENCES tier,              -- current tier
    opened_at       DATE NOT NULL,
    current_balance INT  NOT NULL DEFAULT 0,  -- snapshot of the ledger sum (no sign check)
    CHECK (loy_id = 'LOY-' || customer_id)
);

CREATE TABLE tier_change (
    tier_change_id             TEXT PRIMARY KEY CHECK (tier_change_id ~ '^TCH-2026-[0-9]{6}$'),
    loy_id                     TEXT NOT NULL REFERENCES loyalty_account,
    old_tier_id                TEXT NOT NULL REFERENCES tier,
    new_tier_id                TEXT NOT NULL REFERENCES tier,
    effective_date             DATE NOT NULL,
    qualifying_spend_at_change NUMERIC(12,2) NOT NULL CHECK (qualifying_spend_at_change >= 0),
    CHECK (old_tier_id <> new_tier_id)
);

CREATE TABLE reward (
    reward_id   TEXT PRIMARY KEY CHECK (reward_id ~ '^RWD-[0-9]{4}$'),
    name        TEXT NOT NULL,
    points_cost INT  NOT NULL CHECK (points_cost > 0),
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE expiry_batch (
    exp_id      TEXT PRIMARY KEY CHECK (exp_id ~ '^EXP-2026-[0-9]{6}$'),
    run_date    DATE NOT NULL UNIQUE,      -- one batch per run date
    executed_at TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------- orders and returns

CREATE TABLE orders (                      -- "order" is a reserved word
    ord_id          TEXT PRIMARY KEY CHECK (ord_id ~ '^ORD-2026-[0-9]{6}$'),
    loy_id          TEXT NOT NULL REFERENCES loyalty_account,
    order_ts        TIMESTAMP NOT NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('STORE', 'ONLINE')),
    shipping_amount NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (shipping_amount >= 0),  -- never earns
    tax_amount      NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (tax_amount >= 0)        -- never earns
);

CREATE TABLE order_line (
    line_id    TEXT PRIMARY KEY,
    ord_id     TEXT NOT NULL REFERENCES orders,
    line_no    INT  NOT NULL CHECK (line_no BETWEEN 1 AND 99),
    sku        TEXT NOT NULL,
    category   TEXT NOT NULL CHECK (category IN ('ELECTRONICS', 'APPAREL', 'HOME', 'BEAUTY',
                                                 'GROCERY', 'SPORTS', 'BOOKS', 'GIFT_CARD')),
    unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    qty        INT NOT NULL CHECK (qty > 0),
    discount   NUMERIC(12,2) NOT NULL DEFAULT 0,
    UNIQUE (ord_id, line_no),
    CHECK (line_id = ord_id || '-' || lpad(line_no::text, 2, '0')),
    CHECK (discount >= 0 AND discount <= unit_price * qty)
);

CREATE TABLE line_return (
    return_id     TEXT PRIMARY KEY CHECK (return_id ~ '^RET-2026-[0-9]{6}$'),
    line_id       TEXT NOT NULL REFERENCES order_line,
    return_ts     TIMESTAMP NOT NULL,
    qty_returned  INT NOT NULL CHECK (qty_returned > 0),
    refund_amount NUMERIC(12,2) NOT NULL CHECK (refund_amount >= 0)
);

-- ---------------------------------------------------------------- redemptions

CREATE TABLE redemption (
    rdm_id        TEXT PRIMARY KEY CHECK (rdm_id ~ '^RDM-2026-[0-9]{6}$'),
    loy_id        TEXT NOT NULL REFERENCES loyalty_account,
    reward_id     TEXT NOT NULL REFERENCES reward,
    requested_at  TIMESTAMP NOT NULL,
    points_cost   INT  NOT NULL CHECK (points_cost > 0),   -- cost at request time
    status        TEXT NOT NULL CHECK (status IN ('APPROVED', 'REJECTED')),
    reject_reason TEXT CHECK (reject_reason IN ('INSUFFICIENT_POINTS')),
    CHECK ((status = 'REJECTED') = (reject_reason IS NOT NULL))
);

-- ---------------------------------------------------------------- the ledger

-- One table for all point movements. An ACCRUAL row is also a "lot".
CREATE TABLE points_transaction (
    pts_id          TEXT PRIMARY KEY CHECK (pts_id ~ '^PTS-2026-[0-9]{8}$'),
    loy_id          TEXT NOT NULL REFERENCES loyalty_account,
    txn_type        TEXT NOT NULL CHECK (txn_type IN ('ACCRUAL', 'REVERSAL', 'REDEMPTION', 'EXPIRY')),
    points          INT  NOT NULL,
    txn_ts          TIMESTAMP NOT NULL,
    -- ACCRUAL (lot) fields
    line_id         TEXT REFERENCES order_line,
    eligible_net    NUMERIC(12,2),
    earn_rate       NUMERIC(5,2),
    available_from  DATE,
    expires_at      DATE,
    -- REVERSAL fields
    return_id       TEXT REFERENCES line_return,
    reverses_pts_id TEXT REFERENCES points_transaction,
    -- REDEMPTION field
    rdm_id          TEXT REFERENCES redemption,
    -- EXPIRY fields (no UNIQUE on expires_pts_id: expire-once is a validation rule)
    exp_id          TEXT REFERENCES expiry_batch,
    expires_pts_id  TEXT REFERENCES points_transaction,

    CHECK (txn_type <> 'ACCRUAL' OR (
        points > 0 AND line_id IS NOT NULL AND eligible_net > 0 AND earn_rate > 0
        AND available_from IS NOT NULL AND expires_at IS NOT NULL
        AND txn_ts::date <= available_from AND available_from < expires_at)),
    CHECK (txn_type <> 'REVERSAL'   OR (points < 0 AND reverses_pts_id IS NOT NULL AND return_id IS NOT NULL)),
    CHECK (txn_type <> 'REDEMPTION' OR (points < 0 AND rdm_id IS NOT NULL)),
    CHECK (txn_type <> 'EXPIRY'     OR (points < 0 AND exp_id IS NOT NULL AND expires_pts_id IS NOT NULL))
);

-- Bridge: which lots each approved redemption consumed (N:M).
CREATE TABLE redemption_lot (
    rdm_id      TEXT NOT NULL REFERENCES redemption,
    lot_pts_id  TEXT NOT NULL REFERENCES points_transaction,
    points_used INT  NOT NULL CHECK (points_used > 0),
    PRIMARY KEY (rdm_id, lot_pts_id)
);

-- ---------------------------------------------------------------- indexes

CREATE UNIQUE INDEX one_accrual_per_line ON points_transaction (line_id) WHERE txn_type = 'ACCRUAL';
CREATE INDEX pt_account_time   ON points_transaction (loy_id, txn_ts);
CREATE INDEX pt_reverses       ON points_transaction (reverses_pts_id);
CREATE INDEX pt_expires        ON points_transaction (expires_pts_id);
CREATE INDEX pt_redemption     ON points_transaction (rdm_id);
CREATE INDEX orders_account    ON orders (loy_id, order_ts);
CREATE INDEX line_order        ON order_line (ord_id);
CREATE INDEX return_line       ON line_return (line_id);
CREATE INDEX redemption_acct   ON redemption (loy_id, requested_at);
CREATE INDEX redlot_lot        ON redemption_lot (lot_pts_id);
CREATE INDEX tier_change_acct  ON tier_change (loy_id);

-- ---------------------------------------------------------------- lot status as of a date

-- One row per lot earned on or before as_of. Status precedence:
-- EXPIRED (by a batch) > REVERSED (fully) > CONSUMED (nothing left) > EXPIRED (due, awaiting batch)
-- > PENDING (inside return window) > PARTIAL (partly redeemed) > AVAILABLE.
CREATE FUNCTION lot_status(as_of date DEFAULT DATE '2026-12-31')
RETURNS TABLE (
    lot_pts_id TEXT, loy_id TEXT, earned_at TIMESTAMP, available_from DATE, expires_at DATE,
    original INT, reversed INT, redeemed INT, expired INT, remaining INT, status TEXT
)
LANGUAGE sql STABLE AS $$
    WITH lots AS (
        SELECT a.pts_id, a.loy_id, a.txn_ts, a.available_from, a.expires_at, a.points,
               COALESCE((SELECT -SUM(r.points) FROM points_transaction r
                         WHERE r.txn_type = 'REVERSAL' AND r.reverses_pts_id = a.pts_id
                           AND r.txn_ts::date <= as_of), 0)::int AS reversed,
               COALESCE((SELECT SUM(rl.points_used) FROM redemption_lot rl
                         JOIN redemption d ON d.rdm_id = rl.rdm_id
                         WHERE rl.lot_pts_id = a.pts_id AND d.status = 'APPROVED'
                           AND d.requested_at::date <= as_of), 0)::int AS redeemed,
               COALESCE((SELECT -SUM(e.points) FROM points_transaction e
                         WHERE e.txn_type = 'EXPIRY' AND e.expires_pts_id = a.pts_id
                           AND e.txn_ts::date <= as_of), 0)::int AS expired
        FROM points_transaction a
        WHERE a.txn_type = 'ACCRUAL' AND a.txn_ts::date <= as_of
    )
    SELECT pts_id, loy_id, txn_ts, available_from, expires_at, points,
           reversed, redeemed, expired,
           points - reversed - redeemed - expired AS remaining,
           CASE
               WHEN expired > 0                                THEN 'EXPIRED'
               WHEN reversed >= points                         THEN 'REVERSED'
               WHEN points - reversed - redeemed - expired <= 0 THEN 'CONSUMED'
               WHEN as_of >= expires_at                        THEN 'EXPIRED'
               WHEN as_of < available_from                     THEN 'PENDING'
               WHEN redeemed > 0                               THEN 'PARTIAL'
               ELSE 'AVAILABLE'
           END AS status
    FROM lots
$$;

-- ---------------------------------------------------------------- evaluation (never an input)

CREATE SCHEMA eval;

CREATE TABLE eval.ground_truth (
    record_id     TEXT NOT NULL,     -- id of the mutated record (account, redemption, return, ...)
    entity        TEXT NOT NULL CHECK (entity IN ('loyalty_account', 'redemption', 'line_return',
                                                  'points_transaction', 'tier_change')),
    issue_type    TEXT NOT NULL CHECK (issue_type IN ('BALANCE_MISMATCH', 'REDEMPTION_EXCEEDS_BALANCE',
                                                      'RETURN_KEEPS_POINTS', 'DOUBLE_EXPIRY',
                                                      'TIER_USES_GROSS_SPEND')),
    description   TEXT NOT NULL,
    rule_violated TEXT NOT NULL,
    PRIMARY KEY (record_id, issue_type)
);
