"""Guards on what may be committed."""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FULL_ALLOWED = {"manifest.json", "validation_summary.json", "validation_summary.md",
                "anomaly_report.json", "anomaly_report.md"}


def _ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="not a git checkout")
def test_full_scale_data_files_are_never_committed():
    for name in ("csv/orders.csv", "json/orders.json", "sql/orders.sql", "ground_truth.csv", "ground_truth.json"):
        assert _ignored(f"output/full/{name}"), name
    for name in FULL_ALLOWED:
        assert not _ignored(f"output/full/{name}"), name


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="not a git checkout")
def test_brief_and_env_are_ignored():
    assert _ignored("brief/anything.md") and _ignored(".env")
