"""SQL helpers shared by the endpoints. Business arithmetic stays in core/rules.py."""
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal

from backend.app.errors import ApiError, not_found
from core import ids
from core.config import AS_OF_DATE, TIERS, Tier
from core.rules import eligible_net, remaining_eligible_net

TIER_BY_ID = {ids.tier_id(t.code): t for t in TIERS}
LAST_WRITE_TS = datetime.combine(AS_OF_DATE, time.max)

# Sequence name -> formatter. Names are fixed here, never taken from a request.
ID_SEQUENCES = {"ord_seq": ids.order_id, "pts_seq": ids.pts_id, "ret_seq": ids.ret_id,
                "rdm_seq": ids.rdm_id, "exp_seq": ids.exp_id, "tch_seq": ids.tch_id}

LEDGER_COLUMNS = ("pts_id", "loy_id", "txn_type", "points", "txn_ts", "line_id", "eligible_net", "earn_rate",
                  "available_from", "expires_at", "return_id", "reverses_pts_id", "rdm_id", "exp_id",
                  "expires_pts_id")


def next_id(conn, sequence: str) -> str:
    """nextval() is atomic, so two concurrent requests can never receive the same id."""
    number = conn.execute(f"SELECT nextval('{sequence}') AS n").fetchone()["n"]
    return ID_SEQUENCES[sequence](number)


def lock_account(conn, loy_id: str) -> dict:
    """Lock the account row until the transaction ends; other writers for it wait here."""
    row = conn.execute("SELECT * FROM loyalty_account WHERE loy_id = %s FOR UPDATE", (loy_id,)).fetchone()
    if row is None:
        raise not_found("account", loy_id)
    return row


def check_write_time(conn, loy_id: str, ts: datetime):
    """A write may not rewrite history: it must be on/after the latest expiry batch date and
    the account's latest event, and no later than 31 Dec 2026."""
    row = conn.execute("""
        SELECT (SELECT max(run_date) FROM expiry_batch) AS last_batch,
               GREATEST(
                 (SELECT max(txn_ts) FROM points_transaction WHERE loy_id = %(a)s),
                 (SELECT max(order_ts) FROM orders WHERE loy_id = %(a)s),
                 (SELECT max(requested_at) FROM redemption WHERE loy_id = %(a)s),
                 (SELECT max(r.return_ts) FROM line_return r JOIN order_line l USING (line_id)
                    JOIN orders o USING (ord_id) WHERE o.loy_id = %(a)s)) AS last_event
        """, {"a": loy_id}).fetchone()
    earliest = datetime.combine(row["last_batch"], time.min) if row["last_batch"] else datetime.min
    if row["last_event"] and row["last_event"] > earliest:
        earliest = row["last_event"]
    if not earliest <= ts <= LAST_WRITE_TS:
        raise ApiError(409, "DATE_OUT_OF_RANGE",
                       f"timestamp {ts} must be between {earliest} (latest expiry batch or the account's "
                       f"latest event) and {LAST_WRITE_TS:%Y-%m-%d %H:%M:%S}")


def tier_in_effect(conn, loy_id: str, day) -> Tier:
    """Latest tier change effective on or before `day`; Bronze if none."""
    row = conn.execute("""
        SELECT new_tier_id FROM tier_change WHERE loy_id = %s AND effective_date <= %s
        ORDER BY effective_date DESC, tier_change_id DESC LIMIT 1""", (loy_id, day)).fetchone()
    return TIER_BY_ID[row["new_tier_id"] if row else ids.tier_id("BRONZE")]


def qualifying_spend(conn, loy_id: str) -> Decimal:
    """Eligible net spend minus returned eligible net, computed with core rules."""
    returned = defaultdict(int)
    for r in conn.execute("""
            SELECT r.line_id, r.qty_returned FROM line_return r JOIN order_line l USING (line_id)
            JOIN orders o USING (ord_id) WHERE o.loy_id = %s""", (loy_id,)):
        returned[r["line_id"]] += r["qty_returned"]
    total = Decimal("0.00")
    for line in conn.execute("""
            SELECT l.line_id, l.unit_price, l.qty, l.discount, l.category FROM order_line l
            JOIN orders o USING (ord_id) WHERE o.loy_id = %s""", (loy_id,)):
        eligible = eligible_net(line["unit_price"], line["qty"], line["discount"], line["category"])
        total += remaining_eligible_net(eligible, line["qty"], returned[line["line_id"]])
    return total


def insert_ledger(conn, **values) -> str:
    row = {c: values.get(c) for c in LEDGER_COLUMNS}
    conn.execute(f"INSERT INTO points_transaction ({', '.join(LEDGER_COLUMNS)}) "
                 f"VALUES ({', '.join('%s' for _ in LEDGER_COLUMNS)})", tuple(row.values()))
    return row["pts_id"]


def add_to_balance(conn, loy_id: str, delta: int):
    """Keep the stored snapshot in step with the ledger (incremental, so an injected
    BALANCE_MISMATCH is never silently repaired)."""
    conn.execute("UPDATE loyalty_account SET current_balance = current_balance + %s WHERE loy_id = %s",
                 (delta, loy_id))
