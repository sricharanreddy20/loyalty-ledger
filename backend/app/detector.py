"""Rule-based anomaly detector.

detect() reads INPUT tables only: never eval.ground_truth, never id gaps or id numbering.
score() is the only function that reads the ground truth, to compute precision/recall/F1.
Each detector returns (record_id, loy_id, reason), where record_id is the same kind of id the
ground truth uses for that issue type.
"""
from backend.app.sql_rules import AVAILABLE_AT_REDEMPTION, LINES, RETURNS, SPEND, kept

ISSUE_TYPES = ("BALANCE_MISMATCH", "REDEMPTION_EXCEEDS_BALANCE", "RETURN_KEEPS_POINTS",
               "DOUBLE_EXPIRY", "TIER_USES_GROSS_SPEND")

DETECTORS = {
    # Stored snapshot differs from the sum of the account's ledger.
    "BALANCE_MISMATCH": """
        SELECT a.loy_id AS record_id, a.loy_id,
               format('current_balance %s but ledger sum is %s', a.current_balance, COALESCE(l.s, 0)) AS reason
        FROM loyalty_account a
        LEFT JOIN (SELECT loy_id, SUM(points) AS s FROM points_transaction GROUP BY loy_id) l USING (loy_id)
        WHERE a.current_balance <> COALESCE(l.s, 0)""",

    # An approved redemption that cost more than the points available when it was processed.
    "REDEMPTION_EXCEEDS_BALANCE": f"""
        SELECT d.rdm_id AS record_id, d.loy_id,
               format('approved %s points with only %s available', d.points_cost, av.available) AS reason
        FROM redemption d CROSS JOIN LATERAL ({AVAILABLE_AT_REDEMPTION}) av
        WHERE d.status = 'APPROVED' AND d.points_cost > av.available""",

    # A return whose expected reversal (cumulative rule, original rate) exceeds what was reversed.
    "RETURN_KEEPS_POINTS": f"""
        WITH {LINES}, {RETURNS},
        expected AS (
            SELECT r.return_id, l.loy_id,
                   (CASE WHEN r.before_qty = 0 THEN a.points
                         ELSE floor({kept('l.eligible', 'l.qty', 'r.before_qty')} * a.earn_rate / 100) END
                    - floor({kept('l.eligible', 'l.qty', 'r.after_qty')} * a.earn_rate / 100))::int AS expected,
                   COALESCE((SELECT -SUM(v.points) FROM points_transaction v
                             WHERE v.txn_type = 'REVERSAL' AND v.return_id = r.return_id), 0) AS actual
            FROM rets r JOIN lines l USING (line_id)
            JOIN points_transaction a ON a.txn_type = 'ACCRUAL' AND a.line_id = r.line_id)
        SELECT return_id AS record_id, loy_id,
               format('should reverse %s points, reversed %s', expected, actual) AS reason
        FROM expected WHERE expected > actual""",

    # Every expiry of a lot after its first one.
    "DOUBLE_EXPIRY": """
        SELECT pts_id AS record_id, loy_id,
               format('lot %s expired again in %s (first expired in %s)', expires_pts_id, exp_id, first_exp) AS reason
        FROM (SELECT e.*, row_number() OVER w AS n, first_value(e.exp_id) OVER w AS first_exp
              FROM points_transaction e WHERE e.txn_type = 'EXPIRY'
              WINDOW w AS (PARTITION BY e.expires_pts_id ORDER BY e.txn_ts, e.exp_id)) x
        WHERE n > 1""",

    # A tier change never reached by qualifying spend before it took effect, but reached by gross spend.
    "TIER_USES_GROSS_SPEND": f"""
        WITH {LINES}, {RETURNS}, {SPEND},
        peak AS (SELECT t.tier_change_id, MAX(s.qualifying) AS peak FROM tier_change t
                 JOIN spend s ON s.loy_id = t.loy_id AND s.day < t.effective_date GROUP BY t.tier_change_id),
        gross AS (SELECT t.tier_change_id, SUM(l.unit_price * l.qty) AS gross FROM tier_change t
                  JOIN lines l ON l.loy_id = t.loy_id AND l.order_ts::date < t.effective_date
                  GROUP BY t.tier_change_id)
        SELECT t.tier_change_id AS record_id, t.loy_id,
               format('%s needs %s; qualifying spend peaked at %s, gross spend was %s',
                      t.new_tier_id, nt.min_qualifying_spend, COALESCE(q.peak, 0), COALESCE(g.gross, 0)) AS reason
        FROM tier_change t
        JOIN tier nt ON nt.tier_id = t.new_tier_id
        LEFT JOIN peak q USING (tier_change_id)
        LEFT JOIN gross g USING (tier_change_id)
        WHERE COALESCE(q.peak, 0) < nt.min_qualifying_spend AND COALESCE(g.gross, 0) >= nt.min_qualifying_spend""",
}

# Used only by score() and validation, never by detect(): each labelled record with its account.
GROUND_TRUTH = """
SELECT g.record_id, g.entity, g.issue_type, g.description, g.rule_violated,
       COALESCE(a.loy_id, d.loy_id, p.loy_id, t.loy_id, o.loy_id) AS loy_id
FROM eval.ground_truth g
LEFT JOIN loyalty_account a ON g.entity = 'loyalty_account' AND a.loy_id = g.record_id
LEFT JOIN redemption d ON g.entity = 'redemption' AND d.rdm_id = g.record_id
LEFT JOIN points_transaction p ON g.entity = 'points_transaction' AND p.pts_id = g.record_id
LEFT JOIN tier_change t ON g.entity = 'tier_change' AND t.tier_change_id = g.record_id
LEFT JOIN line_return r ON g.entity = 'line_return' AND r.return_id = g.record_id
LEFT JOIN order_line l ON l.line_id = r.line_id
LEFT JOIN orders o ON o.ord_id = l.ord_id
ORDER BY g.issue_type, g.record_id"""


def detect(conn) -> list[dict]:
    """Run every detector. Returns rows {issue_type, record_id, loy_id, reason}, sorted."""
    flagged = []
    for issue_type, sql in DETECTORS.items():
        for row in conn.execute(sql).fetchall():
            flagged.append({"issue_type": issue_type, "record_id": row["record_id"],
                            "loy_id": row["loy_id"], "reason": row["reason"]})
    return sorted(flagged, key=lambda r: (ISSUE_TYPES.index(r["issue_type"]), r["record_id"]))


def _ratio(numerator: int, denominator: int):
    return round(numerator / denominator, 4) if denominator else None


def _metrics(tp: int, fp: int, fn: int) -> dict:
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def score(conn, flagged: list[dict]) -> dict:
    """Compare detections with eval.ground_truth: per-type and overall (micro) metrics."""
    truth = conn.execute(GROUND_TRUTH).fetchall()
    per_type, false_pos, false_neg = [], [], []
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for issue_type in ISSUE_TYPES:
        found = {f["record_id"]: f for f in flagged if f["issue_type"] == issue_type}
        labelled = {g["record_id"]: g for g in truth if g["issue_type"] == issue_type}
        tp = len(found.keys() & labelled.keys())
        fp_ids, fn_ids = sorted(found.keys() - labelled.keys()), sorted(labelled.keys() - found.keys())
        false_pos += [found[i] for i in fp_ids]
        false_neg += [labelled[i] for i in fn_ids]
        per_type.append({"issue_type": issue_type, "ground_truth": len(labelled), "flagged": len(found),
                         **_metrics(tp, len(fp_ids), len(fn_ids))})
        totals["tp"] += tp
        totals["fp"] += len(fp_ids)
        totals["fn"] += len(fn_ids)
    return {
        "detector": "rule-based SQL over input tables (no ground truth, no id gaps)",
        "metrics": per_type,
        "overall": {"ground_truth": len(truth), "flagged": len(flagged), **_metrics(**totals)},
        "false_positives": false_pos,
        "false_negatives": false_neg,
        "flagged": flagged,
        "ground_truth": truth,
    }
