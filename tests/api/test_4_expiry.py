"""Expiry runs last: the 2027-01-01 batch closes the 2026 write window."""


def ledger_rows(sql) -> int:
    return sql("SELECT count(*) AS n FROM points_transaction")[0]["n"]


def test_rerunning_an_existing_batch_adds_nothing(client, sql):
    before = ledger_rows(sql)
    body = client.post("/expiry-batches/run", params={"run_date": "2026-06-01"}).json()
    assert body == {"exp_id": "EXP-2026-000006", "run_date": "2026-06-01", "already_run": True,
                    "lots_expired": 0, "points_expired": 0}
    assert ledger_rows(sql) == before


def test_invalid_run_dates(client):
    for bad in ("2026-12-15", "2027-02-01", "2025-12-01"):
        r = client.post("/expiry-batches/run", params={"run_date": bad})
        assert r.status_code == 400 and r.json()["error"] == "INVALID_RUN_DATE"


def test_new_batch_expires_due_lots_once(client, sql):
    due = sql("""SELECT count(*) AS n, COALESCE(SUM(remaining), 0) AS p FROM lot_status('2027-01-01')
                 WHERE expires_at <= '2027-01-01' AND remaining > 0""")[0]
    assert due["n"] > 0
    first = client.post("/expiry-batches/run", params={"run_date": "2027-01-01"}).json()
    assert first["exp_id"] == "EXP-2026-000013" and first["already_run"] is False
    assert (first["lots_expired"], first["points_expired"]) == (due["n"], due["p"])
    assert sql("SELECT count(*) AS n FROM points_transaction WHERE exp_id = 'EXP-2026-000013'")[0]["n"] == due["n"]
    assert sql("""SELECT count(*) AS n FROM lot_status('2027-01-01')
                  WHERE expires_at <= '2027-01-01' AND remaining > 0""")[0]["n"] == 0

    before = ledger_rows(sql)
    again = client.post("/expiry-batches/run", params={"run_date": "2027-01-01"}).json()
    assert again["already_run"] is True and again["lots_expired"] == 0
    assert ledger_rows(sql) == before


def test_year_is_closed_after_the_2027_batch(client, sql):
    loy_id = sql("SELECT loy_id FROM loyalty_account ORDER BY loy_id LIMIT 1")[0]["loy_id"]
    r = client.post("/orders", json={"loy_id": loy_id, "order_ts": "2026-12-31T23:30:00", "channel": "STORE",
                                     "lines": [{"sku": "S", "category": "HOME", "unit_price": "100.00", "qty": 1}]})
    assert r.status_code == 409 and r.json()["error"] == "DATE_OUT_OF_RANGE"


def test_after_all_writes_only_labelled_accounts_mismatch(sql):
    """Every API write kept current_balance in step with the ledger."""
    mismatched = {r["loy_id"] for r in sql("""
        SELECT a.loy_id FROM loyalty_account a
        LEFT JOIN (SELECT loy_id, SUM(points) AS s FROM points_transaction GROUP BY loy_id) l USING (loy_id)
        WHERE a.current_balance <> COALESCE(l.s, 0)""")}
    labelled = {r["record_id"] for r in sql("SELECT record_id FROM eval.ground_truth WHERE issue_type = 'BALANCE_MISMATCH'")}
    assert mismatched == labelled
