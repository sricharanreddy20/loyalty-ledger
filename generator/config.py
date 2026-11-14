"""Generator settings: seed, scale presets and the distributions used to plan activity.

Both scales use the same code paths and the same distributions; only the counts differ.
"""
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_SEED = 20261022


@dataclass(frozen=True)
class Scale:
    name: str
    customers: int
    orders: int
    anomalies_per_type: int   # labelled records injected per anomaly type


SCALES = {
    "starter": Scale("starter", customers=1_000, orders=5_000, anomalies_per_type=10),
    "full": Scale("full", customers=20_000, orders=100_000, anomalies_per_type=50),
}

# ---------------------------------------------------------------- customers
SIGNUP_BEFORE_2026_SHARE = 0.70     # the rest sign up during 2026 (up to 30 Nov)
ACTIVITY_SIGMA = 1.2                # log-normal spread of how often each customer orders

# ---------------------------------------------------------------- orders
#                Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec   (Oct/Nov festive, Dec/Jan sales)
MONTH_WEIGHTS = (10,  6,  6,  6,  7,  7,  8,  9, 10, 15, 16, 12)
HOURS = tuple(range(9, 22))         # 09:00-21:59
HOUR_WEIGHTS = (3, 5, 6, 6, 5, 5, 6, 7, 8, 9, 9, 7, 4)

LINES_PER_ORDER = (1, 2, 3, 4)
LINES_WEIGHTS = (20, 30, 28, 22)     # mean 2.52, tuned so full scale lands near 300k ledger rows

# (category, weight, median unit price in INR, log-normal sigma)
CATEGORY_MIX = (
    ("ELECTRONICS", 12, 6000, 0.8),
    ("APPAREL",     22, 1200, 0.6),
    ("HOME",        15, 1500, 0.7),
    ("BEAUTY",      14,  600, 0.5),
    ("GROCERY",     17,  400, 0.5),
    ("SPORTS",       7, 1800, 0.6),
    ("BOOKS",        8,  350, 0.4),
    ("GIFT_CARD",    5, None, None),
)
GIFT_CARD_DENOMINATIONS = (500, 1000, 2000, 5000)
MIN_UNIT_PRICE = 49

QUANTITIES = (1, 2, 3, 4, 5)
QUANTITY_WEIGHTS = (70, 18, 7, 3, 2)

DISCOUNT_SHARE = 0.30               # share of lines with a discount
DISCOUNT_PERCENT_RANGE = (5, 30)

ONLINE_SHARE = 0.60
SHIPPING_FEES = (0, 49, 99)         # online orders only
TAX_RATE = Decimal("0.12")          # flat, on the order's net amount; never earns points

# ---------------------------------------------------------------- returns
RETURN_RATE = 0.08                  # share of non-gift-card lines that get returned
RETURN_PATTERN_WEIGHTS = (50, 35, 15)   # full, one partial, two partials (qty >= 2 only)

# ---------------------------------------------------------------- redemptions
REDEMPTIONS_PER_ORDER = 0.20           # planned requests; some are dropped (below)
AFFORDABLE_CHOICE = 0.85            # share of requests that pick a reward the account can afford
GIVE_UP_WHEN_NOTHING_AFFORDABLE = 0.80   # most customers don't try when no reward fits their balance
CONCURRENT_PAIR_RATE = 0.01         # share of customers with one same-timestamp request pair
