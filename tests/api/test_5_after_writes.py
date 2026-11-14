"""After all the API writes (orders, returns, redemptions, expiry runs), the data is still valid
and the detector still finds exactly the labelled anomalies."""
from backend.app.detector import ISSUE_TYPES
from generator.config import SCALES

PER_TYPE = SCALES["starter"].anomalies_per_type


def test_api_writes_keep_the_data_valid(client):
    summary = client.get("/validation").json()
    assert summary["totals"]["FAIL"] == 0, [c for c in summary["checks"] if c["status"] == "FAIL"]


def test_detector_scores_unchanged_after_writes(client):
    report = client.get("/anomalies").json()
    assert [(m["issue_type"], m["tp"], m["fp"], m["fn"]) for m in report["metrics"]] == \
        [(t, PER_TYPE, 0, 0) for t in ISSUE_TYPES]
