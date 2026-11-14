"""CLI: python -m generator --scale starter [--seed 20261022]

Step 3 builds the dataset in memory and prints a summary. Exports and loading come in Step 4.
"""
import argparse
import time
from collections import Counter

from generator.config import DEFAULT_SEED, SCALES
from generator.generate import generate


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


def main():
    parser = argparse.ArgumentParser(description="Generate the loyalty points dataset.")
    parser.add_argument("--scale", choices=sorted(SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    started = time.perf_counter()        # timing only; never used in the data
    ds = generate(args.seed, args.scale)
    elapsed = time.perf_counter() - started
    print(f"scale={args.scale} seed={args.seed} generated in {elapsed:.2f}s")
    print(summary(ds))
    print(f"fingerprint {ds.fingerprint()}")


if __name__ == "__main__":
    main()
