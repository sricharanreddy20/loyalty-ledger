"""Deterministic, zero-padded ID formatters and the patterns that validate them.

Counters start at 1. A number that does not fit its padding raises ValueError
instead of silently producing a longer ID.
"""
import re

from core.config import TIERS, YEAR


def _pad(n: int, width: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"counter must be an int, got {n!r}")
    if n < 1 or n >= 10 ** width:
        raise ValueError(f"counter {n} out of range 1..{10 ** width - 1}")
    return str(n).zfill(width)


def customer_id(n: int) -> str:
    return f"CUST-{_pad(n, 6)}"


def loyalty_id(cust_id: str) -> str:
    if not PATTERNS["customer"].fullmatch(cust_id):
        raise ValueError(f"not a customer id: {cust_id!r}")
    return f"LOY-{cust_id}"


def order_id(n: int) -> str:
    return f"ORD-{YEAR}-{_pad(n, 6)}"


def line_id(ord_id: str, n: int) -> str:
    if not PATTERNS["order"].fullmatch(ord_id):
        raise ValueError(f"not an order id: {ord_id!r}")
    return f"{ord_id}-{_pad(n, 2)}"


def pts_id(n: int) -> str:
    return f"PTS-{YEAR}-{_pad(n, 8)}"


def rdm_id(n: int) -> str:
    return f"RDM-{YEAR}-{_pad(n, 6)}"


def exp_id(n: int) -> str:
    return f"EXP-{YEAR}-{_pad(n, 6)}"


def ret_id(n: int) -> str:
    return f"RET-{YEAR}-{_pad(n, 6)}"


def tch_id(n: int) -> str:
    return f"TCH-{YEAR}-{_pad(n, 6)}"


def tier_id(code: str) -> str:
    if code not in TIER_CODES:
        raise ValueError(f"unknown tier code: {code!r}")
    return f"TIER-{code}"


def rule_id(n: int) -> str:
    return f"RULE-{_pad(n, 4)}"


def reward_id(n: int) -> str:
    return f"RWD-{_pad(n, 4)}"


TIER_CODES = tuple(t.code for t in TIERS)

PATTERNS = {
    "customer": re.compile(r"CUST-\d{6}"),
    "loyalty": re.compile(r"LOY-CUST-\d{6}"),
    "order": re.compile(rf"ORD-{YEAR}-\d{{6}}"),
    "line": re.compile(rf"ORD-{YEAR}-\d{{6}}-\d{{2}}"),
    "pts": re.compile(rf"PTS-{YEAR}-\d{{8}}"),
    "rdm": re.compile(rf"RDM-{YEAR}-\d{{6}}"),
    "exp": re.compile(rf"EXP-{YEAR}-\d{{6}}"),
    "ret": re.compile(rf"RET-{YEAR}-\d{{6}}"),
    "tch": re.compile(rf"TCH-{YEAR}-\d{{6}}"),
    "tier": re.compile("TIER-(" + "|".join(TIER_CODES) + ")"),
    "rule": re.compile(r"RULE-\d{4}"),
    "reward": re.compile(r"RWD-\d{4}"),
}
