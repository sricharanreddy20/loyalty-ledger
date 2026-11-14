"""Ten simultaneous redemptions for one account whose balance covers exactly one of them.
A real uvicorn server handles them on separate threads and separate DB connections;
SELECT ... FOR UPDATE on the account row must let exactly one through."""
import socket
import threading
import time
from collections import Counter

import httpx
import pytest
import uvicorn

from tests.api.conftest import WRITE_DAY, ledger_sum

REQUESTS = 10


@pytest.fixture(scope="module")
def server_url(test_db_url):
    from backend.app.main import app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_only_one_of_simultaneous_redemptions_succeeds(server_url, sql, pick):
    # An account and a reward with cost <= available < 2 * cost.
    loy_id = pick("""
        WITH avail AS (SELECT loy_id, SUM(remaining) AS a FROM lot_status('2026-12-31')
                       WHERE status IN ('AVAILABLE', 'PARTIAL') GROUP BY loy_id)
        SELECT v.loy_id FROM avail v
        WHERE EXISTS (SELECT 1 FROM reward r WHERE r.points_cost <= v.a AND v.a < 2 * r.points_cost)
          AND NOT EXISTS (SELECT 1 FROM eval.ground_truth g WHERE g.record_id = v.loy_id)
        ORDER BY v.a DESC, v.loy_id""")
    available = sql("""SELECT SUM(remaining) AS a FROM lot_status('2026-12-31', %s)
                       WHERE status IN ('AVAILABLE', 'PARTIAL')""", (loy_id,))[0]["a"]
    reward = sql("""SELECT reward_id, points_cost FROM reward WHERE points_cost <= %s AND %s < 2 * points_cost
                    ORDER BY points_cost DESC LIMIT 1""", (available, available))[0]
    balance_before = ledger_sum(sql, loy_id)
    payload = {"loy_id": loy_id, "reward_id": reward["reward_id"], "requested_at": f"{WRITE_DAY}T23:00:00"}

    barrier = threading.Barrier(REQUESTS)
    responses = [None] * REQUESTS

    def fire(i):
        with httpx.Client(timeout=30) as http:
            barrier.wait()                                   # all ten leave the gate together
            responses[i] = http.post(f"{server_url}/redemptions", json=payload)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(REQUESTS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert Counter(r.status_code for r in responses) == {201: 1, 409: REQUESTS - 1}
    assert all(r.json()["reject_reason"] == "INSUFFICIENT_POINTS" for r in responses if r.status_code == 409)
    rdm_ids = [r.json()["rdm_id"] for r in responses]
    assert len(set(rdm_ids)) == REQUESTS                         # sequence ids never collide

    rows = sql("SELECT status FROM redemption WHERE loy_id = %s AND requested_at = %s",
               (loy_id, f"{WRITE_DAY} 23:00:00"))
    assert Counter(r["status"] for r in rows) == {"APPROVED": 1, "REJECTED": REQUESTS - 1}
    assert sql("SELECT count(*) AS n FROM points_transaction WHERE rdm_id = ANY(%s)", (rdm_ids,))[0]["n"] == 1
    assert ledger_sum(sql, loy_id) == balance_before - reward["points_cost"]
    assert sql("SELECT min(remaining) AS m FROM lot_status('2026-12-31', %s)", (loy_id,))[0]["m"] >= 0
    stored = sql("SELECT current_balance FROM loyalty_account WHERE loy_id = %s", (loy_id,))[0]["current_balance"]
    assert stored == ledger_sum(sql, loy_id)
