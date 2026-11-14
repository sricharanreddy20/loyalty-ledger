# Data dictionary

Every table in PostgreSQL (`db/schema.sql`) and in the exports (`output/<scale>/csv|json|sql`). Column names and IDs are identical everywhere.

**How to read it**
- **Req:** `Y` means required (NOT NULL); `–` means optional.
- **Units:** amounts are in INR (`NUMERIC(12,2)`, 2 decimals, rounded half-up). Points are whole numbers (`INT`). Timestamps are local `TIMESTAMP`s with no time zone. Dates cover calendar year 2026.
- **Validation:** names the SQL check in [`backend/app/validation.py`](../backend/app/validation.py) that covers the field (`GET /validation`). Every table is also covered by `pk_unique`, `fk_integrity`, `enums_allowed` and `id_formats`.
- **Schema rules:** "CHECK" means the database itself enforces the rule. The schema enforces only structure (types, keys, allowed values, signs). Business rules are checked by validation, so labelled anomalies can still be loaded.

Tests guard this document against drift: [`tests/api/test_1_reads.py`](../tests/api/test_1_reads.py) fails if a column is missing here, or listed here but not in the database.

## `tier`
Configured tiers (from `core/config.py`). 4 rows.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `tier_id` | TEXT | Y | `TIER-<CODE>`, e.g. `TIER-GOLD`; CHECK = `'TIER-' \|\| code` | – | PK | `id_formats` |
| `code` | TEXT | Y | BRONZE, SILVER, GOLD, PLATINUM | – | yes | – |
| `rank` | INT | Y | ≥ 1; 1 = lowest | – | yes | used by `tier_changes_upgrade_in_chain` |
| `min_qualifying_spend` | NUMERIC(12,2) | Y | ≥ 0: 0 / 25,000 / 75,000 / 2,00,000 | INR | – | `A4_account_tier_follows_thresholds` |
| `earn_rate` | NUMERIC(5,2) | Y | > 0: 1 / 1.25 / 1.5 / 2 | points per ₹100 eligible net | – | `accrual_rate_is_tier_in_effect` |

## `rule`
Configured business rules with effective dates. 9 rows. Validation reads the excluded categories, return window and lot expiry from this table.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `rule_id` | TEXT | Y | `RULE-0001` | – | PK | `id_formats` |
| `rule_type` | TEXT | Y | EARN_RATE, CATEGORY_EXCLUSION, LOT_EXPIRY, RETURN_WINDOW | – | – | `enums_allowed` |
| `tier_id` | TEXT | – | FK → `tier`; required when `rule_type` = EARN_RATE (CHECK) | – | – | `fk_integrity` |
| `category` | TEXT | – | required when `rule_type` = CATEGORY_EXCLUSION (CHECK), e.g. GIFT_CARD | – | – | – |
| `value` | NUMERIC(12,2) | – | rate or number of days | given by `unit` | – | `lot_dates`, `return_within_window` |
| `unit` | TEXT | – | e.g. POINTS_PER_100_INR, DAYS | – | – | – |
| `description` | TEXT | Y | free text | – | – | – |
| `effective_from` | DATE | Y | – | date | – | – |
| `effective_to` | DATE | – | NULL = open-ended; ≥ `effective_from` (CHECK) | date | – | – |

## `customer`
| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `customer_id` | TEXT | Y | `CUST-000001` | – | PK | `id_formats` |
| `first_name` | TEXT | Y | synthetic name | – | – | – |
| `last_name` | TEXT | Y | synthetic name | – | – | – |
| `email` | TEXT | Y | synthetic, `@example.com` | – | yes (case-insensitive) | `unique_customer_email` |
| `city` | TEXT | Y | Indian city name | – | – | – |
| `signup_date` | DATE | Y | 2024-01-01 … 2026-11-30 (some members joined before 2026; their 2026 activity starts at zero) | date | – | – |

## `loyalty_account`
One account per customer.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `loy_id` | TEXT | Y | `LOY-<customer_id>`, e.g. `LOY-CUST-000001` (CHECK) | – | PK | `id_formats`, `derived_ids` |
| `customer_id` | TEXT | Y | FK → `customer` | – | yes (1:1) | `fk_integrity` |
| `tier_id` | TEXT | Y | FK → `tier`; the current tier | – | – | `account_tier_is_latest_change`, `A4_account_tier_follows_thresholds` |
| `opened_at` | DATE | Y | = the customer's `signup_date` | date | – | `order_after_account_opened` |
| `current_balance` | INT | Y | a stored copy of the ledger sum (deliberately no sign check) | points | – | `A1_balance_equals_ledger` |

## `tier_change`
An upgrade event. History is never overwritten, and there are no downgrades.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `tier_change_id` | TEXT | Y | `TCH-2026-000001` | – | PK | `id_formats` |
| `loy_id` | TEXT | Y | FK → `loyalty_account` | – | – | `fk_integrity` |
| `old_tier_id` | TEXT | Y | FK → `tier`; the previous change's new tier, else the lowest tier | – | – | `tier_changes_upgrade_in_chain` |
| `new_tier_id` | TEXT | Y | FK → `tier`; ranked higher than `old_tier_id`; ≠ old (CHECK) | – | – | `tier_changes_upgrade_in_chain`, `A4_tier_change_earned` |
| `effective_date` | DATE | Y | the day after the threshold was crossed; 2026-01-02 … 2027-01-01 | date | – | `dates_in_program_year`, `A4_tier_change_earned` |
| `qualifying_spend_at_change` | NUMERIC(12,2) | Y | ≥ 0 (CHECK); qualifying spend right after the order that crossed | INR | – | – |

## `reward`
The reward catalogue. 10 rows.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `reward_id` | TEXT | Y | `RWD-0001` | – | PK | `id_formats` |
| `name` | TEXT | Y | free text | – | – | – |
| `points_cost` | INT | Y | > 0 (CHECK) | points | – | – |
| `active` | BOOLEAN | Y | default true | – | – | – |

## `expiry_batch`
Monthly expiry runs: on the 1st of each month, plus an optional 2027-01-01 run that closes the year through the API.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `exp_id` | TEXT | Y | `EXP-2026-000001` (the 2026 prefix is kept for the 2027-01-01 run) | – | PK | `id_formats` |
| `run_date` | DATE | Y | the 1st of a month, 2026-01-01 … 2027-01-01 | date | yes | `batch_on_first_of_month`, `one_batch_per_run_date` |
| `executed_at` | TIMESTAMP | Y | `run_date` 00:05 | – | – | `batch_on_first_of_month`, `expiry_on_time` |

## `orders`
The table is called `orders` because `order` is a reserved word in SQL.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `ord_id` | TEXT | Y | `ORD-2026-000001` | – | PK | `id_formats` |
| `loy_id` | TEXT | Y | FK → `loyalty_account` | – | – | `fk_integrity` |
| `order_ts` | TIMESTAMP | Y | in 2026; on or after `opened_at` | – | – | `dates_in_program_year`, `order_after_account_opened` |
| `channel` | TEXT | Y | STORE, ONLINE | – | – | `enums_allowed` |
| `shipping_amount` | NUMERIC(12,2) | Y | ≥ 0 (CHECK); never earns points | INR | – | – |
| `tax_amount` | NUMERIC(12,2) | Y | ≥ 0 (CHECK); never earns points | INR | – | – |

## `order_line`
| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `line_id` | TEXT | Y | `<ord_id>-NN`, e.g. `ORD-2026-000001-01` (CHECK) | – | PK | `id_formats`, `derived_ids` |
| `ord_id` | TEXT | Y | FK → `orders` | – | (`ord_id`, `line_no`) unique | `fk_integrity` |
| `line_no` | INT | Y | 1 … 99 (CHECK) | – | (`ord_id`, `line_no`) unique | `derived_ids` |
| `sku` | TEXT | Y | synthetic SKU code | – | – | – |
| `category` | TEXT | Y | ELECTRONICS, APPAREL, HOME, BEAUTY, GROCERY, SPORTS, BOOKS, GIFT_CARD (GIFT_CARD is not eligible) | – | – | `enums_allowed`, `accrual_eligible_net` |
| `unit_price` | NUMERIC(12,2) | Y | > 0 (CHECK) | INR | – | `accrual_eligible_net` |
| `qty` | INT | Y | > 0 (CHECK) | units | – | `returned_qty_within_line` |
| `discount` | NUMERIC(12,2) | Y | 0 … `unit_price` × `qty` (CHECK) | INR | – | `accrual_eligible_net` |

## `line_return`
A full or partial return of one order line, within 30 days of the order.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `return_id` | TEXT | Y | `RET-2026-000001` | – | PK | `id_formats` |
| `line_id` | TEXT | Y | FK → `order_line` | – | – | `fk_integrity`, `returned_qty_within_line` |
| `return_ts` | TIMESTAMP | Y | after the order, at most 30 days later (day 30 is allowed) | – | – | `return_within_window` |
| `qty_returned` | INT | Y | > 0 (CHECK); all returns of a line together ≤ `qty` | units | – | `returned_qty_within_line` |
| `refund_amount` | NUMERIC(12,2) | Y | ≥ 0 (CHECK); the returned share of the line's net amount (discount split in proportion) | INR | – | `refund_amount` |

## `redemption`
Every redemption request, approved or rejected.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `rdm_id` | TEXT | Y | `RDM-2026-000001` | – | PK | `id_formats` |
| `loy_id` | TEXT | Y | FK → `loyalty_account` | – | – | `fk_integrity` |
| `reward_id` | TEXT | Y | FK → `reward` | – | – | `fk_integrity` |
| `requested_at` | TIMESTAMP | Y | in 2026; processed in (`requested_at`, `rdm_id`) order | – | – | `dates_in_program_year`, `redeemed_lot_was_available` |
| `points_cost` | INT | Y | > 0 (CHECK); the reward's cost at request time | points | – | `redemption_ledger_amount`, `redemption_lots_cover_cost`, `approved_within_available` |
| `status` | TEXT | Y | APPROVED, REJECTED | – | – | `enums_allowed`, `redemption_status_vs_ledger` |
| `reject_reason` | TEXT | – | INSUFFICIENT_POINTS; set if and only if REJECTED (CHECK) | – | – | `enums_allowed` |

## `points_transaction`
The ledger: one signed row per point movement, never updated. An ACCRUAL row is also a lot.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `pts_id` | TEXT | Y | `PTS-2026-00000001` (8 digits) | – | PK | `id_formats` |
| `loy_id` | TEXT | Y | FK → `loyalty_account` | – | – | `fk_integrity`, `ledger_refs_by_type` |
| `txn_type` | TEXT | Y | ACCRUAL, REVERSAL, REDEMPTION, EXPIRY | – | – | `enums_allowed` |
| `points` | INT | Y | ACCRUAL > 0; all others < 0 (CHECK) | points | – | `accrual_points`, `reversal_amount`, `redemption_ledger_amount` |
| `txn_ts` | TIMESTAMP | Y | in 2026 (EXPIRY may be dated 2027-01-01) | – | – | `dates_in_program_year`, `accrual_on_order_time` |
| `line_id` | TEXT | – | ACCRUAL only (required); FK → `order_line` | – | one ACCRUAL per line (unique index) | `one_accrual_per_line`, `every_earning_line_accrued` |
| `eligible_net` | NUMERIC(12,2) | – | ACCRUAL only, > 0; `unit_price` × `qty` − `discount` | INR | – | `accrual_eligible_net` |
| `earn_rate` | NUMERIC(5,2) | – | ACCRUAL only; the rate of the tier in effect on the order date | points per ₹100 | – | `accrual_rate_is_tier_in_effect`, `accrual_points` |
| `available_from` | DATE | – | ACCRUAL only; earned + 31 days | date | – | `lot_dates` |
| `expires_at` | DATE | – | ACCRUAL only; earned + 180 days | date | – | `lot_dates`, `expiry_on_time` |
| `return_id` | TEXT | – | REVERSAL only (required); FK → `line_return` | – | one REVERSAL per return | `one_reversal_per_return`, `A2_reversal_references_accrual` |
| `reverses_pts_id` | TEXT | – | REVERSAL only (required); FK → the original ACCRUAL of the same account and line | – | – | `A2_reversal_references_accrual`, `reversal_while_pending` |
| `rdm_id` | TEXT | – | REDEMPTION only (required); FK → `redemption` | – | – | `redemption_status_vs_ledger` |
| `exp_id` | TEXT | – | EXPIRY only (required); FK → `expiry_batch` | – | – | `expiry_on_time` |
| `expires_pts_id` | TEXT | – | EXPIRY only (required); FK → the expired ACCRUAL lot | – | at most once (validation, not a constraint) | `A3_no_lot_expired_twice`, `A3_rerun_adds_nothing` |

## `redemption_lot`
Bridge table: which lots each approved redemption consumed, oldest expiry first (FIFO).

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `rdm_id` | TEXT | Y | FK → `redemption` (APPROVED only) | – | PK (`rdm_id`, `lot_pts_id`) | `redemption_status_vs_ledger` |
| `lot_pts_id` | TEXT | Y | FK → an ACCRUAL of the same account, available on the request date | – | PK (`rdm_id`, `lot_pts_id`) | `ledger_refs_by_type`, `redeemed_lot_was_available` |
| `points_used` | INT | Y | > 0 (CHECK); a redemption's rows add up to its `points_cost` | points | – | `redemption_lots_cover_cost`, `lot_remaining_not_negative` |

## `eval.ground_truth`
Labels for the injected anomalies, kept in a separate schema and separate files (`ground_truth.csv/json`). Never an input to the detector.

| Field | Type | Req | Allowed values / format | Units | Unique | Validation |
|---|---|---|---|---|---|---|
| `record_id` | TEXT | Y | ID of the mutated record | – | PK (`record_id`, `issue_type`) | – |
| `entity` | TEXT | Y | loyalty_account, redemption, line_return, points_transaction, tier_change | – | – | – |
| `issue_type` | TEXT | Y | BALANCE_MISMATCH, REDEMPTION_EXCEEDS_BALANCE, RETURN_KEEPS_POINTS, DOUBLE_EXPIRY, TIER_USES_GROSS_SPEND | – | PK (`record_id`, `issue_type`) | – |
| `description` | TEXT | Y | what was changed | – | – | – |
| `rule_violated` | TEXT | Y | the business rule it breaks | – | – | – |

## Derived: `lot_status(as_of, account)`
A SQL function, not a table. It returns one row per lot, as of a date: `lot_pts_id`, `loy_id`, `earned_at`, `available_from`, `expires_at`, `original`, `reversed`, `redeemed`, `expired`, `remaining` (all in points) and `status`. The status is one of PENDING, AVAILABLE, PARTIAL, CONSUMED, REVERSED or EXPIRED. The precedence between them is documented in `db/schema.sql`. `GET /accounts/{loy_id}/lots` uses it.
