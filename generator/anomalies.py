"""Inject labelled anomalies into a fully valid baseline.

Rules:
- Only the chosen records change. Candidates are restricted so a mutation cannot spill
  into other records (see each function).
- Each anomaly is placed on a different account, so every label is unambiguous.
- Types are injected in a fixed order; BALANCE_MISMATCH is last, after every other
  account's current_balance has been recomputed from the (mutated) ledger.
- New ledger / tier-change rows take the next free id; deleted reversals leave a gap.
Returns the ground truth: one row per mutated record.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from core import ids
from core.config import TIERS
from core.rules import tier_for

TIER_BY_ID = {ids.tier_id(t.code): t for t in TIERS}


def _next_number(rows, column) -> int:
    return max(int(r[column].rsplit("-", 1)[1]) for r in rows) + 1


def _label(record_id, entity, issue_type, description, rule_violated) -> dict:
    return {"record_id": record_id, "entity": entity, "issue_type": issue_type,
            "description": description, "rule_violated": rule_violated}


def _pick(rng, candidates, count, used_accounts):
    """Up to `count` candidates on accounts not used yet. candidates: [(account, item)], sorted."""
    chosen = []
    for account, item in rng.sample(candidates, len(candidates)):
        if len(chosen) == count:
            break
        if account not in used_accounts:
            used_accounts.add(account)
            chosen.append(item)
    return chosen


class _Ledger:
    """Indexes over the ledger used to find safe candidates."""

    def __init__(self, ds):
        self.ds = ds
        self.accruals = {r["pts_id"]: r for r in ds.points_transaction if r["txn_type"] == "ACCRUAL"}
        self.accruals_of = defaultdict(list)
        for r in self.accruals.values():
            self.accruals_of[r["loy_id"]].append(r)
        self.reversed = defaultdict(int)
        self.reversals_of_return = defaultdict(list)
        self.expiries_of_lot = defaultdict(list)
        for r in ds.points_transaction:
            if r["txn_type"] == "REVERSAL":
                self.reversed[r["reverses_pts_id"]] += -r["points"]
                self.reversals_of_return[r["return_id"]].append(r)
            elif r["txn_type"] == "EXPIRY":
                self.expiries_of_lot[r["expires_pts_id"]].append(r)
        self.usage_of_lot = defaultdict(list)       # [(rdm_id, points_used)]
        for r in ds.redemption_lot:
            self.usage_of_lot[r["lot_pts_id"]].append((r["rdm_id"], r["points_used"]))

    def available_lots(self, redemption) -> list[tuple[dict, int]]:
        """(lot, remaining) for lots available when this redemption was processed.

        rdm ids are issued in processing order, so 'before' == smaller rdm_id. An available
        lot has had all its reversals (they happen while pending) and no expiry yet.
        """
        day = redemption["requested_at"].date()
        result = []
        for lot in self.accruals_of[redemption["loy_id"]]:
            if lot["available_from"] <= day < lot["expires_at"]:
                used_before = sum(u for rdm, u in self.usage_of_lot[lot["pts_id"]] if rdm < redemption["rdm_id"])
                remaining = lot["points"] - self.reversed[lot["pts_id"]] - used_before
                if remaining > 0:
                    result.append((lot, remaining))
        return sorted(result, key=lambda x: (x[0]["expires_at"], x[0]["pts_id"]))

    def touched_after(self, lot_id, rdm_id) -> bool:
        return (any(rdm > rdm_id for rdm, _ in self.usage_of_lot[lot_id])
                or bool(self.expiries_of_lot[lot_id]))


# ---------------------------------------------------------------- 1. approved over balance

def _redemption_exceeds_balance(rng, ds, ledger, count, used):
    candidates = []
    for r in sorted(ds.redemption, key=lambda r: r["rdm_id"]):
        if r["status"] != "REJECTED":
            continue
        lots = ledger.available_lots(r)
        # Safe only if the lots it would drain are never touched again (no later redemption/expiry).
        if all(not ledger.touched_after(lot["pts_id"], r["rdm_id"]) for lot, _ in lots):
            candidates.append((r["loy_id"], (r, lots)))
    labels = []
    next_pts = _next_number(ds.points_transaction, "pts_id")
    for r, lots in _pick(rng, candidates, count, used):
        available = sum(rem for _, rem in lots)
        r["status"], r["reject_reason"] = "APPROVED", None
        for lot, remaining in lots:                        # drain what was really there
            ds.redemption_lot.append({"rdm_id": r["rdm_id"], "lot_pts_id": lot["pts_id"], "points_used": remaining})
        ds.points_transaction.append(_ledger_row(ids.pts_id(next_pts), r["loy_id"], "REDEMPTION",
                                                 -r["points_cost"], r["requested_at"], rdm_id=r["rdm_id"]))
        next_pts += 1
        labels.append(_label(r["rdm_id"], "redemption", "REDEMPTION_EXCEEDS_BALANCE",
                             f"Approved {r['points_cost']} points when only {available} were available",
                             "A redemption is approved only if points_cost <= available balance"))
    return labels


# ---------------------------------------------------------------- 2. return keeps its points

def _return_keeps_points(rng, ds, ledger, count, used):
    owner = {o["ord_id"]: o["loy_id"] for o in ds.orders}
    lines = {l["line_id"]: l for l in ds.order_line}
    candidates = []
    for ret in sorted(ds.line_return, key=lambda r: r["return_id"]):
        reversals = ledger.reversals_of_return[ret["return_id"]]
        if not reversals:
            continue
        lot = ledger.accruals[reversals[0]["reverses_pts_id"]]
        # Lot expires after the last batch (1 Dec), so the kept points never reach an expiry.
        if lot["expires_at"] > ds.expiry_batch[-1]["run_date"]:
            candidates.append((owner[lines[ret["line_id"]]["ord_id"]], (ret, reversals)))
    labels = []
    for ret, reversals in _pick(rng, candidates, count, used):
        drop = {r["pts_id"] for r in reversals}
        ds.points_transaction[:] = [r for r in ds.points_transaction if r["pts_id"] not in drop]
        kept = sum(-r["points"] for r in reversals)
        labels.append(_label(ret["return_id"], "line_return", "RETURN_KEEPS_POINTS",
                             f"Return of {ret['qty_returned']} unit(s) on {ret['line_id']} has no reversal; "
                             f"{kept} points kept",
                             "Every return reverses the points earned on the returned amount"))
    return labels


# ---------------------------------------------------------------- 3. lot expired twice

def _double_expiry(rng, ds, ledger, count, used):
    batches = ds.expiry_batch
    next_batch = {b["exp_id"]: batches[i + 1] for i, b in enumerate(batches[:-1])}
    candidates = [(r["loy_id"], r) for r in ds.points_transaction
                  if r["txn_type"] == "EXPIRY" and r["exp_id"] in next_batch]
    labels = []
    next_pts = _next_number(ds.points_transaction, "pts_id")
    for original in _pick(rng, candidates, count, used):
        batch = next_batch[original["exp_id"]]
        pts_id = ids.pts_id(next_pts)
        next_pts += 1
        ds.points_transaction.append(_ledger_row(pts_id, original["loy_id"], "EXPIRY", original["points"],
                                                 batch["executed_at"], exp_id=batch["exp_id"],
                                                 expires_pts_id=original["expires_pts_id"]))
        labels.append(_label(pts_id, "points_transaction", "DOUBLE_EXPIRY",
                             f"Lot {original['expires_pts_id']} expired again in {batch['exp_id']} "
                             f"(already expired in {original['exp_id']})",
                             "A lot is expired at most once; re-running expiry adds nothing"))
    return labels


# ---------------------------------------------------------------- 4. tier from gross spend

def _tier_uses_gross_spend(rng, ds, count, used):
    owner = {o["ord_id"]: o["loy_id"] for o in ds.orders}
    gross, last_order = defaultdict(Decimal), {}
    for line in ds.order_line:
        gross[owner[line["ord_id"]]] += line["unit_price"] * line["qty"]   # before discounts/returns, incl. gift cards
    for o in ds.orders:
        last_order[o["loy_id"]] = max(last_order.get(o["loy_id"], o["order_ts"].date()), o["order_ts"].date())
    accounts = {a["loy_id"]: a for a in ds.loyalty_account}
    candidates = []
    for loy_id in sorted(gross):
        current = TIER_BY_ID[accounts[loy_id]["tier_id"]]
        if tier_for(gross[loy_id]).rank > current.rank:
            candidates.append((loy_id, loy_id))
    labels = []
    next_tch = _next_number(ds.tier_change, "tier_change_id")
    for loy_id in _pick(rng, candidates, count, used):
        account = accounts[loy_id]
        wrong = tier_for(gross[loy_id])
        tch_id = ids.tch_id(next_tch)
        next_tch += 1
        # Effective after the last order, so no accrual is earned at the wrong rate.
        ds.tier_change.append({"tier_change_id": tch_id, "loy_id": loy_id, "old_tier_id": account["tier_id"],
                               "new_tier_id": ids.tier_id(wrong.code),
                               "effective_date": last_order[loy_id] + timedelta(days=1),
                               "qualifying_spend_at_change": gross[loy_id].quantize(Decimal("0.01"))})
        old = account["tier_id"]
        account["tier_id"] = ids.tier_id(wrong.code)
        labels.append(_label(tch_id, "tier_change", "TIER_USES_GROSS_SPEND",
                             f"{loy_id} moved {old} -> {account['tier_id']} using gross spend {gross[loy_id]:.2f}",
                             "Tier follows qualifying spend (eligible net minus returns), not gross spend"))
    return labels


# ---------------------------------------------------------------- 5. stored balance != ledger

def _balance_mismatch(rng, ds, count, used):
    candidates = [(a["loy_id"], a) for a in ds.loyalty_account]
    labels = []
    for account in _pick(rng, candidates, count, used):
        delta = rng.choice((-1, 1)) * rng.randint(10, 500)
        ledger_sum = account["current_balance"]
        account["current_balance"] = ledger_sum + delta
        labels.append(_label(account["loy_id"], "loyalty_account", "BALANCE_MISMATCH",
                             f"current_balance {account['current_balance']} but ledger sum is {ledger_sum}",
                             "Account balance equals the sum of its ledger entries"))
    return labels


# ---------------------------------------------------------------- driver

def _ledger_row(pts_id, loy_id, txn_type, points, ts, **refs) -> dict:
    row = {"pts_id": pts_id, "loy_id": loy_id, "txn_type": txn_type, "points": points, "txn_ts": ts,
           "line_id": None, "eligible_net": None, "earn_rate": None, "available_from": None,
           "expires_at": None, "return_id": None, "reverses_pts_id": None, "rdm_id": None,
           "exp_id": None, "expires_pts_id": None}
    row.update(refs)
    return row


def _recompute_balances(ds):
    total = defaultdict(int)
    for r in ds.points_transaction:
        total[r["loy_id"]] += r["points"]
    for a in ds.loyalty_account:
        a["current_balance"] = total[a["loy_id"]]


def inject_anomalies(rng, ds, count: int) -> list[dict]:
    used = set()
    ledger = _Ledger(ds)       # built once from the clean baseline
    labels = []
    labels += _redemption_exceeds_balance(rng, ds, ledger, count, used)
    labels += _return_keeps_points(rng, ds, ledger, count, used)
    labels += _double_expiry(rng, ds, ledger, count, used)
    labels += _tier_uses_gross_spend(rng, ds, count, used)
    _recompute_balances(ds)    # every account consistent with the mutated ledger ...
    labels += _balance_mismatch(rng, ds, count, used)   # ... except these
    return labels
