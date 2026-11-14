"""Random choices about WHAT happens and WHEN: orders, lines, planned returns, planned
redemption requests. No business rules are applied here; simulate.py does that in date order.
"""
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from core import ids
from core.config import AS_OF_DATE, DATASET_START, RETURN_WINDOW_DAYS
from generator import config as cfg


def _random_time(rng) -> time:
    hour = rng.choices(cfg.HOURS, weights=cfg.HOUR_WEIGHTS)[0]
    return time(hour, rng.randint(0, 59), rng.randint(0, 59))


def _month_end(month: int) -> date:
    return (date(2026, 12, 31) if month == 12 else date(2026, month + 1, 1) - timedelta(days=1))


def _random_order_ts(rng, opened_at: date) -> datetime:
    """A 2026 timestamp on or after the account opened, weighted toward festive months."""
    start = max(opened_at, DATASET_START)
    months = list(range(start.month, 13))
    month = rng.choices(months, weights=[cfg.MONTH_WEIGHTS[m - 1] for m in months])[0]
    first = start if month == start.month else date(2026, month, 1)
    day = first + timedelta(days=rng.randint(0, (_month_end(month) - first).days))
    return datetime.combine(day, _random_time(rng))


def _unit_price(rng, category: str, median, sigma) -> Decimal:
    if category == "GIFT_CARD":
        return Decimal(rng.choice(cfg.GIFT_CARD_DENOMINATIONS))
    return Decimal(max(cfg.MIN_UNIT_PRICE, round(median * rng.lognormvariate(0, sigma))))


def _line(rng, ord_id: str, line_no: int) -> dict:
    category, _, median, sigma = rng.choices(cfg.CATEGORY_MIX, weights=[c[1] for c in cfg.CATEGORY_MIX])[0]
    unit_price = _unit_price(rng, category, median, sigma)
    qty = rng.choices(cfg.QUANTITIES, weights=cfg.QUANTITY_WEIGHTS)[0]
    discount = Decimal(0)
    if category != "GIFT_CARD" and rng.random() < cfg.DISCOUNT_SHARE:
        percent = rng.randint(*cfg.DISCOUNT_PERCENT_RANGE)
        discount = (unit_price * qty * percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return {
        "line_id": ids.line_id(ord_id, line_no), "ord_id": ord_id, "line_no": line_no,
        "sku": f"SKU-{category[:3]}-{rng.randint(1, 500):04d}", "category": category,
        "unit_price": unit_price.quantize(Decimal("0.01")), "qty": qty,
        "discount": discount.quantize(Decimal("0.01")),
    }


def _planned_returns(rng, line: dict, order_day: date) -> list[dict]:
    """0, 1 or 2 return events for a line, each within the 30-day window and inside 2026."""
    if line["category"] == "GIFT_CARD" or rng.random() >= cfg.RETURN_RATE:
        return []
    qty = line["qty"]
    pattern = "full" if qty == 1 else rng.choices(("full", "one", "two"), weights=cfg.RETURN_PATTERN_WEIGHTS)[0]
    if pattern == "full":
        events = [(rng.randint(1, RETURN_WINDOW_DAYS), qty)]
    elif pattern == "one":
        events = [(rng.randint(1, RETURN_WINDOW_DAYS), rng.randint(1, qty - 1))]
    else:   # two separate returns on different days; the second may return the rest
        first_qty = rng.randint(1, qty - 1)
        first_day = rng.randint(1, RETURN_WINDOW_DAYS - 1)
        events = [(first_day, first_qty),
                  (rng.randint(first_day + 1, RETURN_WINDOW_DAYS), rng.randint(1, qty - first_qty))]
    planned = []
    for days_after, units in events:
        day = order_day + timedelta(days=days_after)
        if day > AS_OF_DATE:          # nothing is dated after 31 Dec
            break
        planned.append({"line_id": line["line_id"], "return_ts": datetime.combine(day, _random_time(rng)),
                        "qty_returned": units})
    return planned


def plan_orders(rng, scale, accounts: list[dict]) -> tuple[list, list, list, list[float]]:
    """Returns (orders, order_lines, planned_returns, activity_weights)."""
    weights = [rng.lognormvariate(0, cfg.ACTIVITY_SIGMA) for _ in accounts]
    owners = rng.choices(range(len(accounts)), weights=weights, k=scale.orders)
    drafts = sorted((_random_order_ts(rng, accounts[i]["opened_at"]), i, seq)
                    for seq, i in enumerate(owners))

    orders, lines, returns = [], [], []
    for n, (ts, account_index, _) in enumerate(drafts, start=1):   # numbered in time order
        ord_id = ids.order_id(n)
        n_lines = rng.choices(cfg.LINES_PER_ORDER, weights=cfg.LINES_WEIGHTS)[0]
        order_lines = [_line(rng, ord_id, k) for k in range(1, n_lines + 1)]
        online = rng.random() < cfg.ONLINE_SHARE
        net = sum(l["unit_price"] * l["qty"] - l["discount"] for l in order_lines)
        orders.append({
            "ord_id": ord_id, "loy_id": accounts[account_index]["loy_id"], "order_ts": ts,
            "channel": "ONLINE" if online else "STORE",
            "shipping_amount": Decimal(rng.choice(cfg.SHIPPING_FEES) if online else 0).quantize(Decimal("0.01")),
            "tax_amount": (net * cfg.TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        })
        lines.extend(order_lines)
        for line in order_lines:
            returns.extend(_planned_returns(rng, line, ts.date()))
    return orders, lines, returns, weights


def plan_redemptions(rng, scale, accounts: list[dict], orders: list[dict], weights: list[float]) -> list[dict]:
    """Redemption requests in time order (reward and rdm_id assigned at processing time).

    `request_key` fixes the processing order: (timestamp, account, draw order); a concurrent
    pair shares one timestamp and is processed in key order.
    """
    first_order, order_count = {}, {}
    for o in orders:
        first_order.setdefault(o["loy_id"], o["order_ts"].date())
        order_count[o["loy_id"]] = order_count.get(o["loy_id"], 0) + 1

    def request_ts(loy_id: str, not_before: date):
        earliest = max(not_before, first_order[loy_id] + timedelta(days=RETURN_WINDOW_DAYS + 1))
        if earliest > AS_OF_DATE:
            return None
        day = earliest + timedelta(days=rng.randint(0, (AS_OF_DATE - earliest).days))
        return datetime.combine(day, _random_time(rng))

    drafts = []   # (ts, loy_id, seq, pair_key)
    count = round(cfg.REDEMPTIONS_PER_ORDER * scale.orders)
    for seq, i in enumerate(rng.choices(range(len(accounts)), weights=weights, k=count)):
        loy_id = accounts[i]["loy_id"]
        ts = request_ts(loy_id, DATASET_START) if loy_id in first_order else None
        if ts:
            drafts.append((ts, loy_id, seq, None))

    # Concurrent pairs: the most active 20% of accounts (they have points to spend), late in the year.
    active = sorted(order_count, key=lambda loy: (-order_count[loy], loy))
    candidates = sorted(active[: max(1, len(active) // 5)])
    n_pairs = max(1, round(cfg.CONCURRENT_PAIR_RATE * scale.customers))
    for p, loy_id in enumerate(rng.sample(candidates, min(n_pairs, len(candidates)))):
        ts = request_ts(loy_id, date(2026, 9, 1))
        if ts:
            pair_key = f"PAIR-{p + 1}"
            drafts.append((ts, loy_id, count + 2 * p, pair_key))
            drafts.append((ts, loy_id, count + 2 * p + 1, pair_key))

    drafts.sort(key=lambda d: (d[0], d[1], d[2]))
    return [{"request_key": f"{n:08d}", "loy_id": loy_id, "requested_at": ts, "pair_key": pair_key}
            for n, (ts, loy_id, _, pair_key) in enumerate(drafts, start=1)]
