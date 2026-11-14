"""Write CSV, JSON and SQL exports, the ground truth and a manifest (row counts + SHA-256).

Layout of output/<scale>/:
  csv/<table>.csv   json/<table>.json   sql/seed.sql      <- inputs (no labels)
  ground_truth.csv  ground_truth.json                     <- labels, kept apart
  manifest.json                                           <- no timestamps, so it is reproducible too
Formatting is fixed: ISO dates, money as 2-decimal strings, NULL as empty (CSV) / null (JSON), '\n' endings.
"""
import csv
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

GROUND_TRUTH_COLUMNS = ("record_id", "entity", "issue_type", "description", "rule_violated")
SQL_BATCH = 1000


def _plain(value):
    """A JSON-friendly value with a fixed text form."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _csv_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(_plain(value))


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    return "'" + str(_plain(value)).replace("'", "''") + "'"


def _columns(rows: list[dict]) -> list[str]:
    if not rows:
        raise ValueError("cannot export an empty table")
    return list(rows[0])


def write_csv(path: Path, rows: list[dict], columns=None):
    columns = list(columns or _columns(rows))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_csv_text(row[c]) for c in columns])


def write_json(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8") as f:
        f.write("[\n")
        f.write(",\n".join(json.dumps({k: _plain(v) for k, v in row.items()}, ensure_ascii=False) for row in rows))
        f.write("\n]\n")


def write_sql(path: Path, tables: dict[str, list[dict]], seed: int, scale: str):
    with path.open("w", encoding="utf-8") as f:
        f.write(f"-- Loyalty ledger seed data (seed {seed}, scale {scale}). Apply db/schema.sql first.\n")
        f.write("BEGIN;\n")
        for name, rows in tables.items():
            columns = _columns(rows)
            for start in range(0, len(rows), SQL_BATCH):
                batch = rows[start:start + SQL_BATCH]
                f.write(f"INSERT INTO {name} ({', '.join(columns)}) VALUES\n")
                f.write(",\n".join("(" + ", ".join(_sql_literal(r[c]) for c in columns) + ")" for r in batch))
                f.write(";\n")
        f.write("COMMIT;\n")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export(ds, ground_truth: list[dict], out_dir: Path, seed: int, scale: str) -> dict:
    out_dir = Path(out_dir)
    for sub in ("csv", "json", "sql"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    tables = ds.tables()
    rows_in = {}
    for name, rows in tables.items():
        write_csv(out_dir / "csv" / f"{name}.csv", rows)
        write_json(out_dir / "json" / f"{name}.json", rows)
        rows_in[f"csv/{name}.csv"] = rows_in[f"json/{name}.json"] = len(rows)
    write_sql(out_dir / "sql" / "seed.sql", tables, seed, scale)
    rows_in["sql/seed.sql"] = sum(len(r) for r in tables.values())
    write_csv(out_dir / "ground_truth.csv", ground_truth, GROUND_TRUTH_COLUMNS)
    write_json(out_dir / "ground_truth.json", ground_truth)
    rows_in["ground_truth.csv"] = rows_in["ground_truth.json"] = len(ground_truth)

    anomalies = {}
    for g in ground_truth:
        anomalies[g["issue_type"]] = anomalies.get(g["issue_type"], 0) + 1
    manifest = {
        "seed": seed,
        "scale": scale,
        "dataset_fingerprint": ds.fingerprint(),
        "table_rows": ds.row_counts(),
        "anomalies": anomalies,
        "files": [{"path": path, "rows": rows, "bytes": (out_dir / path).stat().st_size,
                   "sha256": sha256_of(out_dir / path)}
                  for path, rows in sorted(rows_in.items())],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
