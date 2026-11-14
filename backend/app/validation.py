"""Validation: named SQL checks run against the database.

Each check returns its violating rows as (record_id, loy_id). Status:
  PASS           no violations
  EXPECTED_FAIL  every violation is on an account with a labelled anomaly of a type in `explained_by`
  FAIL           at least one violation is not explained by a labelled anomaly
Checks never use the ground truth to decide what is a violation; the ground truth is only
used afterwards, to say whether the violations were expected.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass

from backend.app.detector import DETECTORS, GROUND_TRUTH
from backend.app.sql_rules import LINES, RETURNS, SPEND, kept, tier_in_effect
from core import ids
from core.config import CATEGORIES

RETURN_WINDOW = "(SELECT value::int FROM rule WHERE rule_type = 'RETURN_WINDOW')"
EXPIRY_DAYS = "(SELECT value::int FROM rule WHERE rule_type = 'LOT_EXPIRY')"
SAMPLE_SIZE = 5


@dataclass(frozen=True)
class Check:
    id: str
    category: str
    description: str
    sql: str
    explained_by: tuple = ()
    primary: str | None = None     # the anomaly type this check is the acceptance test for


def _union(parts: list[str]) -> str:
    return "\nUNION ALL\n".join(parts)


# ---------------------------------------------------------------- structural checks

PRIMARY_KEYS = {
    "tier": "tier_id", "rule": "rule_id", "reward": "reward_id", "customer": "customer_id",
    "loyalty_account": "loy_id", "tier_change": "tier_change_id", "expiry_batch": "exp_id", "orders": "ord_id",
    "order_line": "line_id", "line_return": "return_id", "redemption": "rdm_id", "points_transaction": "pts_id",
    "redemption_lot": "rdm_id, lot_pts_id",
}
FOREIGN_KEYS = [
    ("rule", "tier_id", "tier", "tier_id"), ("loyalty_account", "customer_id", "customer", "customer_id"),
    ("loyalty_account", "tier_id", "tier", "tier_id"), ("tier_change", "loy_id", "loyalty_account", "loy_id"),
    ("tier_change", "old_tier_id", "tier", "tier_id"), ("tier_change", "new_tier_id", "tier", "tier_id"),
    ("orders", "loy_id", "loyalty_account", "loy_id"), ("order_line", "ord_id", "orders", "ord_id"),
    ("line_return", "line_id", "order_line", "line_id"), ("redemption", "loy_id", "loyalty_account", "loy_id"),
    ("redemption", "reward_id", "reward", "reward_id"), ("points_transaction", "loy_id", "loyalty_account", "loy_id"),
    ("points_transaction", "line_id", "order_line", "line_id"), ("points_transaction", "return_id", "line_return", "return_id"),
    ("points_transaction", "reverses_pts_id", "points_transaction", "pts_id"),
    ("points_transaction", "rdm_id", "redemption", "rdm_id"), ("points_transaction", "exp_id", "expiry_batch", "exp_id"),
    ("points_transaction", "expires_pts_id", "points_transaction", "pts_id"),
    ("redemption_lot", "rdm_id", "redemption", "rdm_id"), ("redemption_lot", "lot_pts_id", "points_transaction", "pts_id"),
]
ENUMS = [
    ("points_transaction", "txn_type", ("ACCRUAL", "REVERSAL", "REDEMPTION", "EXPIRY")),
    ("redemption", "status", ("APPROVED", "REJECTED")),
    ("redemption", "reject_reason", ("INSUFFICIENT_POINTS",)),
    ("orders", "channel", ("STORE", "ONLINE")),
    ("order_line", "category", CATEGORIES),
    ("rule", "rule_type", ("EARN_RATE", "CATEGORY_EXCLUSION", "LOT_EXPIRY", "RETURN_WINDOW")),
]
ID_FORMATS = [
    ("customer", "customer_id", "customer"), ("loyalty_account", "loy_id", "loyalty"), ("orders", "ord_id", "order"),
    ("order_line", "line_id", "line"), ("points_transaction", "pts_id", "pts"), ("redemption", "rdm_id", "rdm"),
    ("expiry_batch", "exp_id", "exp"), ("line_return", "return_id", "ret"), ("tier_change", "tier_change_id", "tch"),
    ("tier", "tier_id", "tier"), ("rule", "rule_id", "rule"), ("reward", "reward_id", "reward"),
]


def _in_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


STRUCTURAL = [
    Check("pk_unique", "Primary keys", "Primary key is unique in all 13 tables", _union([
        f"SELECT '{t}:' || concat_ws('/', {pk}) AS record_id, NULL::text AS loy_id FROM {t} "
        f"GROUP BY {pk} HAVING count(*) > 1"
        for t, pk in PRIMARY_KEYS.items()])),
    Check("fk_integrity", "Foreign keys", "Every reference points to an existing parent row (20 relationships)", _union([
        f"""SELECT '{c}.{col}=' || x.{col} AS record_id, NULL::text AS loy_id FROM {c} x
            WHERE x.{col} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {p} y WHERE y.{pcol} = x.{col})"""
        for c, col, p, pcol in FOREIGN_KEYS])),
    Check("ledger_refs_by_type", "Foreign keys",
          "Ledger references match the entry type (reversal/expiry -> ACCRUAL of same account; lots -> same account)",
          """SELECT e.pts_id AS record_id, e.loy_id FROM points_transaction e
             JOIN points_transaction a ON a.pts_id = COALESCE(e.reverses_pts_id, e.expires_pts_id)
             WHERE a.txn_type <> 'ACCRUAL' OR a.loy_id <> e.loy_id
             UNION ALL
             SELECT rl.rdm_id || '/' || rl.lot_pts_id, d.loy_id FROM redemption_lot rl
             JOIN redemption d USING (rdm_id) JOIN points_transaction a ON a.pts_id = rl.lot_pts_id
             WHERE a.txn_type <> 'ACCRUAL' OR a.loy_id <> d.loy_id"""),
    Check("enums_allowed", "Allowed values", "Enumerated columns hold only allowed values", _union([
        f"""SELECT '{t}.{col}=' || {col} AS record_id, NULL::text AS loy_id FROM {t}
            WHERE {col} IS NOT NULL AND {col} NOT IN ({_in_list(values)})""" for t, col, values in ENUMS])),
    Check("id_formats", "Allowed values", "Every id matches its zero-padded format", _union([
        f"SELECT {col} AS record_id, NULL::text AS loy_id FROM {t} WHERE {col} !~ '^{ids.PATTERNS[kind].pattern}$'"
        for t, col, kind in ID_FORMATS])),
    Check("derived_ids", "Allowed values", "Derived ids are consistent (LOY-<customer>, <order>-<line_no>)",
          """SELECT loy_id AS record_id, loy_id FROM loyalty_account WHERE loy_id <> 'LOY-' || customer_id
             UNION ALL
             SELECT line_id, NULL FROM order_line WHERE line_id <> ord_id || '-' || lpad(line_no::text, 2, '0')"""),
]

# ---------------------------------------------------------------- date order

DATES = [
    Check("order_after_account_opened", "Date order", "Orders are on or after the account was opened",
          """SELECT o.ord_id AS record_id, o.loy_id FROM orders o JOIN loyalty_account a USING (loy_id)
             WHERE o.order_ts::date < a.opened_at"""),
    Check("accrual_on_order_time", "Date order", "An accrual is dated at its order's timestamp",
          """SELECT a.pts_id AS record_id, a.loy_id FROM points_transaction a
             JOIN order_line l ON l.line_id = a.line_id JOIN orders o ON o.ord_id = l.ord_id
             WHERE a.txn_type = 'ACCRUAL' AND a.txn_ts <> o.order_ts"""),
    Check("lot_dates", "Date order",
          "Lot available_from = earned + return window + 1 day; expires_at = earned + lot expiry days",
          f"""SELECT pts_id AS record_id, loy_id FROM points_transaction WHERE txn_type = 'ACCRUAL'
              AND (available_from <> txn_ts::date + {RETURN_WINDOW} + 1 OR expires_at <> txn_ts::date + {EXPIRY_DAYS})"""),
    Check("return_within_window", "Date order", "A return is after its order and within the return window",
          f"""SELECT r.return_id AS record_id, o.loy_id FROM line_return r JOIN order_line l USING (line_id)
              JOIN orders o USING (ord_id)
              WHERE r.return_ts <= o.order_ts OR r.return_ts::date - o.order_ts::date > {RETURN_WINDOW}"""),
    Check("reversal_while_pending", "Date order",
          "A reversal is dated at its return and happens before the lot becomes available",
          """SELECT v.pts_id AS record_id, v.loy_id FROM points_transaction v
             JOIN points_transaction a ON a.pts_id = v.reverses_pts_id
             JOIN line_return r ON r.return_id = v.return_id
             WHERE v.txn_type = 'REVERSAL' AND (v.txn_ts <> r.return_ts OR v.txn_ts::date >= a.available_from)"""),
    Check("expiry_on_time", "Date order",
          "An expiry is not before the lot's expires_at and is dated at its batch's run",
          """SELECT e.pts_id AS record_id, e.loy_id FROM points_transaction e
             JOIN points_transaction a ON a.pts_id = e.expires_pts_id JOIN expiry_batch b ON b.exp_id = e.exp_id
             WHERE e.txn_type = 'EXPIRY' AND (e.txn_ts::date < a.expires_at OR e.txn_ts <> b.executed_at)"""),
    Check("batch_on_first_of_month", "Date order", "Expiry batches run on the 1st of a month",
          """SELECT exp_id AS record_id, NULL::text AS loy_id FROM expiry_batch
             WHERE extract(day FROM run_date) <> 1 OR executed_at::date <> run_date"""),
    Check("redeemed_lot_was_available", "Date order",
          "Lots used by a redemption were available on the request date",
          """SELECT rl.rdm_id || '/' || rl.lot_pts_id AS record_id, d.loy_id FROM redemption_lot rl
             JOIN redemption d USING (rdm_id) JOIN points_transaction a ON a.pts_id = rl.lot_pts_id
             WHERE NOT (a.available_from <= d.requested_at::date AND d.requested_at::date < a.expires_at)"""),
    Check("dates_in_program_year", "Date order",
          "Business events are in 2026 (expiry batches and tier changes may take effect on 2027-01-01)",
          """SELECT ord_id AS record_id, loy_id FROM orders WHERE order_ts < '2026-01-01' OR order_ts >= '2027-01-01'
             UNION ALL SELECT rdm_id, loy_id FROM redemption WHERE requested_at < '2026-01-01' OR requested_at >= '2027-01-01'
             UNION ALL SELECT return_id, NULL FROM line_return WHERE return_ts < '2026-01-01' OR return_ts >= '2027-01-01'
             UNION ALL SELECT pts_id, loy_id FROM points_transaction
                       WHERE txn_ts < '2026-01-01' OR (txn_type <> 'EXPIRY' AND txn_ts >= '2027-01-01')
                          OR txn_ts > '2027-01-01 23:59:59'
             UNION ALL SELECT exp_id, NULL FROM expiry_batch WHERE run_date < '2026-01-01' OR run_date > '2027-01-01'
             UNION ALL SELECT tier_change_id, loy_id FROM tier_change
                       WHERE effective_date <= '2026-01-01' OR effective_date > '2027-01-01'"""),
]

# ---------------------------------------------------------------- arithmetic reconciliation

ARITHMETIC = [
    Check("accrual_eligible_net", "Arithmetic", "Accrual eligible_net = unit_price x qty - discount (0 if excluded)",
          f"""WITH {LINES}
              SELECT a.pts_id AS record_id, a.loy_id FROM points_transaction a JOIN lines l USING (line_id)
              WHERE a.txn_type = 'ACCRUAL' AND a.eligible_net <> l.eligible"""),
    Check("accrual_points", "Arithmetic", "Accrual points = floor(eligible_net x rate / 100)",
          """SELECT pts_id AS record_id, loy_id FROM points_transaction
             WHERE txn_type = 'ACCRUAL' AND points <> floor(eligible_net * earn_rate / 100)"""),
    Check("accrual_rate_is_tier_in_effect", "Arithmetic", "Accrual rate = earn rate of the tier in effect on the order date",
          f"""SELECT a.pts_id AS record_id, a.loy_id FROM points_transaction a
              JOIN order_line l ON l.line_id = a.line_id JOIN orders o ON o.ord_id = l.ord_id
              JOIN tier t ON t.tier_id = {tier_in_effect('a.loy_id', 'o.order_ts::date')}
              WHERE a.txn_type = 'ACCRUAL' AND a.earn_rate <> t.earn_rate"""),
    Check("every_earning_line_accrued", "Arithmetic", "Every line that earns > 0 points has an accrual",
          f"""WITH {LINES}
              SELECT l.line_id AS record_id, l.loy_id FROM lines l
              JOIN tier t ON t.tier_id = {tier_in_effect('l.loy_id', 'l.order_ts::date')}
              WHERE floor(l.eligible * t.earn_rate / 100) > 0
                AND NOT EXISTS (SELECT 1 FROM points_transaction a WHERE a.txn_type = 'ACCRUAL' AND a.line_id = l.line_id)"""),
    Check("refund_amount", "Arithmetic", "Refund = returned share of the line's net amount (discount split in proportion)",
          f"""WITH {LINES}, {RETURNS}
              SELECT r.return_id AS record_id, l.loy_id FROM rets r JOIN lines l USING (line_id)
              WHERE r.refund_amount <> {kept('l.net', 'l.qty', 'r.before_qty')} - {kept('l.net', 'l.qty', 'r.after_qty')}"""),
    Check("reversal_amount", "Arithmetic",
          "Each return reverses exactly the points earned on the returned amount (cumulative rule, original rate)",
          f"""WITH {LINES}, {RETURNS}
              SELECT r.return_id AS record_id, l.loy_id FROM rets r JOIN lines l USING (line_id)
              JOIN points_transaction a ON a.txn_type = 'ACCRUAL' AND a.line_id = r.line_id
              WHERE (CASE WHEN r.before_qty = 0 THEN a.points
                          ELSE floor({kept('l.eligible', 'l.qty', 'r.before_qty')} * a.earn_rate / 100) END
                     - floor({kept('l.eligible', 'l.qty', 'r.after_qty')} * a.earn_rate / 100))
                    <> COALESCE((SELECT -SUM(v.points) FROM points_transaction v
                                 WHERE v.txn_type = 'REVERSAL' AND v.return_id = r.return_id), 0)""",
          explained_by=("RETURN_KEEPS_POINTS",), primary="RETURN_KEEPS_POINTS"),
    Check("redemption_ledger_amount", "Arithmetic", "A REDEMPTION entry is exactly -points_cost",
          """SELECT d.rdm_id AS record_id, d.loy_id FROM redemption d JOIN points_transaction p ON p.rdm_id = d.rdm_id
             WHERE p.points <> -d.points_cost"""),
    Check("redemption_lots_cover_cost", "Arithmetic", "Lots used by an approved redemption add up to its cost",
          """SELECT d.rdm_id AS record_id, d.loy_id FROM redemption d
             LEFT JOIN (SELECT rdm_id, SUM(points_used) AS used FROM redemption_lot GROUP BY rdm_id) u USING (rdm_id)
             WHERE d.status = 'APPROVED' AND COALESCE(u.used, 0) <> d.points_cost""",
          explained_by=("REDEMPTION_EXCEEDS_BALANCE",)),
    Check("lot_remaining_not_negative", "Arithmetic", "No lot has given away more points than it earned",
          """SELECT lot_pts_id AS record_id, loy_id FROM lot_status(DATE '2099-12-31') WHERE remaining < 0""",
          explained_by=("DOUBLE_EXPIRY",)),
]

# ---------------------------------------------------------------- lifecycle transitions

LIFECYCLE = [
    Check("redemption_status_vs_ledger", "Lifecycle",
          "A REJECTED redemption has no ledger entry or lots; an APPROVED one has exactly one ledger entry",
          """SELECT d.rdm_id AS record_id, d.loy_id FROM redemption d
             LEFT JOIN (SELECT rdm_id, count(*) AS n FROM points_transaction WHERE rdm_id IS NOT NULL GROUP BY rdm_id) p
                    USING (rdm_id)
             WHERE (d.status = 'APPROVED' AND COALESCE(p.n, 0) <> 1)
                OR (d.status = 'REJECTED' AND (COALESCE(p.n, 0) > 0
                    OR EXISTS (SELECT 1 FROM redemption_lot rl WHERE rl.rdm_id = d.rdm_id)))"""),
    Check("approved_within_available", "Lifecycle",
          "An approved redemption costs no more than the available balance when processed",
          DETECTORS["REDEMPTION_EXCEEDS_BALANCE"],
          explained_by=("REDEMPTION_EXCEEDS_BALANCE",), primary="REDEMPTION_EXCEEDS_BALANCE"),
    Check("tier_changes_upgrade_in_chain", "Lifecycle",
          "Tier changes only go up and each starts from the previous change's new tier",
          """SELECT t.tier_change_id AS record_id, t.loy_id FROM (
                 SELECT tc.*, lag(tc.new_tier_id) OVER (PARTITION BY tc.loy_id
                                ORDER BY tc.effective_date, tc.tier_change_id) AS previous FROM tier_change tc) t
             JOIN tier o ON o.tier_id = t.old_tier_id JOIN tier n ON n.tier_id = t.new_tier_id
             WHERE n.rank <= o.rank
                OR t.old_tier_id <> COALESCE(t.previous, (SELECT tier_id FROM tier ORDER BY rank LIMIT 1))"""),
    Check("account_tier_is_latest_change", "Lifecycle", "An account's tier is its latest tier change (or the base tier)",
          f"""SELECT a.loy_id AS record_id, a.loy_id FROM loyalty_account a
              WHERE a.tier_id <> {tier_in_effect('a.loy_id', "DATE '9999-12-31'")}"""),
    Check("returned_qty_within_line", "Lifecycle", "Units returned never exceed units bought",
          """SELECT r.line_id AS record_id, o.loy_id FROM line_return r JOIN order_line l USING (line_id)
             JOIN orders o USING (ord_id) GROUP BY r.line_id, o.loy_id, l.qty HAVING SUM(r.qty_returned) > l.qty"""),
]

# ---------------------------------------------------------------- duplicates

DUPLICATES = [
    Check("one_accrual_per_line", "Duplicates", "At most one accrual per order line",
          """SELECT line_id AS record_id, min(loy_id) AS loy_id FROM points_transaction
             WHERE txn_type = 'ACCRUAL' GROUP BY line_id HAVING count(*) > 1"""),
    Check("one_reversal_per_return", "Duplicates", "At most one reversal per return",
          """SELECT return_id AS record_id, min(loy_id) AS loy_id FROM points_transaction
             WHERE txn_type = 'REVERSAL' GROUP BY return_id HAVING count(*) > 1"""),
    Check("unique_customer_email", "Duplicates", "Customer emails are unique (case-insensitive)",
          """SELECT lower(email) AS record_id, NULL::text AS loy_id FROM customer GROUP BY lower(email) HAVING count(*) > 1"""),
    Check("one_batch_per_run_date", "Duplicates", "One expiry batch per run date",
          """SELECT run_date::text AS record_id, NULL::text AS loy_id FROM expiry_batch GROUP BY run_date HAVING count(*) > 1"""),
]

# ---------------------------------------------------------------- the four acceptance checks

ACCEPTANCE = [
    Check("A1_balance_equals_ledger", "Acceptance", "Account balance equals the sum of its ledger entries",
          DETECTORS["BALANCE_MISMATCH"], explained_by=("BALANCE_MISMATCH",), primary="BALANCE_MISMATCH"),
    Check("A2_reversal_references_accrual", "Acceptance",
          "Every reversal references an existing original ACCRUAL of the same account and the returned line",
          """SELECT v.pts_id AS record_id, v.loy_id FROM points_transaction v
             LEFT JOIN points_transaction a ON a.pts_id = v.reverses_pts_id
             LEFT JOIN line_return r ON r.return_id = v.return_id
             WHERE v.txn_type = 'REVERSAL'
               AND (a.pts_id IS NULL OR a.txn_type <> 'ACCRUAL' OR a.loy_id <> v.loy_id
                    OR r.return_id IS NULL OR a.line_id <> r.line_id)"""),
    Check("A3_no_lot_expired_twice", "Acceptance", "Expiry is idempotent: no lot is expired more than once",
          DETECTORS["DOUBLE_EXPIRY"], explained_by=("DOUBLE_EXPIRY",), primary="DOUBLE_EXPIRY"),
    Check("A3_rerun_adds_nothing", "Acceptance",
          "Expiry is idempotent: re-running the latest batch would expire nothing new",
          """SELECT lot_pts_id AS record_id, loy_id
             FROM lot_status((SELECT max(run_date) FROM expiry_batch))
             WHERE expires_at <= (SELECT max(run_date) FROM expiry_batch) AND remaining > 0"""),
    Check("A4_tier_change_earned", "Acceptance",
          "Every tier change was reached by qualifying spend (eligible net minus returns) before it took effect",
          f"""WITH {LINES}, {RETURNS}, {SPEND},
              peak AS (SELECT t.tier_change_id, MAX(s.qualifying) AS peak FROM tier_change t
                       JOIN spend s ON s.loy_id = t.loy_id AND s.day < t.effective_date GROUP BY t.tier_change_id)
              SELECT t.tier_change_id AS record_id, t.loy_id FROM tier_change t
              JOIN tier n ON n.tier_id = t.new_tier_id LEFT JOIN peak p USING (tier_change_id)
              WHERE COALESCE(p.peak, 0) < n.min_qualifying_spend""",
          explained_by=("TIER_USES_GROSS_SPEND",), primary="TIER_USES_GROSS_SPEND"),
    Check("A4_account_tier_follows_thresholds", "Acceptance",
          "Each account's tier is the highest tier its peak qualifying spend reached",
          f"""WITH {LINES}, {RETURNS}, {SPEND},
              peak AS (SELECT a.loy_id, COALESCE(MAX(s.qualifying), 0) AS peak FROM loyalty_account a
                       LEFT JOIN spend s USING (loy_id) GROUP BY a.loy_id)
              SELECT a.loy_id AS record_id, a.loy_id FROM loyalty_account a JOIN peak p USING (loy_id)
              WHERE a.tier_id <> (SELECT tier_id FROM tier WHERE min_qualifying_spend <= p.peak
                                  ORDER BY rank DESC LIMIT 1)""",
          explained_by=("TIER_USES_GROSS_SPEND",)),
]

CHECKS = STRUCTURAL + DATES + ARITHMETIC + LIFECYCLE + DUPLICATES + ACCEPTANCE


def run_validation(conn) -> dict:
    """Run every check. Pass a connection; the caller controls the transaction."""
    anomaly_accounts = defaultdict(set)
    truth_counts = Counter()
    for row in conn.execute(GROUND_TRUTH).fetchall():
        anomaly_accounts[row["issue_type"]].add(row["loy_id"])
        truth_counts[row["issue_type"]] += 1

    results = []
    for check in CHECKS:
        rows = conn.execute(check.sql).fetchall()
        violating = sorted({r["record_id"] for r in rows})
        expected_accounts = set().union(*(anomaly_accounts[t] for t in check.explained_by))
        explained = sorted({r["record_id"] for r in rows if r["loy_id"] in expected_accounts})
        unexplained = len(violating) - len(explained)
        if not violating:
            status = "PASS"
        elif unexplained == 0:
            status = "EXPECTED_FAIL"
        else:
            status = "FAIL"
        results.append({
            "id": check.id, "category": check.category, "description": check.description, "status": status,
            "violations": len(violating), "explained": len(explained), "unexplained": unexplained,
            "explained_by": list(check.explained_by),
            "ground_truth_count": truth_counts[check.primary] if check.primary else None,
            "sample": violating[:SAMPLE_SIZE],
        })
    status_counts = Counter(r["status"] for r in results)
    return {
        "totals": {"checks": len(results), "PASS": status_counts["PASS"],
                   "EXPECTED_FAIL": status_counts["EXPECTED_FAIL"], "FAIL": status_counts["FAIL"]},
        "checks": results,
    }
