"""Pure business-rule functions shared by the generator and the backend.

No database, no I/O, no clock. Money is Decimal (2 dp, ROUND_HALF_UP), points are int.
Floats are rejected so results never depend on binary rounding.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from core.config import EXCLUDED_CATEGORIES, LOT_EXPIRY_DAYS, RETURN_WINDOW_DAYS, TIERS, Tier

CENT = Decimal("0.01")


class InsufficientPoints(Exception):
    """A redemption costs more than the available balance."""

    def __init__(self, available: int, cost: int):
        super().__init__(f"INSUFFICIENT_POINTS: available {available}, cost {cost}")
        self.available = available
        self.cost = cost


@dataclass(frozen=True)
class Lot:
    pts_id: str
    expires_at: date
    remaining: int


def _money(value) -> Decimal:
    if isinstance(value, float):
        raise TypeError("use Decimal or str for money, not float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def eligible_net(unit_price, qty: int, discount, category: str) -> Decimal:
    """Points-eligible amount of one order line (0 for excluded categories)."""
    unit_price, discount = _money(unit_price), _money(discount)
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if unit_price < 0:
        raise ValueError(f"unit_price must not be negative, got {unit_price}")
    gross = unit_price * qty
    if not Decimal(0) <= discount <= gross:
        raise ValueError(f"discount {discount} outside 0..{gross}")
    if category in EXCLUDED_CATEGORIES:
        return Decimal("0.00")
    return _money(gross - discount)


def points_for(eligible, rate) -> int:
    """floor(eligible × rate / 100), exact Decimal arithmetic."""
    if isinstance(rate, float):
        raise TypeError("use Decimal or str for rate, not float")
    eligible, rate = _money(eligible), Decimal(rate)
    if eligible < 0:
        raise ValueError(f"eligible net must not be negative, got {eligible}")
    if rate <= 0:
        raise ValueError(f"rate must be positive, got {rate}")
    return int((eligible * rate / 100).to_integral_value(rounding=ROUND_FLOOR))


def lot_dates(earned_on: date) -> tuple[date, date]:
    """(available_from, expires_at) for a lot earned on this date."""
    return (earned_on + timedelta(days=RETURN_WINDOW_DAYS),
            earned_on + timedelta(days=LOT_EXPIRY_DAYS))


def reversal_points(original_points: int, original_eligible_net, qty: int,
                    returned_qty_total: int, rate, already_reversed: int) -> int:
    """Points to reverse for the latest return of a line (a positive number).

    returned_qty_total counts every unit returned so far, including this return.
    The line's discount is split across units in proportion, so the units kept
    keep their share of it. Uses the ORIGINAL earn rate.
    """
    if not 1 <= returned_qty_total <= qty:
        raise ValueError(f"returned qty {returned_qty_total} outside 1..{qty}")
    remaining_net = _money(_money(original_eligible_net) * (qty - returned_qty_total) / qty)
    still_earned = points_for(remaining_net, rate)
    to_reverse = original_points - still_earned - already_reversed
    if to_reverse < 0:
        raise ValueError("already_reversed is larger than this return allows")
    return to_reverse


def fifo_consume(lots: list[Lot], cost: int) -> list[tuple[str, int]]:
    """Take `cost` points from AVAILABLE lots, earliest expiry first (ties: pts_id).

    Returns [(pts_id, points_used), ...]. Raises InsufficientPoints if short.
    Does not modify the input.
    """
    if cost <= 0:
        raise ValueError(f"cost must be positive, got {cost}")
    available = sum(lot.remaining for lot in lots if lot.remaining > 0)
    if cost > available:
        raise InsufficientPoints(available, cost)
    used = []
    still_needed = cost
    for lot in sorted(lots, key=lambda l: (l.expires_at, l.pts_id)):
        if still_needed == 0:
            break
        if lot.remaining <= 0:
            continue
        take = min(lot.remaining, still_needed)
        used.append((lot.pts_id, take))
        still_needed -= take
    return used


def lots_to_expire(lots: list[Lot], run_date: date) -> list[tuple[str, int]]:
    """Lots due on or before run_date that still hold points: [(pts_id, remaining)].

    An already-expired lot has remaining 0 and is skipped, which makes a re-run add nothing.
    """
    due = [lot for lot in lots if lot.expires_at <= run_date and lot.remaining > 0]
    return [(lot.pts_id, lot.remaining) for lot in sorted(due, key=lambda l: (l.expires_at, l.pts_id))]


def tier_for(qualifying_spend, tiers: tuple[Tier, ...] = TIERS) -> Tier:
    """Highest tier whose threshold is <= qualifying spend."""
    spend = _money(qualifying_spend)
    if spend < 0:
        raise ValueError(f"qualifying spend must not be negative, got {spend}")
    eligible = [t for t in tiers if t.min_qualifying_spend <= spend]
    return max(eligible, key=lambda t: t.rank)
