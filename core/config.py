"""Business configuration shared by the generator and the backend.

Values here seed the tier and rule tables. Money is Decimal, points are int.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Dataset covers calendar year 2026. Used instead of "today" so results are deterministic.
YEAR = 2026
DATASET_START = date(2026, 1, 1)
AS_OF_DATE = date(2026, 12, 31)

CURRENCY = "INR"


@dataclass(frozen=True)
class Tier:
    code: str               # BRONZE, SILVER, GOLD, PLATINUM
    rank: int               # 1 = lowest
    min_qualifying_spend: Decimal
    earn_rate: Decimal      # points per 100 INR of eligible net spend


TIERS = (
    Tier("BRONZE", 1, Decimal("0"), Decimal("1")),
    Tier("SILVER", 2, Decimal("25000"), Decimal("1.25")),
    Tier("GOLD", 3, Decimal("75000"), Decimal("1.5")),
    Tier("PLATINUM", 4, Decimal("200000"), Decimal("2")),
)

# Product categories. Gift cards never earn points; shipping and tax are on the
# order header and are never eligible.
CATEGORIES = ("ELECTRONICS", "APPAREL", "HOME", "BEAUTY", "GROCERY", "SPORTS", "BOOKS", "GIFT_CARD")
EXCLUDED_CATEGORIES = ("GIFT_CARD",)

LOT_EXPIRY_DAYS = 180
RETURN_WINDOW_DAYS = 30   # a return is allowed up to and including day 30 after the order
AVAILABLE_AFTER_DAYS = RETURN_WINDOW_DAYS + 1   # lot becomes available on day 31, after the window closes

# All rules are effective for the whole of 2026 (effective_to = None means open-ended).
RULES_EFFECTIVE_FROM = DATASET_START
RULES_EFFECTIVE_TO = None
