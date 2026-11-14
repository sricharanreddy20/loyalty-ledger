# Anomaly detector report

Detector: rule-based SQL over input tables (no ground truth, no id gaps).

| Issue type | Ground truth | Flagged | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BALANCE_MISMATCH | 10 | 10 | 10 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| REDEMPTION_EXCEEDS_BALANCE | 10 | 10 | 10 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| RETURN_KEEPS_POINTS | 10 | 10 | 10 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| DOUBLE_EXPIRY | 10 | 10 | 10 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| TIER_USES_GROSS_SPEND | 10 | 10 | 10 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| **Overall (micro)** | 50 | 50 | 50 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |

## False positives

None.

## False negatives

None.
