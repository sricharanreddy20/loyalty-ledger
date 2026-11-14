# Validation summary

39 checks: **31 PASS**, **8 EXPECTED_FAIL**, **0 FAIL**.

EXPECTED_FAIL = every violation is on an account carrying a labelled anomaly of the listed type(s).

| Check | Category | Status | Violations | Explained | Ground truth | Description |
|---|---|---|---:|---:|---:|---|
| `pk_unique` | Primary keys | PASS | 0 | 0 |  | Primary key is unique in all 13 tables |
| `fk_integrity` | Foreign keys | PASS | 0 | 0 |  | Every reference points to an existing parent row (20 relationships) |
| `ledger_refs_by_type` | Foreign keys | PASS | 0 | 0 |  | Ledger references match the entry type (reversal/expiry -> ACCRUAL of same account; lots -> same account) |
| `enums_allowed` | Allowed values | PASS | 0 | 0 |  | Enumerated columns hold only allowed values |
| `id_formats` | Allowed values | PASS | 0 | 0 |  | Every id matches its zero-padded format |
| `derived_ids` | Allowed values | PASS | 0 | 0 |  | Derived ids are consistent (LOY-<customer>, <order>-<line_no>) |
| `order_after_account_opened` | Date order | PASS | 0 | 0 |  | Orders are on or after the account was opened |
| `accrual_on_order_time` | Date order | PASS | 0 | 0 |  | An accrual is dated at its order's timestamp |
| `lot_dates` | Date order | PASS | 0 | 0 |  | Lot available_from = earned + return window + 1 day; expires_at = earned + lot expiry days |
| `return_within_window` | Date order | PASS | 0 | 0 |  | A return is after its order and within the return window |
| `reversal_while_pending` | Date order | PASS | 0 | 0 |  | A reversal is dated at its return and happens before the lot becomes available |
| `expiry_on_time` | Date order | PASS | 0 | 0 |  | An expiry is not before the lot's expires_at and is dated at its batch's run |
| `batch_on_first_of_month` | Date order | PASS | 0 | 0 |  | Expiry batches run on the 1st of a month |
| `redeemed_lot_was_available` | Date order | PASS | 0 | 0 |  | Lots used by a redemption were available on the request date |
| `dates_in_program_year` | Date order | PASS | 0 | 0 |  | Business events are in 2026 (expiry batches and tier changes may take effect on 2027-01-01) |
| `accrual_eligible_net` | Arithmetic | PASS | 0 | 0 |  | Accrual eligible_net = unit_price x qty - discount (0 if excluded) |
| `accrual_points` | Arithmetic | PASS | 0 | 0 |  | Accrual points = floor(eligible_net x rate / 100) |
| `accrual_rate_is_tier_in_effect` | Arithmetic | PASS | 0 | 0 |  | Accrual rate = earn rate of the tier in effect on the order date |
| `every_earning_line_accrued` | Arithmetic | PASS | 0 | 0 |  | Every line that earns > 0 points has an accrual |
| `refund_amount` | Arithmetic | PASS | 0 | 0 |  | Refund = returned share of the line's net amount (discount split in proportion) |
| `reversal_amount` | Arithmetic | EXPECTED_FAIL | 10 | 10 | 10 | Each return reverses exactly the points earned on the returned amount (cumulative rule, original rate) |
| `redemption_ledger_amount` | Arithmetic | PASS | 0 | 0 |  | A REDEMPTION entry is exactly -points_cost |
| `redemption_lots_cover_cost` | Arithmetic | EXPECTED_FAIL | 10 | 10 |  | Lots used by an approved redemption add up to its cost |
| `lot_remaining_not_negative` | Arithmetic | EXPECTED_FAIL | 10 | 10 |  | No lot has given away more points than it earned |
| `redemption_status_vs_ledger` | Lifecycle | PASS | 0 | 0 |  | A REJECTED redemption has no ledger entry or lots; an APPROVED one has exactly one ledger entry |
| `approved_within_available` | Lifecycle | EXPECTED_FAIL | 10 | 10 | 10 | An approved redemption costs no more than the available balance when processed |
| `tier_changes_upgrade_in_chain` | Lifecycle | PASS | 0 | 0 |  | Tier changes only go up and each starts from the previous change's new tier |
| `account_tier_is_latest_change` | Lifecycle | PASS | 0 | 0 |  | An account's tier is its latest tier change (or the base tier) |
| `returned_qty_within_line` | Lifecycle | PASS | 0 | 0 |  | Units returned never exceed units bought |
| `one_accrual_per_line` | Duplicates | PASS | 0 | 0 |  | At most one accrual per order line |
| `one_reversal_per_return` | Duplicates | PASS | 0 | 0 |  | At most one reversal per return |
| `unique_customer_email` | Duplicates | PASS | 0 | 0 |  | Customer emails are unique (case-insensitive) |
| `one_batch_per_run_date` | Duplicates | PASS | 0 | 0 |  | One expiry batch per run date |
| `A1_balance_equals_ledger` | Acceptance | EXPECTED_FAIL | 10 | 10 | 10 | Account balance equals the sum of its ledger entries |
| `A2_reversal_references_accrual` | Acceptance | PASS | 0 | 0 |  | Every reversal references an existing original ACCRUAL of the same account and the returned line |
| `A3_no_lot_expired_twice` | Acceptance | EXPECTED_FAIL | 10 | 10 | 10 | Expiry is idempotent: no lot is expired more than once |
| `A3_rerun_adds_nothing` | Acceptance | PASS | 0 | 0 |  | Expiry is idempotent: re-running the latest batch would expire nothing new |
| `A4_tier_change_earned` | Acceptance | EXPECTED_FAIL | 10 | 10 | 10 | Every tier change was reached by qualifying spend (eligible net minus returns) before it took effect |
| `A4_account_tier_follows_thresholds` | Acceptance | EXPECTED_FAIL | 10 | 10 |  | Each account's tier is the highest tier its peak qualifying spend reached |
