"""Reusable SQL fragments that recompute business values from INPUT tables only.

These deliberately re-implement the rule arithmetic in SQL (independently of core/rules.py),
so validation and detection are a second, independent implementation. Configured values
(excluded categories, return window, lot expiry) are read from the `rule` table.
Postgres round(numeric, 2) rounds half away from zero = ROUND_HALF_UP for these positive amounts.
"""

# Every order line with its account and eligible net (0 for excluded categories).
LINES = """
lines AS (
    SELECT l.line_id, l.ord_id, o.loy_id, o.order_ts, l.qty, l.unit_price, l.discount, l.category,
           (l.unit_price * l.qty - l.discount) AS net,
           CASE WHEN l.category IN (SELECT category FROM rule WHERE rule_type = 'CATEGORY_EXCLUSION')
                THEN 0 ELSE l.unit_price * l.qty - l.discount END AS eligible
    FROM order_line l JOIN orders o USING (ord_id)
)"""

# Every return with the units returned on that line before and after it (in return order).
RETURNS = """
rets AS (
    SELECT r.*,
           COALESCE(SUM(r.qty_returned) OVER (PARTITION BY r.line_id ORDER BY r.return_ts, r.return_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS before_qty,
           SUM(r.qty_returned) OVER (PARTITION BY r.line_id ORDER BY r.return_ts, r.return_id) AS after_qty
    FROM line_return r
)"""

# Eligible net still kept on a line after `k` units were returned (discount split in proportion).
def kept(eligible: str, qty: str, k: str) -> str:
    return f"round({eligible} * ({qty} - {k}) / {qty}, 2)"


# Qualifying spend (eligible net minus returned eligible net) as a running total after every order
# line and every return, in event order (order before return at the same timestamp). Tiers are
# checked right after each order, so a peak inside a day counts even if a later return lowers it.
SPEND = f"""
spend_events AS (
    SELECT loy_id, order_ts AS ts, 0 AS kind, line_id AS ref, eligible AS delta FROM lines
    UNION ALL
    SELECT l.loy_id, r.return_ts, 1, r.return_id,
           -({kept('l.eligible', 'l.qty', 'r.before_qty')} - {kept('l.eligible', 'l.qty', 'r.after_qty')})
    FROM rets r JOIN lines l USING (line_id)
),
spend AS (
    SELECT loy_id, ts::date AS day,
           SUM(delta) OVER (PARTITION BY loy_id ORDER BY ts, kind, ref ROWS UNBOUNDED PRECEDING) AS qualifying
    FROM spend_events
)"""

# Tier in effect on a date: the latest tier change effective on or before it, else the lowest tier.
def tier_in_effect(loy_id: str, day: str) -> str:
    return f"""COALESCE(
        (SELECT tc.new_tier_id FROM tier_change tc WHERE tc.loy_id = {loy_id} AND tc.effective_date <= {day}
         ORDER BY tc.effective_date DESC, tc.tier_change_id DESC LIMIT 1),
        (SELECT tier_id FROM tier ORDER BY rank LIMIT 1))"""


# Points available to an approved redemption at the moment it was processed.
# Processing order is (requested_at, rdm_id), the documented order for same-timestamp requests.
AVAILABLE_AT_REDEMPTION = """
SELECT COALESCE(SUM(
           a.points
           - COALESCE((SELECT -SUM(v.points) FROM points_transaction v
                       WHERE v.txn_type = 'REVERSAL' AND v.reverses_pts_id = a.pts_id), 0)
           - COALESCE((SELECT SUM(rl.points_used) FROM redemption_lot rl JOIN redemption p ON p.rdm_id = rl.rdm_id
                       WHERE rl.lot_pts_id = a.pts_id AND p.status = 'APPROVED'
                         AND (p.requested_at, p.rdm_id) < (d.requested_at, d.rdm_id)), 0)
           - COALESCE((SELECT -SUM(e.points) FROM points_transaction e
                       WHERE e.txn_type = 'EXPIRY' AND e.expires_pts_id = a.pts_id
                         AND e.txn_ts < d.requested_at), 0)), 0) AS available
FROM points_transaction a
WHERE a.txn_type = 'ACCRUAL' AND a.loy_id = d.loy_id
  AND a.available_from <= d.requested_at::date AND d.requested_at::date < a.expires_at
"""
