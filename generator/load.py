"""Load an export folder into PostgreSQL with COPY, in one transaction.

Re-applies db/schema.sql first (it drops and recreates our objects), so a load always
starts from an empty database. Then checks row counts in the DB against the manifest.
"""
import json
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
# sequence -> (table, id column): set past the highest loaded number so the API continues from there
SEQUENCES = {"ord_seq": ("orders", "ord_id"), "pts_seq": ("points_transaction", "pts_id"),
             "ret_seq": ("line_return", "return_id"), "rdm_seq": ("redemption", "rdm_id"),
             "exp_seq": ("expiry_batch", "exp_id"), "tch_seq": ("tier_change", "tier_change_id")}


def database_url() -> str:
    """DATABASE_URL from the environment, else from the project's .env file."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key == "DATABASE_URL":
                return value.strip()
    raise SystemExit("DATABASE_URL is not set (copy .env.example to .env)")


def _copy_csv(cur, table: str, path: Path):
    columns = path.open(encoding="utf-8").readline().strip()
    with cur.copy(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                copy.write(chunk)


def load(out_dir: Path, url: str | None = None) -> dict[str, int]:
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / "manifest.json").read_text())
    with psycopg.connect(url or database_url()) as conn:      # commits on success, rolls back on error
        with conn.cursor() as cur:
            cur.execute(SCHEMA.read_text())
            for table in manifest["table_rows"]:              # manifest keeps foreign-key load order
                _copy_csv(cur, table, out_dir / "csv" / f"{table}.csv")
            _copy_csv(cur, "eval.ground_truth", out_dir / "ground_truth.csv")
            for seq, (table, column) in SEQUENCES.items():
                cur.execute(f"SELECT setval('{seq}', COALESCE(MAX((regexp_match({column}, '([0-9]+)$'))[1]::int), 0) + 1, false) "
                            f"FROM {table}")
            counts = {}
            for table in list(manifest["table_rows"]) + ["eval.ground_truth"]:
                cur.execute(f"SELECT count(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
            cur.execute("ANALYZE")      # fresh planner statistics, so queries get good plans right away
    expected = dict(manifest["table_rows"], **{"eval.ground_truth": sum(manifest["anomalies"].values())})
    if counts != expected:
        raise SystemExit(f"row counts differ after load: {counts} vs {expected}")
    return counts
