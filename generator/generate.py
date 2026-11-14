"""Build the dataset from one seed. Same seed and scale -> identical dataset.

generate() returns the fully valid baseline; build() continues with the SAME random
generator to inject labelled anomalies, and returns (dataset, ground_truth).
"""
import random

from generator.anomalies import inject_anomalies
from generator.config import SCALES
from generator.dataset import Dataset
from generator.master import build_customers_and_accounts, build_rewards, build_rules, build_tiers
from generator.plan import plan_orders, plan_redemptions
from generator.simulate import simulate


def _baseline(rng, scale) -> Dataset:
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


def generate(seed: int, scale_name: str) -> Dataset:
    """The valid baseline only (no anomalies)."""
    return _baseline(random.Random(seed), SCALES[scale_name])


def build(seed: int, scale_name: str) -> tuple[Dataset, list[dict]]:
    """Baseline plus labelled anomalies. One random generator for the whole run."""
    scale = SCALES[scale_name]
    rng = random.Random(seed)
    ds = _baseline(rng, scale)
    ground_truth = inject_anomalies(rng, ds, scale.anomalies_per_type)
    return ds, ground_truth
