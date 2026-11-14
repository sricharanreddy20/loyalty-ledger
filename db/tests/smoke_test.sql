-- Schema smoke test. Runs inside one transaction and rolls back, so it leaves no data.
-- It needs an EMPTY database with only db/schema.sql applied (its fixed test IDs would clash
-- with loaded data). Usage, with a throwaway database:
--   docker compose exec -T db psql -U loyalty -d loyalty -c "DROP DATABASE IF EXISTS smokecheck" -c "CREATE DATABASE smokecheck"
--   docker compose exec -T db psql -q -U loyalty -d smokecheck -f - < db/schema.sql
--   docker compose exec -T db psql -q -U loyalty -d smokecheck -v ON_ERROR_STOP=1 -f - < db/tests/smoke_test.sql
--   docker compose exec -T db psql -U loyalty -d loyalty -c "DROP DATABASE smokecheck"
\set QUIET on
\o /dev/null
BEGIN;

-- Runs a statement that MUST be rejected by a constraint (SQLSTATE class 23).
-- If it is accepted, raise an error, which stops the script.
CREATE FUNCTION pg_temp.expect_reject(label text, stmt text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE stmt;
    RAISE EXCEPTION 'NOT REJECTED: %', label;
EXCEPTION WHEN integrity_constraint_violation THEN
    RAISE NOTICE 'rejected as expected: %', label;
END $$;

-- ------------------------------------------------ 1. a valid mini-lifecycle loads
INSERT INTO tier VALUES ('TIER-BRONZE', 'BRONZE', 1, 0, 1), ('TIER-SILVER', 'SILVER', 2, 25000, 1.25);
INSERT INTO rule VALUES
    ('RULE-0001', 'EARN_RATE', 'TIER-BRONZE', NULL, 1, 'POINTS_PER_100_INR', 'Bronze earn rate', '2026-01-01', NULL),
    ('RULE-0002', 'CATEGORY_EXCLUSION', NULL, 'GIFT_CARD', NULL, NULL, 'Gift cards earn nothing', '2026-01-01', NULL);
INSERT INTO customer VALUES ('CUST-000001', 'Asha', 'Rao', 'asha@example.com', 'Pune', '2026-01-02');
INSERT INTO loyalty_account VALUES ('LOY-CUST-000001', 'CUST-000001', 'TIER-BRONZE', '2026-01-02', 0);
INSERT INTO orders VALUES ('ORD-2026-000001', 'LOY-CUST-000001', '2026-01-10 11:00', 'ONLINE', 50, 180);
INSERT INTO order_line VALUES ('ORD-2026-000001-01', 'ORD-2026-000001', 1, 'SKU-1', 'HOME', 2500, 4, 0);
INSERT INTO line_return VALUES ('RET-2026-000001', 'ORD-2026-000001-01', '2026-01-20 10:00', 1, 2500);
INSERT INTO reward VALUES ('RWD-0001', 'Coffee voucher', 30, TRUE);
INSERT INTO redemption VALUES
    ('RDM-2026-000001', 'LOY-CUST-000001', 'RWD-0001', '2026-03-01 09:00', 30, 'APPROVED', NULL),
    ('RDM-2026-000002', 'LOY-CUST-000001', 'RWD-0001', '2026-03-01 09:00', 30, 'REJECTED', 'INSUFFICIENT_POINTS');
INSERT INTO expiry_batch VALUES ('EXP-2026-000008', '2026-08-01', '2026-08-01 00:05');

-- accrual 100 (lot), reversal -25 (1 of 4 returned), redemption -30, expiry of the rest -45
INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, line_id, eligible_net, earn_rate, available_from, expires_at)
    VALUES ('PTS-2026-00000001', 'LOY-CUST-000001', 'ACCRUAL', 100, '2026-01-10 11:00', 'ORD-2026-000001-01', 10000, 1, '2026-02-10', '2026-07-09');
INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, return_id, reverses_pts_id)
    VALUES ('PTS-2026-00000002', 'LOY-CUST-000001', 'REVERSAL', -25, '2026-01-20 10:00', 'RET-2026-000001', 'PTS-2026-00000001');
INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, rdm_id)
    VALUES ('PTS-2026-00000003', 'LOY-CUST-000001', 'REDEMPTION', -30, '2026-03-01 09:00', 'RDM-2026-000001');
INSERT INTO redemption_lot VALUES ('RDM-2026-000001', 'PTS-2026-00000001', 30);
INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, exp_id, expires_pts_id)
    VALUES ('PTS-2026-00000004', 'LOY-CUST-000001', 'EXPIRY', -45, '2026-08-01 00:05', 'EXP-2026-000008', 'PTS-2026-00000001');
INSERT INTO tier_change VALUES ('TCH-2026-000001', 'LOY-CUST-000001', 'TIER-BRONZE', 'TIER-SILVER', '2026-01-11', 30000);
INSERT INTO eval.ground_truth VALUES ('LOY-CUST-000001', 'loyalty_account', 'BALANCE_MISMATCH', 'smoke', 'balance = ledger sum');
\echo 'valid lifecycle inserted'

-- ------------------------------------------------ 2. lot_status(as_of) walks the lifecycle
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        (DATE '2026-01-15', 'PENDING',   100),
        (DATE '2026-01-25', 'PENDING',    75),
        (DATE '2026-02-15', 'AVAILABLE',  75),
        (DATE '2026-03-15', 'PARTIAL',    45),
        (DATE '2026-07-15', 'EXPIRED',    45),   -- due, batch not run yet
        (DATE '2026-08-02', 'EXPIRED',     0)
    ) AS t(d, want_status, want_remaining)
    LOOP
        PERFORM 1 FROM lot_status(r.d)
         WHERE lot_pts_id = 'PTS-2026-00000001'
           AND status = r.want_status AND remaining = r.want_remaining;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'lot_status(%) expected % / %', r.d, r.want_status, r.want_remaining;
        END IF;
        RAISE NOTICE 'lot_status(%) = % remaining %', r.d, r.want_status, r.want_remaining;
    END LOOP;
    IF (SELECT count(*) FROM lot_status('2026-01-09')) <> 0 THEN
        RAISE EXCEPTION 'lot earned after as_of must not appear';
    END IF;
    IF (SELECT SUM(points) FROM points_transaction WHERE loy_id = 'LOY-CUST-000001') <> 0 THEN
        RAISE EXCEPTION 'ledger sum should be 0';
    END IF;
END $$;
\echo 'lot_status checks passed'

-- ------------------------------------------------ 3. structural violations are rejected
SELECT pg_temp.expect_reject('bad customer id format',
    $q$INSERT INTO customer VALUES ('CUST-1', 'X', 'Y', 'x@example.com', 'Pune', '2026-01-01')$q$);
SELECT pg_temp.expect_reject('loy_id does not match customer',
    $q$INSERT INTO customer VALUES ('CUST-000002', 'X', 'Y', 'x@example.com', 'Pune', '2026-01-01');
       INSERT INTO loyalty_account VALUES ('LOY-CUST-000009', 'CUST-000002', 'TIER-BRONZE', '2026-01-01', 0)$q$);
SELECT pg_temp.expect_reject('second account for same customer',
    $q$INSERT INTO loyalty_account VALUES ('LOY-CUST-000001', 'CUST-000001', 'TIER-BRONZE', '2026-01-01', 0)$q$);
SELECT pg_temp.expect_reject('order line for missing order',
    $q$INSERT INTO order_line VALUES ('ORD-2026-000099-01', 'ORD-2026-000099', 1, 'S', 'HOME', 10, 1, 0)$q$);
SELECT pg_temp.expect_reject('unknown category',
    $q$INSERT INTO order_line VALUES ('ORD-2026-000001-02', 'ORD-2026-000001', 2, 'S', 'TOYS', 10, 1, 0)$q$);
SELECT pg_temp.expect_reject('zero quantity',
    $q$INSERT INTO order_line VALUES ('ORD-2026-000001-02', 'ORD-2026-000001', 2, 'S', 'HOME', 10, 0, 0)$q$);
SELECT pg_temp.expect_reject('discount above line amount',
    $q$INSERT INTO order_line VALUES ('ORD-2026-000001-02', 'ORD-2026-000001', 2, 'S', 'HOME', 10, 1, 11)$q$);
SELECT pg_temp.expect_reject('line_id does not match order + line_no',
    $q$INSERT INTO order_line VALUES ('ORD-2026-000001-07', 'ORD-2026-000001', 2, 'S', 'HOME', 10, 1, 0)$q$);
SELECT pg_temp.expect_reject('positive REVERSAL',
    $q$INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, return_id, reverses_pts_id)
       VALUES ('PTS-2026-00000009', 'LOY-CUST-000001', 'REVERSAL', 5, '2026-01-21', 'RET-2026-000001', 'PTS-2026-00000001')$q$);
SELECT pg_temp.expect_reject('REVERSAL pointing at missing accrual',
    $q$INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, return_id, reverses_pts_id)
       VALUES ('PTS-2026-00000009', 'LOY-CUST-000001', 'REVERSAL', -5, '2026-01-21', 'RET-2026-000001', 'PTS-2026-99999999')$q$);
SELECT pg_temp.expect_reject('ACCRUAL without line',
    $q$INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, eligible_net, earn_rate, available_from, expires_at)
       VALUES ('PTS-2026-00000009', 'LOY-CUST-000001', 'ACCRUAL', 5, '2026-01-10', 500, 1, '2026-02-10', '2026-07-09')$q$);
SELECT pg_temp.expect_reject('second ACCRUAL for same line',
    $q$INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, line_id, eligible_net, earn_rate, available_from, expires_at)
       VALUES ('PTS-2026-00000009', 'LOY-CUST-000001', 'ACCRUAL', 5, '2026-01-10', 'ORD-2026-000001-01', 500, 1, '2026-02-10', '2026-07-09')$q$);
SELECT pg_temp.expect_reject('EXPIRY without lot reference',
    $q$INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, exp_id)
       VALUES ('PTS-2026-00000009', 'LOY-CUST-000001', 'EXPIRY', -5, '2026-08-01', 'EXP-2026-000008')$q$);
SELECT pg_temp.expect_reject('REJECTED redemption without reason',
    $q$INSERT INTO redemption VALUES ('RDM-2026-000003', 'LOY-CUST-000001', 'RWD-0001', '2026-03-02', 30, 'REJECTED', NULL)$q$);
SELECT pg_temp.expect_reject('APPROVED redemption with reason',
    $q$INSERT INTO redemption VALUES ('RDM-2026-000003', 'LOY-CUST-000001', 'RWD-0001', '2026-03-02', 30, 'APPROVED', 'INSUFFICIENT_POINTS')$q$);
SELECT pg_temp.expect_reject('second batch on same run date',
    $q$INSERT INTO expiry_batch VALUES ('EXP-2026-000009', '2026-08-01', '2026-08-01 00:10')$q$);
SELECT pg_temp.expect_reject('tier change to same tier',
    $q$INSERT INTO tier_change VALUES ('TCH-2026-000002', 'LOY-CUST-000001', 'TIER-SILVER', 'TIER-SILVER', '2026-02-01', 30000)$q$);
SELECT pg_temp.expect_reject('rule effective_to before effective_from',
    $q$INSERT INTO rule VALUES ('RULE-0003', 'LOT_EXPIRY', NULL, NULL, 180, 'DAYS', 'x', '2026-06-01', '2026-01-01')$q$);
SELECT pg_temp.expect_reject('unknown anomaly type',
    $q$INSERT INTO eval.ground_truth VALUES ('X', 'loyalty_account', 'MADE_UP', 'x', 'x')$q$);

-- ------------------------------------------------ 4. anomalies must still be loadable
-- DOUBLE_EXPIRY: the same lot expired again by another batch is structurally valid.
INSERT INTO expiry_batch VALUES ('EXP-2026-000009', '2026-09-01', '2026-09-01 00:05');
INSERT INTO points_transaction (pts_id, loy_id, txn_type, points, txn_ts, exp_id, expires_pts_id)
    VALUES ('PTS-2026-00000005', 'LOY-CUST-000001', 'EXPIRY', -45, '2026-09-01 00:05', 'EXP-2026-000009', 'PTS-2026-00000001');
-- BALANCE_MISMATCH: snapshot may disagree with the ledger.
UPDATE loyalty_account SET current_balance = 999 WHERE loy_id = 'LOY-CUST-000001';
\echo 'anomaly rows loadable (double expiry, balance mismatch)'

ROLLBACK;
\echo 'SMOKE TEST PASSED (rolled back, no data left)'
