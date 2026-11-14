"""Validation and detector on the freshly loaded starter data (runs before any API write)."""
import inspect

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from backend.app import detector, sql_rules
from backend.app.detector import ISSUE_TYPES, detect, score
from backend.app.validation import CHECKS, run_validation
from generator.config import DEFAULT_SEED, SCALES
from generator.export import export
from generator.generate import generate
from generator.load import database_url, load

PER_TYPE = SCALES["starter"].anomalies_per_type
STRUCTURAL = {"Primary keys", "Foreign keys", "Allowed values", "Duplicates"}


@pytest.fixture(scope="module")
def validation(client):
    response = client.get("/validation")
    assert response.status_code == 200
    return response.json()


@pytest.fixture(scope="module")
def report(client):
    response = client.get("/anomalies")
    assert response.status_code == 200
    return response.json()


def test_no_unexpected_failures(validation):
    t = validation["totals"]
    assert t["FAIL"] == 0
    assert t["checks"] == len(CHECKS) == t["PASS"] + t["EXPECTED_FAIL"]


def test_structural_checks_pass(validation):
    for c in validation["checks"]:
        if c["category"] in STRUCTURAL:
            assert c["status"] == "PASS", c


def test_each_anomaly_type_shows_up_as_expected_failure_with_ground_truth_count(validation):
    primary = [c for c in validation["checks"] if c["ground_truth_count"] is not None]
    assert {t for c in primary for t in c["explained_by"]} == set(ISSUE_TYPES)
    for c in primary:
        assert c["status"] == "EXPECTED_FAIL", c
        assert c["violations"] == c["explained"] == c["ground_truth_count"] == PER_TYPE, c


def test_detector_finds_every_label_and_nothing_else(report):
    for m in report["metrics"]:
        assert (m["ground_truth"], m["tp"], m["fp"], m["fn"]) == (PER_TYPE, PER_TYPE, 0, 0), m
        assert m["precision"] == m["recall"] == m["f1"] == 1.0
    assert report["overall"]["tp"] == PER_TYPE * len(ISSUE_TYPES)
    assert report["false_positives"] == report["false_negatives"] == []
    assert all(f["reason"] for f in report["flagged"])


def test_detector_never_reads_ground_truth():
    source = inspect.getsource(sql_rules) + "".join(detector.DETECTORS.values())
    assert "eval" not in source.lower().replace("evaluat", "")
    assert "ground_truth" not in source
    assert "ground_truth" not in inspect.getsource(detect)


def test_unlabelled_corruption_is_a_fail_and_a_false_positive(test_db_url):
    """Corrupt a clean account inside a transaction that is rolled back."""
    with psycopg.connect(test_db_url, row_factory=dict_row) as conn:
        loy_id = conn.execute("""SELECT loy_id FROM loyalty_account
                                 WHERE loy_id NOT IN (SELECT record_id FROM eval.ground_truth)
                                 ORDER BY loy_id LIMIT 1""").fetchone()["loy_id"]
        conn.execute("UPDATE loyalty_account SET current_balance = current_balance + 7 WHERE loy_id = %s", (loy_id,))
        a1 = next(c for c in run_validation(conn)["checks"] if c["id"] == "A1_balance_equals_ledger")
        result = score(conn, detect(conn))
        conn.rollback()
    assert a1["status"] == "FAIL" and a1["unexplained"] == 1
    balance = next(m for m in result["metrics"] if m["issue_type"] == "BALANCE_MISMATCH")
    assert (balance["tp"], balance["fp"], balance["fn"]) == (PER_TYPE, 1, 0)
    assert result["false_positives"][0]["record_id"] == loy_id


def test_clean_baseline_passes_everything_and_flags_nothing(tmp_path):
    """The baseline before anomaly injection, loaded with an empty ground truth into its own database."""
    base = database_url()
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS loyalty_test_clean WITH (FORCE)")
        admin.execute("CREATE DATABASE loyalty_test_clean")
    url = make_conninfo(base, dbname="loyalty_test_clean")
    export(generate(DEFAULT_SEED, "starter"), [], tmp_path, DEFAULT_SEED, "starter")
    load(tmp_path, url)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        summary = run_validation(conn)
        flagged = detect(conn)
    assert summary["totals"]["PASS"] == len(CHECKS), [c for c in summary["checks"] if c["status"] != "PASS"]
    assert flagged == []
