"""Generator settings: seed and scale presets. Both scales use the same code paths."""
from dataclasses import dataclass

DEFAULT_SEED = 20261022


@dataclass(frozen=True)
class Scale:
    name: str
    customers: int
    orders: int
    min_lines_per_order: int
    max_lines_per_order: int
    anomaly_rate: float   # share of eligible records mutated per anomaly type (tuned in Step 4)


SCALES = {
    "starter": Scale("starter", customers=1_000, orders=5_000,
                     min_lines_per_order=1, max_lines_per_order=4, anomaly_rate=0.0025),
    "full": Scale("full", customers=20_000, orders=100_000,
                  min_lines_per_order=1, max_lines_per_order=4, anomaly_rate=0.0025),
}
