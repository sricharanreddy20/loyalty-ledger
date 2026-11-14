import csv
import json
import os

import pytest

from generator.config import DEFAULT_SEED
from generator.export import sha256_of, export
from generator.generate import build


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    ds, ground_truth = build(DEFAULT_SEED, "starter")
    out = tmp_path_factory.mktemp("export")
    manifest = export(ds, ground_truth, out, DEFAULT_SEED, "starter")
    return ds, ground_truth, out, manifest


def test_manifest_hashes_and_row_counts_match_files(exported):
    _, _, out, manifest = exported
    for f in manifest["files"]:
        path = out / f["path"]
        assert sha256_of(path) == f["sha256"]
        assert path.stat().st_size == f["bytes"]
        if path.suffix == ".csv":
            with path.open() as fh:
                assert sum(1 for _ in fh) - 1 == f["rows"]
        elif path.suffix == ".json":
            assert len(json.loads(path.read_text())) == f["rows"]


def test_manifest_has_no_timestamps_and_covers_every_file(exported):
    _, _, out, manifest = exported
    written = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()} - {"manifest.json"}
    assert written == {f["path"] for f in manifest["files"]}
    assert set(manifest) == {"seed", "scale", "dataset_fingerprint", "table_rows", "anomalies", "files"}


def test_inputs_carry_no_labels(exported):
    _, _, out, _ = exported
    for path in (out / "csv").glob("*.csv"):
        header = path.open().readline().strip().split(",")
        assert not {"issue_type", "rule_violated", "ground_truth"} & set(header), path.name
    assert not list((out / "csv").glob("ground_truth*"))
    assert not list((out / "json").glob("ground_truth*"))


def test_csv_round_trip_values(exported):
    ds, ground_truth, out, _ = exported
    with (out / "csv" / "points_transaction.csv").open() as f:
        first = next(csv.DictReader(f))
    row = ds.points_transaction[0]
    assert first["pts_id"] == row["pts_id"]
    assert first["txn_ts"] == row["txn_ts"].isoformat(sep=" ")
    assert first["eligible_net"] == str(row["eligible_net"])
    assert first["return_id"] == ""                       # NULL
    with (out / "ground_truth.csv").open() as f:
        assert sum(1 for _ in csv.DictReader(f)) == len(ground_truth)


def test_seed_sql_has_every_table(exported):
    ds, _, out, _ = exported
    text = (out / "sql" / "seed.sql").read_text()
    assert text.startswith("-- Loyalty ledger seed data") and text.rstrip().endswith("COMMIT;")
    for name in ds.tables():
        assert f"INSERT INTO {name} (" in text


def test_two_builds_are_byte_identical(exported, tmp_path):
    _, _, _, first = exported
    ds, ground_truth = build(DEFAULT_SEED, "starter")
    second = export(ds, ground_truth, tmp_path, DEFAULT_SEED, "starter")
    assert second == first


@pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1",
                    reason="resets the database; run with RUN_DB_TESTS=1")
def test_load_into_postgres(exported):
    from generator.load import load
    _, _, out, manifest = exported
    counts = load(out)
    assert counts["points_transaction"] == manifest["table_rows"]["points_transaction"]
    assert counts["eval.ground_truth"] == sum(manifest["anomalies"].values())
