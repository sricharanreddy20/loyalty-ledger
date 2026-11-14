from datetime import date
from decimal import Decimal

from core.rules import Lot, fifo_consume
from tests.api.conftest import WRITE_DAY, ledger_sum

# Bronze accounts that never had a tier change (rate 1 on 31 Dec), most spend first.
BRONZE = """SELECT a.loy_id FROM loyalty_account a
            WHERE a.tier_id = 'TIER-BRONZE' AND NOT EXISTS (SELECT 1 FROM tier_change t WHERE t.loy_id = a.loy_id)
              AND NOT EXISTS (SELECT 1 FROM eval.ground_truth g WHERE g.record_id = a.loy_id)
            ORDER BY a.loy_id"""


def line(category="HOME", price="1000.00", qty=1, discount="0.00"):
    return {"sku": "SKU-TEST-0001", "category": category, "unit_price": price, "qty": qty, "discount": discount}


def order(client, loy_id, time, *lines):
    return client.post("/orders", json={"loy_id": loy_id, "order_ts": f"{WRITE_DAY}T{time}",
                                        "channel": "ONLINE", "lines": list(lines)})


# ---------------------------------------------------------------- orders

def test_order_accrues_per_line(client, sql, pick):
    loy_id = pick(BRONZE)
    before = ledger_sum(sql, loy_id)
    r = order(client, loy_id, "22:00:00",
              line("HOME", "1000.00", 2, "100.00"),      # 1900 eligible -> 19 points
              line("GIFT_CARD", "500.00", 1),            # never eligible -> no ledger entry
              line("BOOKS", "99.99", 1))                 # 0 points -> no ledger entry
    assert r.status_code == 201, r.json()
    body = r.json()
    assert [l["points"] for l in body["lines"]] == [19, 0, 0]
    assert body["lines"][1]["pts_id"] is None and body["lines"][2]["pts_id"] is None
    assert body["tier_id_used"] == "TIER-BRONZE" and body["points_total"] == 19
    assert ledger_sum(sql, loy_id) == before + 19
    lot = [l for l in client.get(f"/accounts/{loy_id}/lots").json() if l["lot_pts_id"] == body["lines"][0]["pts_id"]][0]
    assert (lot["status"], lot["available_from"], lot["expires_at"]) == ("PENDING", "2027-01-31", "2027-06-29")
    assert client.get(f"/accounts/{loy_id}").json()["balance_matches"] is True


def test_crossing_a_threshold_upgrades_from_the_next_day(client, pick):
    loy_id = pick(BRONZE)
    spend = Decimal(client.get(f"/accounts/{loy_id}").json()["qualifying_spend"])
    needed = Decimal("25000.00") - spend + Decimal("100.00")
    first = order(client, loy_id, "22:00:00", line("ELECTRONICS", f"{needed:.2f}")).json()
    assert Decimal(first["earn_rate"]) == 1 and first["tier_change"]["new_tier_id"] == "TIER-SILVER"
    assert first["tier_change"]["effective_date"] == "2027-01-01"
    second = order(client, loy_id, "22:05:00", line("HOME", "1000.00")).json()
    assert second["tier_id_used"] == "TIER-BRONZE"             # same day: still the old rate
    assert second["tier_change"] is None
    assert client.get(f"/accounts/{loy_id}").json()["tier_id"] == "TIER-SILVER"


def test_failed_line_rolls_back_the_whole_order(client, sql, pick):
    loy_id = pick(BRONZE)
    orders_before = sql("SELECT count(*) AS n FROM orders WHERE loy_id = %s", (loy_id,))[0]["n"]
    r = order(client, loy_id, "22:00:00", line("HOME", "1000.00"), line("HOME", "10.00", 1, "11.00"))
    assert r.status_code == 400 and r.json()["error"] == "INVALID_LINE"
    assert sql("SELECT count(*) AS n FROM orders WHERE loy_id = %s", (loy_id,))[0]["n"] == orders_before
    assert client.get(f"/accounts/{loy_id}").json()["balance_matches"] is True


def test_invalid_requests_are_400_json(client, pick):
    loy_id = pick(BRONZE)
    missing = client.post("/orders", json={"loy_id": loy_id, "order_ts": f"{WRITE_DAY}T22:00:00", "channel": "ONLINE"})
    assert missing.status_code == 400 and missing.json()["error"] == "INVALID_REQUEST"
    with_tz = client.post("/orders", json={"loy_id": loy_id, "order_ts": f"{WRITE_DAY}T22:00:00+05:30",
                                           "channel": "ONLINE", "lines": [line()]})
    assert with_tz.status_code == 400
    bad_category = order(client, loy_id, "22:00:00", line("TOYS"))
    assert bad_category.status_code == 400


def test_writes_cannot_rewrite_history(client, pick):
    loy_id = pick(BRONZE)
    r = client.post("/orders", json={"loy_id": loy_id, "order_ts": "2026-11-20T10:00:00",
                                     "channel": "STORE", "lines": [line()]})
    assert r.status_code == 409 and r.json()["error"] == "DATE_OUT_OF_RANGE"


# ---------------------------------------------------------------- returns

def test_two_partial_returns_then_the_rest(client, sql, pick):
    loy_id = pick(BRONZE)
    placed = order(client, loy_id, "22:00:00", line("HOME", "2500.00", 4, "1000.00")).json()   # 9000 -> 90 points
    ord_id, accrual = placed["ord_id"], placed["lines"][0]["pts_id"]
    assert placed["points_total"] == 90

    def ret(time, qty):
        return client.post(f"/orders/{ord_id}/returns",
                           json={"line_no": 1, "qty": qty, "return_ts": f"{WRITE_DAY}T{time}"})
    first, second = ret("22:10:00", 1).json(), ret("22:20:00", 2).json()
    assert (first["points_reversed"], second["points_reversed"]) == (23, 45)
    assert first["reverses_pts_id"] == second["reverses_pts_id"] == accrual
    assert first["refund_amount"] == "2250.00"
    too_many = ret("22:25:00", 2)
    assert too_many.status_code == 409 and too_many.json()["error"] == "RETURN_EXCEEDS_QUANTITY"
    last = ret("22:30:00", 1).json()
    assert last["points_reversed"] == 22
    lot = [l for l in client.get(f"/accounts/{loy_id}/lots").json() if l["lot_pts_id"] == accrual][0]
    assert (lot["remaining"], lot["status"]) == (0, "REVERSED")
    assert client.get(f"/accounts/{loy_id}").json()["balance_matches"] is True


def test_return_outside_window_is_rejected(client, sql):
    old = sql("""SELECT o.ord_id FROM orders o WHERE o.order_ts < '2026-11-30'
                 AND NOT EXISTS (SELECT 1 FROM eval.ground_truth g WHERE g.record_id = o.loy_id)
                 ORDER BY o.order_ts DESC LIMIT 1""")[0]["ord_id"]
    r = client.post(f"/orders/{old}/returns", json={"line_no": 1, "qty": 1, "return_ts": f"{WRITE_DAY}T22:00:00"})
    assert r.status_code == 409 and r.json()["error"] == "RETURN_WINDOW_CLOSED"


def test_return_unknown_order_or_line(client, sql):
    assert client.post("/orders/ORD-2026-999999/returns",
                       json={"line_no": 1, "qty": 1, "return_ts": f"{WRITE_DAY}T22:00:00"}).status_code == 404
    any_order = sql("SELECT ord_id FROM orders LIMIT 1")[0]["ord_id"]
    assert client.post(f"/orders/{any_order}/returns",
                       json={"line_no": 99, "qty": 1, "return_ts": f"{WRITE_DAY}T22:00:00"}).status_code == 404


# ---------------------------------------------------------------- redemptions

def test_redemption_consumes_lots_fifo(client, sql, pick):
    loy_id = pick("""SELECT loy_id FROM lot_status('2026-12-31') WHERE status IN ('AVAILABLE','PARTIAL')
                     AND NOT EXISTS (SELECT 1 FROM eval.ground_truth g WHERE g.record_id = loy_id)
                     GROUP BY loy_id HAVING count(*) >= 3 AND SUM(remaining) >= 600 ORDER BY loy_id""")
    lots_before = [l for l in client.get(f"/accounts/{loy_id}/lots").json() if l["status"] in ("AVAILABLE", "PARTIAL")]
    expected = fifo_consume([Lot(l["lot_pts_id"], date.fromisoformat(l["expires_at"]), l["remaining"])
                             for l in lots_before], 500)
    balance = client.get(f"/accounts/{loy_id}").json()["ledger_balance"]
    r = client.post("/redemptions", json={"loy_id": loy_id, "reward_id": "RWD-0003",     # 500 points
                                          "requested_at": f"{WRITE_DAY}T22:40:00"})
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["status"] == "APPROVED" and body["points_cost"] == 500
    assert [(u["lot_pts_id"], u["points_used"]) for u in body["lots_used"]] == expected
    detail = client.get(f"/accounts/{loy_id}").json()
    assert detail["ledger_balance"] == balance - 500 and detail["balance_matches"] is True


def test_redemption_without_enough_points_is_rejected_and_recorded(client, sql, pick):
    loy_id = pick("""SELECT a.loy_id FROM loyalty_account a
                     WHERE COALESCE((SELECT SUM(remaining) FROM lot_status('2026-12-31', a.loy_id)
                                     WHERE status IN ('AVAILABLE','PARTIAL')), 0) < 100
                       AND NOT EXISTS (SELECT 1 FROM eval.ground_truth g WHERE g.record_id = a.loy_id)
                     ORDER BY a.loy_id""")
    balance = ledger_sum(sql, loy_id)
    r = client.post("/redemptions", json={"loy_id": loy_id, "reward_id": "RWD-0001",
                                          "requested_at": f"{WRITE_DAY}T22:40:00"})
    assert r.status_code == 409
    body = r.json()
    assert body["status"] == "REJECTED" and body["reject_reason"] == "INSUFFICIENT_POINTS"
    row = sql("SELECT status FROM redemption WHERE rdm_id = %s", (body["rdm_id"],))
    assert row == [{"status": "REJECTED"}]                      # the exception path is kept
    assert sql("SELECT count(*) AS n FROM points_transaction WHERE rdm_id = %s", (body["rdm_id"],))[0]["n"] == 0
    assert ledger_sum(sql, loy_id) == balance


def test_redemption_unknown_reward_or_account(client):
    r = client.post("/redemptions", json={"loy_id": "LOY-CUST-000001", "reward_id": "RWD-9999",
                                          "requested_at": f"{WRITE_DAY}T22:40:00"})
    assert r.status_code == 404
    r = client.post("/redemptions", json={"loy_id": "LOY-CUST-999999", "reward_id": "RWD-0001",
                                          "requested_at": f"{WRITE_DAY}T22:40:00"})
    assert r.status_code == 404
