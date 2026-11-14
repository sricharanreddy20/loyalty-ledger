"""Write endpoints. Each runs in ONE transaction and starts by locking the account row
(SELECT ... FOR UPDATE), so writes for the same account are processed one at a time.
Raising ApiError rolls everything back; every amount comes from core/rules.py."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.app.db import transaction
from backend.app.errors import ApiError, not_found
from backend.app.models import ExpiryResult, OrderIn, OrderResult, RedemptionIn, RedemptionResult, ReturnIn, ReturnResult
from backend.app.queries import (
    TIER_BY_ID, add_to_balance, check_write_time, insert_ledger, lock_account, next_id,
    qualifying_spend, tier_in_effect,
)
from core import ids
from core.config import RETURN_WINDOW_DAYS
from core.rules import (
    InsufficientPoints, Lot, eligible_net, fifo_consume, lot_dates, lots_to_expire, points_for,
    remaining_eligible_net, reversal_points, tier_for,
)

router = APIRouter()
EXPIRY_RUN_TIME = time(0, 5)
LAST_EXPIRY_RUN = date(2027, 1, 1)


# ---------------------------------------------------------------- orders

@router.post("/orders", response_model=OrderResult, status_code=201, tags=["writes"])
def create_order(order: OrderIn):
    with transaction() as conn:
        account = lock_account(conn, order.loy_id)
        check_write_time(conn, order.loy_id, order.order_ts)
        if order.order_ts.date() < account["opened_at"]:
            raise ApiError(409, "DATE_OUT_OF_RANGE", "order is before the account was opened")
        day = order.order_ts.date()
        tier = tier_in_effect(conn, order.loy_id, day)        # an upgrade only applies from the next day
        ord_id = next_id(conn, "ord_seq")
        conn.execute("INSERT INTO orders VALUES (%s, %s, %s, %s, %s, %s)",
                     (ord_id, order.loy_id, order.order_ts, order.channel, order.shipping_amount, order.tax_amount))
        results, total = [], 0
        for line_no, line in enumerate(order.lines, start=1):
            line_id = ids.line_id(ord_id, line_no)
            try:
                eligible = eligible_net(line.unit_price, line.qty, line.discount, line.category)
            except ValueError as exc:
                raise ApiError(400, "INVALID_LINE", f"line {line_no}: {exc}")
            conn.execute("INSERT INTO order_line VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                         (line_id, ord_id, line_no, line.sku, line.category, line.unit_price, line.qty, line.discount))
            points = points_for(eligible, tier.earn_rate)
            pts_id = None
            if points > 0:                                     # a 0-point line gets no ledger entry
                available_from, expires_at = lot_dates(day)
                pts_id = insert_ledger(conn, pts_id=next_id(conn, "pts_seq"), loy_id=order.loy_id,
                                       txn_type="ACCRUAL", points=points, txn_ts=order.order_ts, line_id=line_id,
                                       eligible_net=eligible, earn_rate=tier.earn_rate,
                                       available_from=available_from, expires_at=expires_at)
                total += points
            results.append({"line_id": line_id, "eligible_net": eligible, "points": points, "pts_id": pts_id})
        add_to_balance(conn, order.loy_id, total)

        # Tier check right after the order (the generator checks at end of day; same outcome).
        spend = qualifying_spend(conn, order.loy_id)
        current = TIER_BY_ID[account["tier_id"]]
        target = tier_for(spend)
        change = None
        if target.rank > current.rank:                         # upgrades only
            change = {"tier_change_id": next_id(conn, "tch_seq"), "old_tier_id": account["tier_id"],
                      "new_tier_id": ids.tier_id(target.code), "effective_date": day + timedelta(days=1),
                      "qualifying_spend_at_change": spend}
            conn.execute("INSERT INTO tier_change VALUES (%s, %s, %s, %s, %s, %s)",
                         (change["tier_change_id"], order.loy_id, change["old_tier_id"], change["new_tier_id"],
                          change["effective_date"], spend))
            conn.execute("UPDATE loyalty_account SET tier_id = %s WHERE loy_id = %s",
                         (change["new_tier_id"], order.loy_id))
    return {"ord_id": ord_id, "loy_id": order.loy_id, "tier_id_used": ids.tier_id(tier.code),
            "earn_rate": tier.earn_rate, "lines": results, "points_total": total,
            "qualifying_spend": spend, "tier_change": change}


# ---------------------------------------------------------------- returns

@router.post("/orders/{ord_id}/returns", response_model=ReturnResult, status_code=201, tags=["writes"])
def create_return(ord_id: str, ret: ReturnIn):
    with transaction() as conn:
        order = conn.execute("SELECT * FROM orders WHERE ord_id = %s", (ord_id,)).fetchone()
        if order is None:
            raise not_found("order", ord_id)
        lock_account(conn, order["loy_id"])
        line = conn.execute("SELECT * FROM order_line WHERE ord_id = %s AND line_no = %s",
                            (ord_id, ret.line_no)).fetchone()
        if line is None:
            raise not_found("order line", f"{ord_id} #{ret.line_no}")
        days = (ret.return_ts.date() - order["order_ts"].date()).days
        if ret.return_ts <= order["order_ts"] or days > RETURN_WINDOW_DAYS:
            raise ApiError(409, "RETURN_WINDOW_CLOSED",
                           f"returns are allowed up to day {RETURN_WINDOW_DAYS} after the order; this is day {days}")
        check_write_time(conn, order["loy_id"], ret.return_ts)
        before = conn.execute("SELECT COALESCE(SUM(qty_returned), 0) AS q FROM line_return WHERE line_id = %s",
                              (line["line_id"],)).fetchone()["q"]
        if before + ret.qty > line["qty"]:
            raise ApiError(409, "RETURN_EXCEEDS_QUANTITY",
                           f"{before} of {line['qty']} already returned; cannot return {ret.qty} more")
        after = before + ret.qty
        net = line["unit_price"] * line["qty"] - line["discount"]
        refund = remaining_eligible_net(net, line["qty"], before) - remaining_eligible_net(net, line["qty"], after)
        return_id = next_id(conn, "ret_seq")
        conn.execute("INSERT INTO line_return VALUES (%s, %s, %s, %s, %s)",
                     (return_id, line["line_id"], ret.return_ts, ret.qty, refund))

        accrual = conn.execute("SELECT * FROM points_transaction WHERE txn_type = 'ACCRUAL' AND line_id = %s",
                               (line["line_id"],)).fetchone()
        points, reversal_id = 0, None
        if accrual:
            already = conn.execute("""SELECT COALESCE(-SUM(points), 0) AS r FROM points_transaction
                                      WHERE txn_type = 'REVERSAL' AND reverses_pts_id = %s""",
                                   (accrual["pts_id"],)).fetchone()["r"]
            eligible = eligible_net(line["unit_price"], line["qty"], line["discount"], line["category"])
            points = reversal_points(accrual["points"], eligible, line["qty"], after, accrual["earn_rate"], already)
            if points > 0:
                reversal_id = insert_ledger(conn, pts_id=next_id(conn, "pts_seq"), loy_id=order["loy_id"],
                                            txn_type="REVERSAL", points=-points, txn_ts=ret.return_ts,
                                            return_id=return_id, reverses_pts_id=accrual["pts_id"])
                add_to_balance(conn, order["loy_id"], -points)
        spend = qualifying_spend(conn, order["loy_id"])        # goes down; tiers never do
    return {"return_id": return_id, "line_id": line["line_id"], "qty_returned": ret.qty, "refund_amount": refund,
            "points_reversed": points, "reversal_pts_id": reversal_id,
            "reverses_pts_id": accrual["pts_id"] if accrual else None, "qualifying_spend": spend}


# ---------------------------------------------------------------- redemptions

@router.post("/redemptions", response_model=RedemptionResult, status_code=201, tags=["writes"],
             responses={409: {"model": RedemptionResult, "description": "Rejected: INSUFFICIENT_POINTS"}})
def create_redemption(req: RedemptionIn):
    with transaction() as conn:
        reward = conn.execute("SELECT * FROM reward WHERE reward_id = %s AND active", (req.reward_id,)).fetchone()
        if reward is None:
            raise not_found("reward", req.reward_id)
        lock_account(conn, req.loy_id)       # a concurrent redemption for this account waits here
        check_write_time(conn, req.loy_id, req.requested_at)
        lots = [Lot(r["lot_pts_id"], r["expires_at"], r["remaining"]) for r in conn.execute(
            """SELECT lot_pts_id, expires_at, remaining FROM lot_status(%s, %s)
               WHERE status IN ('AVAILABLE', 'PARTIAL') AND remaining > 0""",
            (req.requested_at.date(), req.loy_id))]
        available = sum(l.remaining for l in lots)
        cost = reward["points_cost"]
        rdm_id = next_id(conn, "rdm_seq")
        result = {"rdm_id": rdm_id, "points_cost": cost, "available_before": available, "lots_used": [],
                  "pts_id": None, "status": "APPROVED", "reject_reason": None}
        try:
            used = fifo_consume(lots, cost)
        except InsufficientPoints:
            # The exception path is recorded too: a REJECTED row, no ledger entry. Committed.
            result.update(status="REJECTED", reject_reason="INSUFFICIENT_POINTS")
            conn.execute("INSERT INTO redemption VALUES (%s, %s, %s, %s, %s, 'REJECTED', 'INSUFFICIENT_POINTS')",
                         (rdm_id, req.loy_id, req.reward_id, req.requested_at, cost))
        else:
            conn.execute("INSERT INTO redemption VALUES (%s, %s, %s, %s, %s, 'APPROVED', NULL)",
                         (rdm_id, req.loy_id, req.reward_id, req.requested_at, cost))
            for lot_id, points in used:
                conn.execute("INSERT INTO redemption_lot VALUES (%s, %s, %s)", (rdm_id, lot_id, points))
            result["pts_id"] = insert_ledger(conn, pts_id=next_id(conn, "pts_seq"), loy_id=req.loy_id,
                                             txn_type="REDEMPTION", points=-cost, txn_ts=req.requested_at,
                                             rdm_id=rdm_id)
            result["lots_used"] = [{"lot_pts_id": lot_id, "points_used": points} for lot_id, points in used]
            add_to_balance(conn, req.loy_id, -cost)
    if result["status"] == "REJECTED":
        return JSONResponse(status_code=409, content=RedemptionResult(**result).model_dump(mode="json"))
    return result


# ---------------------------------------------------------------- expiry

@router.post("/expiry-batches/run", response_model=ExpiryResult, tags=["writes"])
def run_expiry(run_date: date = Query(..., description="1st of a month in 2026, or 2027-01-01")):
    if run_date.day != 1 or not (run_date.year == 2026 or run_date == LAST_EXPIRY_RUN):
        raise ApiError(400, "INVALID_RUN_DATE", "run_date must be the 1st of a month in 2026, or 2027-01-01")
    with transaction() as conn:
        existing = conn.execute("SELECT exp_id FROM expiry_batch WHERE run_date = %s", (run_date,)).fetchone()
        if existing:                                   # idempotent: a date runs at most once
            return {"exp_id": existing["exp_id"], "run_date": run_date, "already_run": True,
                    "lots_expired": 0, "points_expired": 0}
        latest = conn.execute("SELECT max(run_date) AS d FROM expiry_batch").fetchone()["d"]
        if latest and run_date < latest:
            raise ApiError(409, "DATE_OUT_OF_RANGE", f"a later batch ({latest}) has already run")
        executed_at = datetime.combine(run_date, EXPIRY_RUN_TIME)
        exp_id = next_id(conn, "exp_seq")
        inserted = conn.execute("""INSERT INTO expiry_batch VALUES (%s, %s, %s)
                                   ON CONFLICT (run_date) DO NOTHING RETURNING exp_id""",
                                (exp_id, run_date, executed_at)).fetchone()
        if inserted is None:                           # a concurrent request ran this date first
            existing = conn.execute("SELECT exp_id FROM expiry_batch WHERE run_date = %s", (run_date,)).fetchone()
            return {"exp_id": existing["exp_id"], "run_date": run_date, "already_run": True,
                    "lots_expired": 0, "points_expired": 0}
        # Lock every affected account in loy_id order (single-account writers can't deadlock with this).
        accounts = [r["loy_id"] for r in conn.execute(
            """SELECT DISTINCT loy_id FROM lot_status(%s) WHERE expires_at <= %s AND remaining > 0
               ORDER BY loy_id""", (run_date, run_date))]
        conn.execute("SELECT loy_id FROM loyalty_account WHERE loy_id = ANY(%s) ORDER BY loy_id FOR UPDATE",
                     (accounts,))
        lots = conn.execute("""SELECT lot_pts_id, loy_id, expires_at, remaining FROM lot_status(%s)
                               WHERE loy_id = ANY(%s)""", (run_date, accounts)).fetchall()
        owner = {l["lot_pts_id"]: l["loy_id"] for l in lots}
        due = lots_to_expire([Lot(l["lot_pts_id"], l["expires_at"], l["remaining"]) for l in lots], run_date)
        per_account = {}
        for lot_id, points in due:
            insert_ledger(conn, pts_id=next_id(conn, "pts_seq"), loy_id=owner[lot_id], txn_type="EXPIRY",
                          points=-points, txn_ts=executed_at, exp_id=exp_id, expires_pts_id=lot_id)
            per_account[owner[lot_id]] = per_account.get(owner[lot_id], 0) + points
        for loy_id, points in per_account.items():
            add_to_balance(conn, loy_id, -points)
    return {"exp_id": exp_id, "run_date": run_date, "already_run": False,
            "lots_expired": len(due), "points_expired": sum(p for _, p in due)}
