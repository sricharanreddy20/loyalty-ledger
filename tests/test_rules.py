from datetime import date
from decimal import Decimal as D

import pytest

from core.rules import (
    InsufficientPoints, Lot, eligible_net, fifo_consume, lot_dates, lots_to_expire,
    points_for, remaining_eligible_net, reversal_points, tier_for,
)


# ------------------------------------------------------------ eligible_net / points_for

def test_eligible_net_normal_line():
    assert eligible_net("2500", 4, "1000", "HOME") == D("9000.00")


def test_gift_card_is_never_eligible():
    assert eligible_net("500", 2, "0", "GIFT_CARD") == D("0.00")


def test_full_discount_gives_zero_eligible():
    assert eligible_net("100", 3, "300", "BOOKS") == D("0.00")


@pytest.mark.parametrize("rate, expected", [("1", 90), ("1.25", 112), ("1.5", 135), ("2", 180)])
def test_points_at_each_tier_rate(rate, expected):
    assert points_for(D("9000.00"), D(rate)) == expected


def test_fractional_rate_is_exact():
    assert points_for(D("10000.00"), D("1.25")) == 125


def test_zero_points_below_one_hundred_rupees():
    assert points_for(D("99.99"), D("1")) == 0
    assert points_for(D("100.00"), D("1")) == 1


def test_points_always_floor():
    assert points_for(D("199.99"), D("2")) == 3      # 3.9998 -> 3


@pytest.mark.parametrize("args", [
    ("100", 0, "0", "HOME"),        # zero qty
    ("100", -1, "0", "HOME"),
    ("-1", 1, "0", "HOME"),         # negative price
    ("100", 1, "101", "HOME"),      # discount above line amount
    ("100", 1, "-1", "HOME"),       # negative discount
])
def test_eligible_net_rejects_bad_lines(args):
    with pytest.raises(ValueError):
        eligible_net(*args)


def test_points_for_rejects_bad_input():
    with pytest.raises(ValueError):
        points_for(D("-1"), D("1"))
    with pytest.raises(ValueError):
        points_for(D("100"), D("0"))
    with pytest.raises(TypeError):
        points_for(D("100"), 1.25)       # floats are not allowed


def test_lot_dates_available_the_day_after_the_return_window():
    # Earned 10 Jan: a return is still allowed on 9 Feb (day 30), so points become available on 10 Feb (day 31).
    available_from, expires_at = lot_dates(date(2026, 1, 10))
    assert available_from == date(2026, 2, 10)
    assert (available_from - date(2026, 1, 10)).days == 31
    assert expires_at == date(2026, 7, 9)


def test_remaining_eligible_net_splits_discount_in_proportion():
    assert remaining_eligible_net(D("9000.00"), 4, 0) == D("9000.00")
    assert remaining_eligible_net(D("9000.00"), 4, 1) == D("6750.00")
    assert remaining_eligible_net(D("100.00"), 3, 1) == D("66.67")     # rounded half-up
    assert remaining_eligible_net(D("9000.00"), 4, 4) == D("0.00")
    with pytest.raises(ValueError):
        remaining_eligible_net(D("9000.00"), 4, 5)


# ------------------------------------------------------------ reversal_points

# 4 units at 2500 with 1000 off -> 9000 eligible -> 90 points at rate 1.
LINE = dict(original_points=90, original_eligible_net=D("9000.00"), qty=4, rate=D("1"))


def test_full_return_reverses_everything():
    assert reversal_points(**LINE, returned_qty_total=4, already_reversed=0) == 90


def test_two_partial_returns_then_the_rest():
    # Return 1 unit: 3 units keep 6750 (discount split in proportion) -> 67 points kept.
    first = reversal_points(**LINE, returned_qty_total=1, already_reversed=0)
    assert first == 23
    # Return 2 more (3 in total): 1 unit keeps 2250 -> 22 points kept.
    second = reversal_points(**LINE, returned_qty_total=3, already_reversed=first)
    assert second == 45
    # The two partial returns equal a single return of the same 3 units.
    assert first + second == reversal_points(**LINE, returned_qty_total=3, already_reversed=0)
    # Returning the last unit reverses exactly what is left.
    last = reversal_points(**LINE, returned_qty_total=4, already_reversed=first + second)
    assert last == 22
    assert first + second + last == 90


def test_reversal_uses_original_rate():
    gold = dict(LINE, original_points=135, rate=D("1.5"))
    assert reversal_points(**gold, returned_qty_total=1, already_reversed=0) == 135 - 101


def _return_sequences(qty):
    """Every ordered sequence of partial returns (each >= 1 unit) totalling <= qty."""
    if qty == 0:
        yield []
        return
    yield []
    for first in range(1, qty + 1):
        for rest in _return_sequences(qty - first):
            yield [first] + rest


@pytest.mark.parametrize("net", ["9000.00", "1234.57", "99.99", "333.33", "0.00"])
@pytest.mark.parametrize("rate", ["1", "1.25", "1.5", "2"])
def test_total_reversed_never_exceeds_original(net, rate):
    for qty in range(1, 7):
        original = points_for(D(net), D(rate))
        for sequence in _return_sequences(qty):
            reversed_total, returned = 0, 0
            for units in sequence:
                returned += units
                step = reversal_points(original, D(net), qty, returned, D(rate), reversed_total)
                assert step >= 0
                reversed_total += step
                assert reversed_total <= original, (net, rate, qty, sequence)
            if returned == qty:
                assert reversed_total == original      # a full return reverses everything


def test_reversal_rejects_bad_quantities():
    with pytest.raises(ValueError):
        reversal_points(**LINE, returned_qty_total=5, already_reversed=0)   # more than bought
    with pytest.raises(ValueError):
        reversal_points(**LINE, returned_qty_total=0, already_reversed=0)
    with pytest.raises(ValueError):
        reversal_points(**LINE, returned_qty_total=1, already_reversed=50)  # over-reversed


# ------------------------------------------------------------ fifo_consume

LOTS = [
    Lot("PTS-2026-00000001", date(2026, 3, 1), 10),
    Lot("PTS-2026-00000002", date(2026, 2, 1), 5),
    Lot("PTS-2026-00000003", date(2026, 3, 1), 20),   # same expiry as 001 -> after it
]


def test_fifo_oldest_expiry_first_with_partial_lot():
    assert fifo_consume(LOTS, 20) == [
        ("PTS-2026-00000002", 5),
        ("PTS-2026-00000001", 10),
        ("PTS-2026-00000003", 5),
    ]


def test_fifo_tie_broken_by_pts_id_regardless_of_input_order():
    assert fifo_consume(list(reversed(LOTS)), 20) == fifo_consume(LOTS, 20)


def test_cost_equal_to_balance_uses_every_lot():
    assert sum(used for _, used in fifo_consume(LOTS, 35)) == 35


def test_redemption_exceeding_balance_is_rejected():
    with pytest.raises(InsufficientPoints) as err:
        fifo_consume(LOTS, 36)
    assert (err.value.available, err.value.cost) == (35, 36)


def test_fifo_skips_empty_lots_and_does_not_modify_input():
    lots = LOTS + [Lot("PTS-2026-00000000", date(2026, 1, 1), 0)]
    before = list(lots)
    assert fifo_consume(lots, 5) == [("PTS-2026-00000002", 5)]
    assert lots == before


def test_fifo_rejects_non_positive_cost():
    with pytest.raises(ValueError):
        fifo_consume(LOTS, 0)


# ------------------------------------------------------------ lots_to_expire

def test_expiry_on_exact_date_and_one_day_later():
    lot = Lot("PTS-2026-00000001", date(2026, 7, 1), 40)
    assert lots_to_expire([lot], date(2026, 7, 1)) == [("PTS-2026-00000001", 40)]
    assert lots_to_expire([lot], date(2026, 6, 30)) == []


def test_already_expired_lot_is_skipped_so_rerun_adds_nothing():
    lots = [Lot("PTS-2026-00000001", date(2026, 6, 15), 40),
            Lot("PTS-2026-00000002", date(2026, 6, 20), 0)]     # fully redeemed or already expired
    first_run = lots_to_expire(lots, date(2026, 7, 1))
    assert first_run == [("PTS-2026-00000001", 40)]
    # After the batch, the lot's remaining is 0; running the same date again expires nothing.
    after = [Lot(l.pts_id, l.expires_at, 0) for l in lots]
    assert lots_to_expire(after, date(2026, 7, 1)) == []


# ------------------------------------------------------------ tier_for

@pytest.mark.parametrize("spend, code", [
    ("0", "BRONZE"),
    ("24999.99", "BRONZE"),
    ("25000.00", "SILVER"),       # exact threshold
    ("74999.99", "SILVER"),
    ("75000.00", "GOLD"),
    ("199999.99", "GOLD"),
    ("200000.00", "PLATINUM"),
    ("1000000", "PLATINUM"),
])
def test_tier_thresholds(spend, code):
    assert tier_for(D(spend)).code == code


def test_tier_rejects_negative_spend():
    with pytest.raises(ValueError):
        tier_for(D("-0.01"))
