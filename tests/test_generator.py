"""Invariants of the generated starter dataset. Most checks recompute from raw rows
rather than trusting the generator's internal state."""
from collections import Counter, defaultdict
from datetime import date, timedelta

import pytest

from core import ids
from core.config import AS_OF_DATE, RETURN_WINDOW_DAYS, TIERS
from core.rules import eligible_net, remaining_eligible_net, tier_for
from generator.config import DEFAULT_SEED
from generator.generate import generate

TIER_BY_ID = {ids.tier_id(t.code): t for t in TIERS}


@pytest.fixture(scope="module")
def ds():
    return generate(DEFAULT_SEED, "starter")


# ------------------------------------------------------------ determinism

def test_same_seed_same_dataset(ds):
    assert generate(DEFAULT_SEED, "starter").fingerprint() == ds.fingerprint()


def test_different_seed_different_dataset(ds):
    assert generate(DEFAULT_SEED + 1, "starter").fingerprint() != ds.fingerprint()


# ------------------------------------------------------------ structure

PRIMARY_KEYS = {
    "tier": ("tier_id", "tier"), "rule": ("rule_id", "rule"), "reward": ("reward_id", "reward"),
    "customer": ("customer_id", "customer"), "loyalty_account": ("loy_id", "loyalty"),
    "tier_change": ("tier_change_id", "tch"), "expiry_batch": ("exp_id", "exp"),
    "orders": ("ord_id", "order"), "order_line": ("line_id", "line"), "line_return": ("return_id", "ret"),
    "redemption": ("rdm_id", "rdm"), "points_transaction": ("pts_id", "pts"),
}


@pytest.mark.parametrize("table", sorted(PRIMARY_KEYS))
def test_ids_unique_and_well_formed(ds, table):
    column, pattern = PRIMARY_KEYS[table]
    values = [row[column] for row in ds.tables()[table]]
    assert len(values) == len(set(values))
    assert all(ids.PATTERNS[pattern].fullmatch(v) for v in values)


def test_sequential_ids_have_no_gaps(ds):
    for table, (column, _) in PRIMARY_KEYS.items():
        if table in ("tier", "rule", "reward", "order_line"):
            continue
        numbers = [int(row[column].rsplit("-", 1)[1]) for row in ds.tables()[table]]
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), table


def test_foreign_keys_resolve(ds):
    keys = {t: {row[c] for row in ds.tables()[t]} for t, (c, _) in PRIMARY_KEYS.items()}
    assert {a["customer_id"] for a in ds.loyalty_account} <= keys["customer"]
    assert {a["tier_id"] for a in ds.loyalty_account} <= keys["tier"]
    assert {o["loy_id"] for o in ds.orders} <= keys["loyalty_account"]
    assert {l["ord_id"] for l in ds.order_line} <= keys["orders"]
    assert {r["line_id"] for r in ds.line_return} <= keys["order_line"]
    assert {r["loy_id"] for r in ds.redemption} <= keys["loyalty_account"]
    assert {r["reward_id"] for r in ds.redemption} <= keys["reward"]
    assert {r["rdm_id"] for r in ds.redemption_lot} <= keys["redemption"]
    assert {r["lot_pts_id"] for r in ds.redemption_lot} <= keys["points_transaction"]
    assert {(t["old_tier_id"], t["new_tier_id"]) for t in ds.tier_change} <= {
        (a, b) for a in keys["tier"] for b in keys["tier"]}
    for row in ds.points_transaction:
        assert row["loy_id"] in keys["loyalty_account"]
        for column, table in (("line_id", "order_line"), ("return_id", "line_return"), ("rdm_id", "redemption"),
                              ("exp_id", "expiry_batch"), ("reverses_pts_id", "points_transaction"),
                              ("expires_pts_id", "points_transaction")):
            assert row[column] is None or row[column] in keys[table]


def test_dates_inside_2026_and_after_account_opened(ds):
    opened = {a["loy_id"]: a["opened_at"] for a in ds.loyalty_account}
    for o in ds.orders:
        assert date(2026, 1, 1) <= o["order_ts"].date() <= AS_OF_DATE
        assert o["order_ts"].date() >= opened[o["loy_id"]]
    for row in ds.points_transaction:
        assert date(2026, 1, 1) <= row["txn_ts"].date() <= AS_OF_DATE
    for r in ds.redemption:
        assert r["requested_at"].date() <= AS_OF_DATE


def test_ledger_written_in_date_order(ds):
    timestamps = [row["txn_ts"] for row in ds.points_transaction]     # pts_id order == processing order
    assert timestamps == sorted(timestamps)


# ------------------------------------------------------------ balances and lots

def test_balance_equals_ledger_sum(ds):
    total = defaultdict(int)
    for row in ds.points_transaction:
        total[row["loy_id"]] += row["points"]
    for account in ds.loyalty_account:
        assert account["current_balance"] == total[account["loy_id"]]


def test_lot_never_negative_at_any_point_and_redemptions_were_affordable(ds):
    """Replay the ledger in processing order, tracking every lot after every single entry."""
    used_by_rdm = defaultdict(list)
    for row in ds.redemption_lot:
        used_by_rdm[row["rdm_id"]].append(row)
    lots, lots_of = {}, defaultdict(list)
    for row in ds.points_transaction:
        day = row["txn_ts"].date()
        if row["txn_type"] == "ACCRUAL":
            lot = {"remaining": row["points"], "available_from": row["available_from"],
                   "expires_at": row["expires_at"], "loy_id": row["loy_id"]}
            lots[row["pts_id"]] = lot
            lots_of[row["loy_id"]].append(lot)
        elif row["txn_type"] == "REVERSAL":
            lot = lots[row["reverses_pts_id"]]
            assert day < lot["available_from"], "reversal after the lot became available"
            lot["remaining"] += row["points"]
            assert lot["remaining"] >= 0, row["pts_id"]
        elif row["txn_type"] == "EXPIRY":
            lot = lots[row["expires_pts_id"]]
            assert day >= lot["expires_at"], "expired early"
            lot["remaining"] += row["points"]
            assert lot["remaining"] >= 0, row["pts_id"]
        else:   # REDEMPTION
            available = sum(l["remaining"] for l in lots_of[row["loy_id"]]
                            if l["available_from"] <= day < l["expires_at"])
            assert -row["points"] <= available, f"{row['rdm_id']} exceeded available balance"
            used = used_by_rdm[row["rdm_id"]]
            assert sum(u["points_used"] for u in used) == -row["points"]
            for u in used:
                lot = lots[u["lot_pts_id"]]
                assert lot["loy_id"] == row["loy_id"]
                assert lot["available_from"] <= day < lot["expires_at"], "consumed a lot that was not available"
                lot["remaining"] -= u["points_used"]
                assert lot["remaining"] >= 0, u["lot_pts_id"]


def test_redemption_ledger_matches_status(ds):
    in_ledger = {r["rdm_id"] for r in ds.points_transaction if r["txn_type"] == "REDEMPTION"}
    for r in ds.redemption:
        if r["status"] == "APPROVED":
            assert r["rdm_id"] in in_ledger and r["reject_reason"] is None
        else:
            assert r["rdm_id"] not in in_ledger and r["reject_reason"] == "INSUFFICIENT_POINTS"


# ------------------------------------------------------------ returns and reversals

def test_returns_within_window_and_quantity(ds):
    lines = {l["line_id"]: l for l in ds.order_line}
    order_day = {o["ord_id"]: o["order_ts"].date() for o in ds.orders}
    returned = Counter()
    for r in ds.line_return:
        line = lines[r["line_id"]]
        days = (r["return_ts"].date() - order_day[line["ord_id"]]).days
        assert 1 <= days <= RETURN_WINDOW_DAYS
        assert line["category"] != "GIFT_CARD"
        returned[r["line_id"]] += r["qty_returned"]
    assert all(returned[lid] <= lines[lid]["qty"] for lid in returned)


def test_reversals_reference_the_accrual_of_the_returned_line(ds):
    by_pts = {r["pts_id"]: r for r in ds.points_transaction}
    return_line = {r["return_id"]: r["line_id"] for r in ds.line_return}
    reversed_total = Counter()
    for row in ds.points_transaction:
        if row["txn_type"] == "REVERSAL":
            accrual = by_pts[row["reverses_pts_id"]]
            assert accrual["txn_type"] == "ACCRUAL"
            assert accrual["line_id"] == return_line[row["return_id"]]
            assert accrual["loy_id"] == row["loy_id"]
            reversed_total[accrual["pts_id"]] += -row["points"]
    assert all(reversed_total[p] <= by_pts[p]["points"] for p in reversed_total)


def test_fully_returned_lines_lose_all_points(ds):
    lines = {l["line_id"]: l for l in ds.order_line}
    returned = Counter()
    for r in ds.line_return:
        returned[r["line_id"]] += r["qty_returned"]
    accrual = {r["line_id"]: r for r in ds.points_transaction if r["txn_type"] == "ACCRUAL"}
    reversed_total = Counter()
    for r in ds.points_transaction:
        if r["txn_type"] == "REVERSAL":
            reversed_total[r["reverses_pts_id"]] += -r["points"]
    for line_id, qty in returned.items():
        if qty == lines[line_id]["qty"] and line_id in accrual:
            assert reversed_total[accrual[line_id]["pts_id"]] == accrual[line_id]["points"]


# ------------------------------------------------------------ expiry

def test_expiry_batches_monthly_and_no_lot_expired_twice(ds):
    assert [b["run_date"] for b in ds.expiry_batch] == [date(2026, m, 1) for m in range(1, 13)]
    expired = Counter(r["expires_pts_id"] for r in ds.points_transaction if r["txn_type"] == "EXPIRY")
    assert expired and max(expired.values()) == 1


def test_every_due_lot_with_points_was_expired(ds):
    """After the December batch, no lot due by 1 Dec still holds points."""
    remaining = {r["pts_id"]: r["points"] for r in ds.points_transaction if r["txn_type"] == "ACCRUAL"}
    expires = {r["pts_id"]: r["expires_at"] for r in ds.points_transaction if r["txn_type"] == "ACCRUAL"}
    for r in ds.points_transaction:
        if r["txn_type"] in ("REVERSAL", "EXPIRY"):
            remaining[r["reverses_pts_id"] or r["expires_pts_id"]] += r["points"]
    for r in ds.redemption_lot:
        remaining[r["lot_pts_id"]] -= r["points_used"]
    assert all(remaining[p] == 0 for p in remaining if expires[p] <= date(2026, 12, 1))


# ------------------------------------------------------------ tiers

def _qualifying_spend_events(ds):
    """Per account: [(timestamp, kind, change in qualifying spend)], recomputed from orders and returns.
    kind 0 = order, 1 = return, so an order sorts before a return at the same timestamp."""
    lines = {l["line_id"]: l for l in ds.order_line}
    order = {o["ord_id"]: o for o in ds.orders}
    events = defaultdict(list)
    for l in ds.order_line:
        o = order[l["ord_id"]]
        events[o["loy_id"]].append((o["order_ts"], 0, eligible_net(l["unit_price"], l["qty"], l["discount"], l["category"])))
    returned = Counter()
    for r in sorted(ds.line_return, key=lambda r: (r["return_ts"], r["return_id"])):
        l = lines[r["line_id"]]
        o = order[l["ord_id"]]
        eligible = eligible_net(l["unit_price"], l["qty"], l["discount"], l["category"])
        before = remaining_eligible_net(eligible, l["qty"], returned[r["line_id"]])
        returned[r["line_id"]] += r["qty_returned"]
        after = remaining_eligible_net(eligible, l["qty"], returned[r["line_id"]])
        events[o["loy_id"]].append((r["return_ts"], 1, -(before - after)))
    return events


def test_final_tier_follows_peak_qualifying_spend(ds):
    """Tiers are checked right after each order, so the peak is taken event by event, not per day."""
    events = _qualifying_spend_events(ds)
    for account in ds.loyalty_account:
        running, peak = 0, 0
        for _, _, delta in sorted(events[account["loy_id"]], key=lambda e: e[:2]):
            running += delta
            peak = max(peak, running)
        assert account["tier_id"] == ids.tier_id(tier_for(peak).code), account["loy_id"]


def test_tier_changes_only_upgrade_and_match_thresholds(ds):
    for t in ds.tier_change:
        old, new = TIER_BY_ID[t["old_tier_id"]], TIER_BY_ID[t["new_tier_id"]]
        assert new.rank > old.rank
        assert tier_for(t["qualifying_spend_at_change"]) == new


def test_accrual_rate_is_tier_in_effect_on_order_date(ds):
    changes = defaultdict(list)
    for t in sorted(ds.tier_change, key=lambda t: t["effective_date"]):
        changes[t["loy_id"]].append(t)
    order_day = {o["ord_id"]: o["order_ts"].date() for o in ds.orders}
    line_order = {l["line_id"]: l["ord_id"] for l in ds.order_line}
    for row in ds.points_transaction:
        if row["txn_type"] != "ACCRUAL":
            continue
        day = order_day[line_order[row["line_id"]]]
        tier = "TIER-BRONZE"
        for t in changes[row["loy_id"]]:
            if t["effective_date"] <= day:
                tier = t["new_tier_id"]
        assert row["earn_rate"] == TIER_BY_ID[tier].earn_rate, row["pts_id"]


def test_upgrade_applies_from_the_next_day(ds):
    for t in ds.tier_change:
        assert t["effective_date"] - timedelta(days=1) <= AS_OF_DATE


# ------------------------------------------------------------ coverage of required scenarios

def test_required_scenarios_are_present(ds):
    lines = {l["line_id"]: l for l in ds.order_line}
    returns_per_line = Counter(r["line_id"] for r in ds.line_return)
    returned_qty = Counter()
    for r in ds.line_return:
        returned_qty[r["line_id"]] += r["qty_returned"]
    status = Counter(r["status"] for r in ds.redemption)
    pairs = defaultdict(list)
    for r in ds.redemption:
        pairs[(r["loy_id"], r["requested_at"])].append(r)
    pair_outcomes = {tuple(x["status"] for x in sorted(p, key=lambda x: x["rdm_id"]))
                     for p in pairs.values() if len(p) == 2}
    tiers = Counter(a["tier_id"] for a in ds.loyalty_account)
    ledger = Counter(r["txn_type"] for r in ds.points_transaction)

    assert any(returned_qty[l] == lines[l]["qty"] for l in returned_qty), "full return"
    assert any(returned_qty[l] < lines[l]["qty"] for l in returned_qty), "partial return"
    assert any(n == 2 for n in returns_per_line.values()), "two partial returns on one line"
    assert status["APPROVED"] and status["REJECTED"]
    assert ("APPROVED", "REJECTED") in pair_outcomes, "concurrent pair: first approved, second rejected"
    assert ledger["EXPIRY"] and ledger["REVERSAL"] and ledger["REDEMPTION"]
    assert tiers["TIER-GOLD"] and tiers["TIER-PLATINUM"]
    assert any(l["category"] == "GIFT_CARD" for l in ds.order_line)
    assert any(l["discount"] > 0 for l in ds.order_line)
