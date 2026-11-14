"""Build the whole dataset from one seed. Same seed and scale -> identical dataset."""
import random

from generator.config import SCALES
from generator.dataset import Dataset
from generator.master import build_customers_and_accounts, build_rewards, build_rules, build_tiers
from generator.plan import plan_orders, plan_redemptions
from generator.simulate import simulate


def generate(seed: int, scale_name: str) -> Dataset:
    scale = SCALES[scale_name]
    rng = random.Random(seed)       # the only source of randomness, passed everywhere
    ds = Dataset()
    ds.tier = build_tiers()
    ds.rule = build_rules()
    ds.reward = build_rewards()
    ds.customer, ds.loyalty_account = build_customers_and_accounts(rng, scale.customers)
    orders, lines, planned_returns, weights = plan_orders(rng, scale, ds.loyalty_account)
    ds.orders, ds.order_line = orders, lines
    planned_redemptions = plan_redemptions(rng, scale, ds.loyalty_account, orders, weights)
    simulate(rng, ds, orders, lines, planned_returns, planned_redemptions)
    return ds
