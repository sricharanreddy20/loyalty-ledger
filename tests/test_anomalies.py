"""Every label must be a real violation, and each rule check over ALL records must find
exactly the labelled set: no unlabelled record was corrupted."""
from collections import Counter, defaultdict

import pytest

from core import ids
from core.config import TIERS
from core.rules import eligible_net, points_for, remaining_eligible_net, tier_for
from generator.config import DEFAULT_SEED, SCALES
from generator.generate import build

TIER_BY_ID = {ids.tier_id(t.code): t for t in TIERS}
TYPES = ("BALANCE_MISMATCH", "REDEMPTION_EXCEEDS_BALANCE", "RETURN_KEEPS_POINTS", "DOUBLE_EXPIRY",
         "TIER_USES_GROSS_SPEND")


@pytest.fixture(scope="module")
def built():
    return build(DEFAULT_SEED, "starter")


def labelled(ground_truth, issue_type) -> set:
    return {g["record_id"] for g in ground_truth if g["issue_type"] == issue_type}


# ------------------------------------------------------------ independent rule checks

def balance_mismatches(ds) -> set:
    total = defaultdict(int)
    for r in ds.points_transaction:
        total[r["loy_id"]] += r["points"]
    return {a["loy_id"] for a in ds.loyalty_account if a["current_balance"] != total[a["loy_id"]]}


def redemptions_over_balance(ds) -> set:
    """Approved redemptions whose cost exceeded the available balance when processed."""
    lots_of = defaultdict(list)
    reversed_ = defaultdict(int)
    for r in ds.points_transaction:
        if r["txn_type"] == "ACCRUAL":
            lots_of[r["loy_id"]].append(r)
        elif r["txn_type"] == "REVERSAL":
            reversed_[r["reverses_pts_id"]] += -r["points"]
    used = defaultdict(list)
    for r in ds.redemption_lot:
        used[r["lot_pts_id"]].append((r["rdm_id"], r["points_used"]))
    found = set()
    for d in ds.redemption:
        if d["status"] != "APPROVED":
            continue
        day = d["requested_at"].date()
        available = sum(
            lot["points"] - reversed_[lot["pts_id"]] - sum(u for rdm, u in used[lot["pts_id"]] if rdm < d["rdm_id"])
            for lot in lots_of[d["loy_id"]] if lot["available_from"] <= day < lot["expires_at"])
        if d["points_cost"] > available:
            found.add(d["rdm_id"])
    return found


def returns_without_reversal(ds) -> set:
    """Returns that should reverse > 0 points (cumulative rule) but have no REVERSAL entry."""
    lines = {l["line_id"]: l for l in ds.order_line}
    accrual = {r["line_id"]: r for r in ds.points_transaction if r["txn_type"] == "ACCRUAL"}
    has_reversal = {r["return_id"] for r in ds.points_transaction if r["txn_type"] == "REVERSAL"}
    returned = Counter()
    found = set()
    for ret in sorted(ds.line_return, key=lambda r: r["return_id"]):
        line = lines[ret["line_id"]]
        before = returned[ret["line_id"]]
        returned[ret["line_id"]] += ret["qty_returned"]
        lot = accrual.get(ret["line_id"])
        if lot is None:
            continue
        eligible = eligible_net(line["unit_price"], line["qty"], line["discount"], line["category"])
        kept_before = points_for(remaining_eligible_net(eligible, line["qty"], before), lot["earn_rate"])
        kept_after = points_for(remaining_eligible_net(eligible, line["qty"], returned[ret["line_id"]]), lot["earn_rate"])
        if kept_before - kept_after > 0 and ret["return_id"] not in has_reversal:
            found.add(ret["return_id"])
    return found


def repeated_expiries(ds) -> set:
    """Every EXPIRY entry after the first one for the same lot."""
    seen, found = set(), set()
    for r in sorted((r for r in ds.points_transaction if r["txn_type"] == "EXPIRY"),
                    key=lambda r: (r["txn_ts"], r["pts_id"])):
        if r["expires_pts_id"] in seen:
            found.add(r["pts_id"])
        seen.add(r["expires_pts_id"])
    return found


def tier_changes_not_earned(ds) -> set:
    """Tier changes whose new tier was never reached by qualifying spend before the effective date."""
    lines = {l["line_id"]: l for l in ds.order_line}
    order = {o["ord_id"]: o for o in ds.orders}
    events = defaultdict(list)            # loy_id -> [(day, change)]
    for l in ds.order_line:
        o = order[l["ord_id"]]
        events[o["loy_id"]].append((o["order_ts"].date(), eligible_net(l["unit_price"], l["qty"], l["discount"], l["category"])))
    returned = Counter()
    for r in sorted(ds.line_return, key=lambda r: r["return_id"]):
        l = lines[r["line_id"]]
        e = eligible_net(l["unit_price"], l["qty"], l["discount"], l["category"])
        before = remaining_eligible_net(e, l["qty"], returned[r["line_id"]])
        returned[r["line_id"]] += r["qty_returned"]
        events[order[l["ord_id"]]["loy_id"]].append(
            (r["return_ts"].date(), -(before - remaining_eligible_net(e, l["qty"], returned[r["line_id"]]))))
    found = set()
    for t in ds.tier_change:
        by_day = defaultdict(int)
        for day, change in events[t["loy_id"]]:
            if day < t["effective_date"]:
                by_day[day] += change
        running = peak = 0
        for day in sorted(by_day):
            running += by_day[day]
            peak = max(peak, running)
        if tier_for(peak).rank < TIER_BY_ID[t["new_tier_id"]].rank:
            found.add(t["tier_change_id"])
    return found


CHECKS = {
    "BALANCE_MISMATCH": balance_mismatches,
    "REDEMPTION_EXCEEDS_BALANCE": redemptions_over_balance,
    "RETURN_KEEPS_POINTS": returns_without_reversal,
    "DOUBLE_EXPIRY": repeated_expiries,
    "TIER_USES_GROSS_SPEND": tier_changes_not_earned,
}


# ------------------------------------------------------------ tests

@pytest.mark.parametrize("issue_type", TYPES)
def test_check_finds_exactly_the_labelled_records(built, issue_type):
    ds, ground_truth = built
    assert CHECKS[issue_type](ds) == labelled(ground_truth, issue_type)


@pytest.mark.parametrize("issue_type", TYPES)
def test_clean_baseline_has_no_violations(issue_type):
    from generator.generate import generate
    assert CHECKS[issue_type](generate(DEFAULT_SEED, "starter")) == set()


def test_counts_per_type(built):
    _, ground_truth = built
    counts = Counter(g["issue_type"] for g in ground_truth)
    assert counts == {t: SCALES["starter"].anomalies_per_type for t in TYPES}


def test_each_anomaly_on_a_different_account(built):
    ds, ground_truth = built
    owner = {}
    owner.update({a["loy_id"]: a["loy_id"] for a in ds.loyalty_account})
    owner.update({r["rdm_id"]: r["loy_id"] for r in ds.redemption})
    owner.update({r["pts_id"]: r["loy_id"] for r in ds.points_transaction})
    owner.update({t["tier_change_id"]: t["loy_id"] for t in ds.tier_change})
    order_owner = {o["ord_id"]: o["loy_id"] for o in ds.orders}
    line_owner = {l["line_id"]: order_owner[l["ord_id"]] for l in ds.order_line}
    owner.update({r["return_id"]: line_owner[r["line_id"]] for r in ds.line_return})
    accounts = [owner[g["record_id"]] for g in ground_truth]
    assert len(accounts) == len(set(accounts))


def test_ground_truth_rows_are_complete(built):
    _, ground_truth = built
    for g in ground_truth:
        assert set(g) == {"record_id", "entity", "issue_type", "description", "rule_violated"}
        assert all(g.values())


def test_tier_anomaly_does_not_change_any_earn_rate(built):
    """The wrong tier takes effect after the account's last order, so no accrual uses it."""
    ds, ground_truth = built
    wrong = {t["loy_id"]: t["effective_date"] for t in ds.tier_change
             if t["tier_change_id"] in labelled(ground_truth, "TIER_USES_GROSS_SPEND")}
    for o in ds.orders:
        if o["loy_id"] in wrong:
            assert o["order_ts"].date() < wrong[o["loy_id"]]
