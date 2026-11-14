from tests.api.conftest import ledger_sum


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_list_accounts_filters(client):
    platinum = client.get("/accounts", params={"tier": "PLATINUM", "limit": 500}).json()
    assert platinum and all(a["tier_id"] == "TIER-PLATINUM" for a in platinum)
    found = client.get("/accounts", params={"search": "LOY-CUST-00012"}).json()
    assert found and all("LOY-CUST-00012" in a["loy_id"] for a in found)
    assert len(client.get("/accounts", params={"limit": 3}).json()) == 3


def test_account_detail_matches_ledger_and_lots(client, sql):
    loy_id = sql("SELECT loy_id FROM loyalty_account WHERE tier_id = 'TIER-GOLD' ORDER BY loy_id LIMIT 1")[0]["loy_id"]
    detail = client.get(f"/accounts/{loy_id}").json()
    assert detail["ledger_balance"] == ledger_sum(sql, loy_id)
    lots = sql("SELECT status, remaining FROM lot_status('2026-12-31', %s)", (loy_id,))
    assert detail["available"] == sum(l["remaining"] for l in lots if l["status"] in ("AVAILABLE", "PARTIAL"))
    assert detail["pending"] == sum(l["remaining"] for l in lots if l["status"] == "PENDING")


def test_balance_mismatch_accounts_are_reported(client, sql):
    labelled = sql("SELECT record_id FROM eval.ground_truth WHERE issue_type = 'BALANCE_MISMATCH' LIMIT 1")
    detail = client.get(f"/accounts/{labelled[0]['record_id']}").json()
    assert detail["balance_matches"] is False
    assert detail["current_balance"] != detail["ledger_balance"]


def test_ledger_is_in_time_order(client, sql):
    loy_id = sql("""SELECT loy_id FROM points_transaction GROUP BY loy_id ORDER BY count(*) DESC LIMIT 1""")[0]["loy_id"]
    entries = client.get(f"/accounts/{loy_id}/ledger").json()
    keys = [(e["txn_ts"], e["pts_id"]) for e in entries]
    assert keys == sorted(keys) and len(entries) > 10


def test_lots_arithmetic(client, sql):
    loy_id = sql("SELECT loy_id FROM points_transaction WHERE txn_type = 'EXPIRY' LIMIT 1")[0]["loy_id"]
    lots = client.get(f"/accounts/{loy_id}/lots").json()
    assert lots
    for lot in lots:
        assert lot["remaining"] == lot["original"] - lot["reversed"] - lot["redeemed"] - lot["expired"]


def test_reference_data(client):
    tiers = client.get("/tiers").json()
    assert [t["code"] for t in tiers] == ["BRONZE", "SILVER", "GOLD", "PLATINUM"]
    assert tiers[1]["min_qualifying_spend"] == "25000.00" and tiers[1]["earn_rate"] == "1.25"
    assert len(client.get("/rules").json()) == 9
    costs = [r["points_cost"] for r in client.get("/rewards").json()]
    assert costs == sorted(costs) and len(costs) == 10


def test_unknown_things_are_json_404s(client):
    for path in ("/accounts/LOY-CUST-999999", "/accounts/LOY-CUST-999999/ledger",
                 "/accounts/LOY-CUST-999999/lots", "/no-such-route"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"] == "NOT_FOUND"
