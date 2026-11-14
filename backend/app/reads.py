"""Read endpoints."""
from datetime import date

from fastapi import APIRouter, Query

from backend.app.db import transaction
from backend.app.detector import detect, score
from backend.app.evaluate import snapshot
from backend.app.errors import not_found
from backend.app.models import (
    AccountDetail, AccountSummary, AnomalyReport, Health, LedgerEntry, LotOut, RewardOut, RuleOut, Stats, TierOut, ValidationSummary,
)
from backend.app.queries import qualifying_spend
from backend.app.validation import run_validation
from core.config import AS_OF_DATE

router = APIRouter()

ACCOUNT_SELECT = """
    SELECT a.loy_id, a.customer_id, c.first_name || ' ' || c.last_name AS name, c.email, c.city,
           a.tier_id, a.current_balance, a.opened_at
    FROM loyalty_account a JOIN customer c USING (customer_id)"""


@router.get("/health", response_model=Health, tags=["reference"])
def health():
    with transaction() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "database": "ok"}


@router.get("/stats", response_model=Stats, tags=["reference"])
def get_stats():
    """Dashboard totals and the current tier mix."""
    with transaction() as conn:
        totals = conn.execute("""
            SELECT (SELECT count(*) FROM customer) AS customers,
                   (SELECT count(*) FROM loyalty_account) AS accounts,
                   (SELECT count(*) FROM orders) AS orders,
                   (SELECT count(*) FROM order_line) AS order_lines,
                   (SELECT count(*) FROM points_transaction) AS ledger_rows,
                   (SELECT COALESCE(SUM(points), 0) FROM points_transaction) AS points_outstanding,
                   (SELECT count(*) FROM redemption WHERE status = 'APPROVED') AS redemptions_approved,
                   (SELECT count(*) FROM redemption WHERE status = 'REJECTED') AS redemptions_rejected""").fetchone()
        tiers = conn.execute("""
            SELECT t.tier_id, t.code, count(a.loy_id) AS accounts
            FROM tier t LEFT JOIN loyalty_account a USING (tier_id)
            GROUP BY t.tier_id, t.code, t.rank ORDER BY t.rank""").fetchall()
    total = totals["accounts"] or 1
    return {**totals, "tier_mix": [{**t, "share": round(t["accounts"] / total, 4)} for t in tiers]}


@router.get("/accounts", response_model=list[AccountSummary], tags=["accounts"])
def list_accounts(search: str | None = None, tier: str | None = None,
                  limit: int = Query(50, ge=1, le=500)):
    where, params = [], []
    if search:
        where.append("(a.loy_id ILIKE %s OR c.first_name || ' ' || c.last_name ILIKE %s OR c.email ILIKE %s)")
        params += [f"%{search}%"] * 3
    if tier:
        where.append("a.tier_id = %s")
        params.append(tier if tier.startswith("TIER-") else f"TIER-{tier.upper()}")
    sql = ACCOUNT_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.loy_id LIMIT %s"
    with transaction() as conn:
        return conn.execute(sql, (*params, limit)).fetchall()


@router.get("/accounts/{loy_id}", response_model=AccountDetail, tags=["accounts"])
def get_account(loy_id: str, as_of: date = AS_OF_DATE):
    with transaction() as conn:
        account = conn.execute(ACCOUNT_SELECT + " WHERE a.loy_id = %s", (loy_id,)).fetchone()
        if account is None:
            raise not_found("account", loy_id)
        ledger = conn.execute("SELECT COALESCE(SUM(points), 0) AS s FROM points_transaction WHERE loy_id = %s",
                              (loy_id,)).fetchone()["s"]
        by_status = {r["status"]: r["remaining"] or 0 for r in conn.execute(
            """SELECT status, SUM(remaining) FILTER (WHERE remaining > 0) AS remaining
               FROM lot_status(%s, %s) GROUP BY status""", (as_of, loy_id))}
        spend = qualifying_spend(conn, loy_id)
    return {**account, "as_of": as_of, "ledger_balance": ledger,
            "balance_matches": ledger == account["current_balance"],
            "available": by_status.get("AVAILABLE", 0) + by_status.get("PARTIAL", 0),
            "pending": by_status.get("PENDING", 0),
            "expired_awaiting_batch": by_status.get("EXPIRED", 0),
            "qualifying_spend": spend}


def _require_account(conn, loy_id: str):
    if conn.execute("SELECT 1 FROM loyalty_account WHERE loy_id = %s", (loy_id,)).fetchone() is None:
        raise not_found("account", loy_id)


@router.get("/accounts/{loy_id}/ledger", response_model=list[LedgerEntry], tags=["accounts"])
def get_ledger(loy_id: str):
    with transaction() as conn:
        _require_account(conn, loy_id)
        return conn.execute("SELECT * FROM points_transaction WHERE loy_id = %s ORDER BY txn_ts, pts_id",
                            (loy_id,)).fetchall()


@router.get("/accounts/{loy_id}/lots", response_model=list[LotOut], tags=["accounts"])
def get_lots(loy_id: str, as_of: date = AS_OF_DATE):
    with transaction() as conn:
        _require_account(conn, loy_id)
        return conn.execute("SELECT * FROM lot_status(%s, %s) ORDER BY expires_at, lot_pts_id",
                            (as_of, loy_id)).fetchall()


@router.get("/tiers", response_model=list[TierOut], tags=["reference"])
def get_tiers():
    with transaction() as conn:
        return conn.execute("SELECT * FROM tier ORDER BY rank").fetchall()


@router.get("/rules", response_model=list[RuleOut], tags=["reference"])
def get_rules():
    with transaction() as conn:
        return conn.execute("SELECT * FROM rule ORDER BY rule_id").fetchall()


@router.get("/rewards", response_model=list[RewardOut], tags=["reference"])
def get_rewards():
    with transaction() as conn:
        return conn.execute("SELECT * FROM reward ORDER BY points_cost, reward_id").fetchall()


@router.get("/validation", response_model=ValidationSummary, tags=["evaluation"])
def get_validation():
    """All validation checks, run as SQL against the current database."""
    with transaction() as conn:
        snapshot(conn)
        return run_validation(conn)


@router.get("/anomalies", response_model=AnomalyReport, tags=["evaluation"])
def get_anomalies():
    """Ground truth, detector results and precision/recall/F1 per issue type."""
    with transaction() as conn:
        snapshot(conn)
        return score(conn, detect(conn))
