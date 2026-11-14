"""Master data: tiers, rules, rewards, customers and loyalty accounts."""
from datetime import date, timedelta
from decimal import Decimal

from core import ids
from core.config import (
    LOT_EXPIRY_DAYS, RETURN_WINDOW_DAYS, RULES_EFFECTIVE_FROM, RULES_EFFECTIVE_TO, TIERS,
)
from generator.config import SIGNUP_BEFORE_2026_SHARE

REWARDS = (
    ("Coffee voucher", 100),
    ("Movie ticket", 250),
    ("Rs 250 shopping voucher", 500),
    ("Free delivery pass (3 months)", 750),
    ("Rs 500 shopping voucher", 1000),
    ("Bluetooth earphones", 1500),
    ("Rs 1000 shopping voucher", 2000),
    ("Smart watch", 3000),
    ("Weekend hotel stay", 4000),
    ("Rs 2500 shopping voucher", 5000),
)

FIRST_NAMES = (
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Diya", "Farhan", "Gauri", "Harsh", "Ishita",
    "Jaya", "Kabir", "Kavya", "Lakshmi", "Manav", "Meera", "Neha", "Nikhil", "Pooja", "Pranav",
    "Rahul", "Riya", "Rohan", "Sana", "Siddharth", "Sneha", "Tanvi", "Varun", "Vikram", "Zoya",
)
LAST_NAMES = (
    "Agarwal", "Bose", "Chopra", "Das", "Desai", "Gupta", "Iyer", "Joshi", "Kapoor", "Khan",
    "Kumar", "Mehta", "Menon", "Nair", "Patel", "Pillai", "Rao", "Reddy", "Shah", "Sharma",
    "Singh", "Verma",
)
CITIES = (("Mumbai", 16), ("Delhi", 16), ("Bengaluru", 14), ("Hyderabad", 11), ("Chennai", 10),
          ("Kolkata", 9), ("Pune", 8), ("Ahmedabad", 6), ("Jaipur", 4), ("Kochi", 3), ("Lucknow", 3))


def build_tiers() -> list[dict]:
    return [{"tier_id": ids.tier_id(t.code), "code": t.code, "rank": t.rank,
             "min_qualifying_spend": t.min_qualifying_spend, "earn_rate": t.earn_rate}
            for t in TIERS]


def build_rules() -> list[dict]:
    specs = [("EARN_RATE", ids.tier_id(t.code), None, t.earn_rate, "POINTS_PER_100_INR",
              f"{t.code.title()} earns {t.earn_rate} points per 100 INR of eligible net spend")
             for t in TIERS]
    specs += [
        ("CATEGORY_EXCLUSION", None, "GIFT_CARD", None, None, "Gift card lines never earn points"),
        ("CATEGORY_EXCLUSION", None, "SHIPPING", None, None, "Shipping (order header) never earns points"),
        ("CATEGORY_EXCLUSION", None, "TAX", None, None, "Tax (order header) never earns points"),
        ("LOT_EXPIRY", None, None, Decimal(LOT_EXPIRY_DAYS), "DAYS", "A lot expires 180 days after it is earned"),
        ("RETURN_WINDOW", None, None, Decimal(RETURN_WINDOW_DAYS), "DAYS",
         "Returns allowed through day 30; points become available on day 31"),
    ]
    return [{"rule_id": ids.rule_id(n), "rule_type": rtype, "tier_id": tier, "category": category,
             "value": value, "unit": unit, "description": desc,
             "effective_from": RULES_EFFECTIVE_FROM, "effective_to": RULES_EFFECTIVE_TO}
            for n, (rtype, tier, category, value, unit, desc) in enumerate(specs, start=1)]


def build_rewards() -> list[dict]:
    return [{"reward_id": ids.reward_id(n), "name": name, "points_cost": cost, "active": True}
            for n, (name, cost) in enumerate(REWARDS, start=1)]


def _random_date(rng, first: date, last: date) -> date:
    return first + timedelta(days=rng.randint(0, (last - first).days))


def build_customers_and_accounts(rng, count: int) -> tuple[list[dict], list[dict]]:
    city_names = [c for c, _ in CITIES]
    city_weights = [w for _, w in CITIES]
    customers, accounts = [], []
    for n in range(1, count + 1):
        cust_id = ids.customer_id(n)
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        if rng.random() < SIGNUP_BEFORE_2026_SHARE:
            signup = _random_date(rng, date(2024, 1, 1), date(2025, 12, 31))
        else:
            signup = _random_date(rng, date(2026, 1, 1), date(2026, 11, 30))
        customers.append({
            "customer_id": cust_id, "first_name": first, "last_name": last,
            "email": f"{first}.{last}.{n}@example.com".lower(),
            "city": rng.choices(city_names, weights=city_weights)[0],
            "signup_date": signup,
        })
        accounts.append({
            "loy_id": ids.loyalty_id(cust_id), "customer_id": cust_id,
            "tier_id": ids.tier_id("BRONZE"), "opened_at": signup,
            "current_balance": 0,     # set from the ledger at the end of the simulation
        })
    return customers, accounts
