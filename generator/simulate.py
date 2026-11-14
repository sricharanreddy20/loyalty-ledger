"""Apply the business rules to the planned activity, strictly in date order.

Each day, in this order:
  1. expiry batch (1st of the month, 00:05)
  2. tier upgrades effective today
  3. events by (timestamp, kind: order < return < redemption, id)
  4. end of day: write a tier change (effective tomorrow) for accounts whose qualifying spend crossed a threshold
Every amount and point value comes from core/rules.py.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from core import ids
from core.config import AS_OF_DATE, DATASET_START, TIERS, Tier
from core.rules import (
    InsufficientPoints, Lot, eligible_net, fifo_consume, lot_dates, lots_to_expire,
    points_for, remaining_eligible_net, reversal_points, tier_for,
)
from generator.config import AFFORDABLE_CHOICE, GIVE_UP_WHEN_NOTHING_AFFORDABLE

TIER_BY_ID = {ids.tier_id(t.code): t for t in TIERS}
EXPIRY_RUN_TIME = time(0, 5)


@dataclass
class LotState:
    pts_id: str
    loy_id: str
    available_from: object
    expires_at: object
    points: int
    rate: Decimal
    remaining: int
    reversed: int = 0


@dataclass
class LineState:
    row: dict
    eligible: Decimal
    lot: LotState | None
    returned: int = 0


@dataclass
class AccountState:
    loy_id: str
    tier: Tier
    qualifying_spend: Decimal = Decimal("0.00")
    lots: list = field(default_factory=list)
    upgrade_due: tuple | None = None      # (effective_date, Tier)


class Simulation:
    def __init__(self, rng, ds, orders, lines, planned_returns, planned_redemptions):
        self.rng = rng
        self.ds = ds
        self.rewards = sorted(ds.reward, key=lambda r: (r["points_cost"], r["reward_id"]))
        self.accounts = {a["loy_id"]: AccountState(a["loy_id"], TIER_BY_ID[a["tier_id"]]) for a in ds.loyalty_account}
        self.order_owner = {o["ord_id"]: o["loy_id"] for o in orders}
        self.lines_by_order = defaultdict(list)
        for line in lines:
            self.lines_by_order[line["ord_id"]].append(line)
        self.line_state = {}
        self.all_lots = []        # creation order == expires_at order (fixed 180-day expiry)
        self.expiry_cursor = 0
        self.pair_choice = {}     # pair_key -> [reward for 1st request, reward for 2nd]
        self.next_pts = self.next_ret = self.next_tch = self.next_exp = self.next_rdm = 1

        self.events_by_day = defaultdict(list)
        for o in orders:
            self.events_by_day[o["order_ts"].date()].append((o["order_ts"], 0, o["ord_id"], self._order, o))
        for r in planned_returns:
            self.events_by_day[r["return_ts"].date()].append((r["return_ts"], 1, r["line_id"], self._return, r))
        for r in planned_redemptions:
            self.events_by_day[r["requested_at"].date()].append((r["requested_at"], 2, r["request_key"], self._redeem, r))

    # ------------------------------------------------------------ main loop

    def run(self):
        day = DATASET_START
        while day <= AS_OF_DATE:
            if day.day == 1:
                self._expiry_batch(day)
            for acct in self.accounts.values():
                if acct.upgrade_due and acct.upgrade_due[0] == day:
                    acct.tier, acct.upgrade_due = acct.upgrade_due[1], None
            touched = {}
            for ts, _, _, handler, event in sorted(self.events_by_day.get(day, []), key=lambda e: e[:3]):
                touched[handler(event)] = True
            for loy_id in touched:           # dict keeps first-touch order: deterministic
                self._check_upgrade(self.accounts[loy_id], day)
            day += timedelta(days=1)
        self._finish()

    # ------------------------------------------------------------ helpers

    def _ledger(self, loy_id, txn_type, points, ts, **refs) -> str:
        pts_id = ids.pts_id(self.next_pts)
        self.next_pts += 1
        row = {"pts_id": pts_id, "loy_id": loy_id, "txn_type": txn_type, "points": points, "txn_ts": ts,
               "line_id": None, "eligible_net": None, "earn_rate": None, "available_from": None,
               "expires_at": None, "return_id": None, "reverses_pts_id": None, "rdm_id": None,
               "exp_id": None, "expires_pts_id": None}
        row.update(refs)
        self.ds.points_transaction.append(row)
        return pts_id

    @staticmethod
    def _take(lot: LotState, points: int):
        if points > lot.remaining:
            raise AssertionError(f"{lot.pts_id} would go negative: remaining {lot.remaining}, taking {points}")
        lot.remaining -= points

    @staticmethod
    def _available_lots(acct: AccountState, day) -> list[Lot]:
        return [Lot(l.pts_id, l.expires_at, l.remaining) for l in acct.lots
                if l.remaining > 0 and l.available_from <= day < l.expires_at]

    # ------------------------------------------------------------ events

    def _order(self, order) -> str:
        acct = self.accounts[order["loy_id"]]
        earned_on = order["order_ts"].date()
        for line in self.lines_by_order[order["ord_id"]]:
            eligible = eligible_net(line["unit_price"], line["qty"], line["discount"], line["category"])
            acct.qualifying_spend += eligible
            points = points_for(eligible, acct.tier.earn_rate)
            lot = None
            if points > 0:
                available_from, expires_at = lot_dates(earned_on)
                pts_id = self._ledger(acct.loy_id, "ACCRUAL", points, order["order_ts"],
                                      line_id=line["line_id"], eligible_net=eligible,
                                      earn_rate=acct.tier.earn_rate, available_from=available_from,
                                      expires_at=expires_at)
                lot = LotState(pts_id, acct.loy_id, available_from, expires_at, points,
                               acct.tier.earn_rate, remaining=points)
                acct.lots.append(lot)
                if self.all_lots and self.all_lots[-1].expires_at > expires_at:
                    raise AssertionError("lots must be created in expiry order")
                self.all_lots.append(lot)
            self.line_state[line["line_id"]] = LineState(line, eligible, lot)
        return acct.loy_id

    def _return(self, planned) -> str:
        state = self.line_state[planned["line_id"]]
        line = state.row
        acct = self.accounts[self.order_owner[line["ord_id"]]]
        before = state.returned
        state.returned += planned["qty_returned"]
        net = line["unit_price"] * line["qty"] - line["discount"]
        refund = remaining_eligible_net(net, line["qty"], before) - remaining_eligible_net(net, line["qty"], state.returned)
        kept_before = remaining_eligible_net(state.eligible, line["qty"], before)
        kept_after = remaining_eligible_net(state.eligible, line["qty"], state.returned)
        acct.qualifying_spend -= kept_before - kept_after

        return_id = ids.ret_id(self.next_ret)
        self.next_ret += 1
        self.ds.line_return.append({"return_id": return_id, "line_id": line["line_id"],
                                    "return_ts": planned["return_ts"], "qty_returned": planned["qty_returned"],
                                    "refund_amount": refund})
        lot = state.lot
        if lot is not None:
            points = reversal_points(lot.points, state.eligible, line["qty"], state.returned, lot.rate, lot.reversed)
            if points > 0:     # a 0-point reversal writes no entry
                self._take(lot, points)
                lot.reversed += points
                self._ledger(acct.loy_id, "REVERSAL", -points, planned["return_ts"],
                             return_id=return_id, reverses_pts_id=lot.pts_id)
        return acct.loy_id

    def _choose_reward(self, available: int) -> dict:
        affordable = [r for r in self.rewards if r["points_cost"] <= available]
        if affordable and self.rng.random() < AFFORDABLE_CHOICE:
            return self.rng.choice(affordable)
        return self.rng.choice(self.rewards)

    def _choose_pair(self, available: int) -> list[dict]:
        """Two rewards that each fit the balance but together exceed it (when possible)."""
        affordable = [r for r in self.rewards if r["points_cost"] <= available]
        if not affordable:
            return [self.rewards[0], self.rewards[0]]
        first = affordable[-1]
        second_options = [r for r in affordable if r["points_cost"] > available - first["points_cost"]]
        return [first, self.rng.choice(second_options) if second_options else first]

    def _redeem(self, request) -> str:
        acct = self.accounts[request["loy_id"]]
        day = request["requested_at"].date()
        lots = self._available_lots(acct, day)
        available = sum(l.remaining for l in lots)
        if request["pair_key"]:
            if request["pair_key"] not in self.pair_choice:
                self.pair_choice[request["pair_key"]] = self._choose_pair(available)
            reward = self.pair_choice[request["pair_key"]].pop(0)
        else:
            if available < self.rewards[0]["points_cost"] and self.rng.random() < GIVE_UP_WHEN_NOTHING_AFFORDABLE:
                return acct.loy_id      # customer does not try: no request is recorded
            reward = self._choose_reward(available)
        rdm_id = ids.rdm_id(self.next_rdm)     # issued in processing order: (timestamp, request order)
        self.next_rdm += 1
        row = {"rdm_id": rdm_id, "loy_id": acct.loy_id, "reward_id": reward["reward_id"],
               "requested_at": request["requested_at"], "points_cost": reward["points_cost"],
               "status": "APPROVED", "reject_reason": None}
        try:
            used = fifo_consume(lots, reward["points_cost"])
        except InsufficientPoints:
            row.update(status="REJECTED", reject_reason="INSUFFICIENT_POINTS")
            self.ds.redemption.append(row)
            return acct.loy_id
        self.ds.redemption.append(row)
        by_id = {l.pts_id: l for l in acct.lots}
        for pts_id, points in used:
            self._take(by_id[pts_id], points)
            self.ds.redemption_lot.append({"rdm_id": rdm_id, "lot_pts_id": pts_id, "points_used": points})
        self._ledger(acct.loy_id, "REDEMPTION", -reward["points_cost"], request["requested_at"], rdm_id=rdm_id)
        return acct.loy_id

    # ------------------------------------------------------------ batches and tiers

    def _expiry_batch(self, run_date):
        exp_id = ids.exp_id(self.next_exp)
        self.next_exp += 1
        ts = datetime.combine(run_date, EXPIRY_RUN_TIME)
        self.ds.expiry_batch.append({"exp_id": exp_id, "run_date": run_date, "executed_at": ts})
        due = []
        while self.expiry_cursor < len(self.all_lots) and self.all_lots[self.expiry_cursor].expires_at <= run_date:
            due.append(self.all_lots[self.expiry_cursor])
            self.expiry_cursor += 1
        by_id = {l.pts_id: l for l in due}
        for pts_id, points in lots_to_expire([Lot(l.pts_id, l.expires_at, l.remaining) for l in due], run_date):
            lot = by_id[pts_id]
            self._take(lot, points)
            self._ledger(lot.loy_id, "EXPIRY", -points, ts, exp_id=exp_id, expires_pts_id=pts_id)

    def _check_upgrade(self, acct: AccountState, day):
        target = tier_for(acct.qualifying_spend)
        if target.rank <= acct.tier.rank:
            return                      # no downgrades
        effective = day + timedelta(days=1)
        self.ds.tier_change.append({
            "tier_change_id": ids.tch_id(self.next_tch), "loy_id": acct.loy_id,
            "old_tier_id": ids.tier_id(acct.tier.code), "new_tier_id": ids.tier_id(target.code),
            "effective_date": effective, "qualifying_spend_at_change": acct.qualifying_spend,
        })
        self.next_tch += 1
        acct.upgrade_due = (effective, target)

    def _finish(self):
        balance = defaultdict(int)
        for row in self.ds.points_transaction:
            balance[row["loy_id"]] += row["points"]
        for row in self.ds.loyalty_account:
            acct = self.accounts[row["loy_id"]]
            if acct.upgrade_due:        # an upgrade earned on 31 Dec takes effect 1 Jan 2027
                acct.tier, acct.upgrade_due = acct.upgrade_due[1], None
            lots_total = sum(l.remaining for l in acct.lots)
            if balance[row["loy_id"]] != lots_total:
                raise AssertionError(f"{row['loy_id']}: ledger {balance[row['loy_id']]} != lots {lots_total}")
            row["current_balance"] = balance[row["loy_id"]]
            row["tier_id"] = ids.tier_id(acct.tier.code)


def simulate(rng, ds, orders, lines, planned_returns, planned_redemptions):
    Simulation(rng, ds, orders, lines, planned_returns, planned_redemptions).run()
