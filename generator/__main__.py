"""CLI: python -m generator --scale starter [--seed N] [--out DIR] [--load] [--check-reproducible]

Builds the baseline, injects labelled anomalies, writes CSV/JSON/SQL + ground truth + manifest.
--load copies the export into PostgreSQL; --check-reproducible rebuilds twice in separate
processes (different PYTHONHASHSEED) and compares every file's SHA-256.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from generator.config import DEFAULT_SEED, SCALES
from generator.export import export, sha256_of
from generator.generate import build

ROOT = Path(__file__).resolve().parent.parent


def summary(ds) -> str:
    out = ["Row counts:"]
    out += [f"  {name:<20} {count:>9,}" for name, count in ds.row_counts().items()]

    out.append("Ledger by type:")
    by_type = Counter(r["txn_type"] for r in ds.points_transaction)
    out += [f"  {t:<20} {by_type[t]:>9,}" for t in ("ACCRUAL", "REVERSAL", "REDEMPTION", "EXPIRY")]

    out.append("Final tier mix:")
    tiers = Counter(a["tier_id"] for a in ds.loyalty_account)
    total = len(ds.loyalty_account)
    out += [f"  {t['tier_id']:<20} {tiers[t['tier_id']]:>9,}  ({100 * tiers[t['tier_id']] / total:.1f}%)"
            for t in ds.tier]

    status = Counter(r["status"] for r in ds.redemption)
    pairs = Counter((r["loy_id"], r["requested_at"]) for r in ds.redemption)
    by_rdm = {r["rdm_id"]: r for r in ds.redemption}
    pair_outcomes = Counter()
    for (loy, ts), n in pairs.items():
        if n == 2:
            pair = sorted((r for r in ds.redemption if r["loy_id"] == loy and r["requested_at"] == ts),
                          key=lambda r: r["rdm_id"])
            pair_outcomes["+".join(r["status"] for r in pair)] += 1
    out.append(f"Redemptions: {status['APPROVED']:,} approved, {status['REJECTED']:,} rejected; "
               f"concurrent pairs: {dict(pair_outcomes)}")

    lines = {l["line_id"]: l for l in ds.order_line}
    per_line = Counter(r["line_id"] for r in ds.line_return)
    returned_qty = Counter()
    for r in ds.line_return:
        returned_qty[r["line_id"]] += r["qty_returned"]
    full = sum(1 for lid in per_line if returned_qty[lid] == lines[lid]["qty"])
    two = sum(1 for n in per_line.values() if n == 2)
    out.append(f"Returned lines: {len(per_line):,} ({full:,} fully returned, {len(per_line) - full:,} partly; "
               f"{two:,} with two returns)")
    out.append(f"Tier changes: {len(ds.tier_change):,}; points outstanding: "
               f"{sum(a['current_balance'] for a in ds.loyalty_account):,}")
    return "\n".join(out)


def check_reproducible(scale: str, seed: int) -> bool:
    """Two independent runs (different hash seeds) must produce byte-identical files."""
    runs = []
    for n, hash_seed in ((1, "1"), (2, "987654")):
        out = ROOT / "output" / ".repro" / f"run{n}"
        env = dict(os.environ, PYTHONHASHSEED=hash_seed)
        subprocess.run([sys.executable, "-m", "generator", "--scale", scale, "--seed", str(seed),
                        "--out", str(out), "--quiet"], cwd=ROOT, env=env, check=True)
        runs.append({f["path"]: f for f in json.loads((out / "manifest.json").read_text())["files"]})
        runs[-1]["manifest.json"] = {"sha256": sha256_of(out / "manifest.json"), "rows": "-"}
    ok = runs[0].keys() == runs[1].keys()
    print(f"Reproducibility (seed {seed}, scale {scale}; run 1 PYTHONHASHSEED=1, run 2 PYTHONHASHSEED=987654):")
    for path in sorted(runs[0]):
        a, b = runs[0][path], runs[1].get(path, {})
        same = a["sha256"] == b.get("sha256")
        ok &= same
        print(f"  {'MATCH' if same else 'DIFF ':<5}  {path:<32} rows {a['rows']!s:>7}  sha256 {a['sha256'][:16]}")
    print("REPRODUCIBLE: all files identical" if ok else "NOT REPRODUCIBLE")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Generate the loyalty points dataset.")
    parser.add_argument("--scale", choices=sorted(SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, help="export folder (default output/<scale>)")
    parser.add_argument("--load", action="store_true", help="load the export into PostgreSQL")
    parser.add_argument("--check-reproducible", action="store_true", help="build twice and compare checksums")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    out = args.out or ROOT / "output" / args.scale

    started = time.perf_counter()        # timing only; never used in the data
    ds, ground_truth = build(args.seed, args.scale)
    built = time.perf_counter()
    manifest = export(ds, ground_truth, out, args.seed, args.scale)
    exported = time.perf_counter()
    if not args.quiet:
        print(f"scale={args.scale} seed={args.seed}: generated in {built - started:.2f}s, "
              f"exported in {exported - built:.2f}s -> {out}")
        print(summary(ds))
        print("Anomalies injected: " + ", ".join(f"{k} {v}" for k, v in manifest["anomalies"].items()))
        print(f"fingerprint {manifest['dataset_fingerprint']}")

    if args.load:
        from generator.load import load      # psycopg only needed when loading
        t = time.perf_counter()
        counts = load(out)
        print(f"Loaded into PostgreSQL in {time.perf_counter() - t:.2f}s; row counts match manifest: "
              + ", ".join(f"{k} {v:,}" for k, v in counts.items()))

    if args.check_reproducible and not check_reproducible(args.scale, args.seed):
        sys.exit(1)


if __name__ == "__main__":
    main()
