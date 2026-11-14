"""Evaluation: validation summary + detector report, from the database.

CLI: python -m backend.app.evaluate [--out output/starter]
Writes validation_summary.json/.md and anomaly_report.json/.md (no timestamps, so reruns are identical).
"""
import argparse
import json
from pathlib import Path

from backend.app.db import ROOT, transaction
from backend.app.detector import detect, score
from backend.app.validation import run_validation


def snapshot(conn):
    """One consistent, read-only view of the database for all checks."""
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")


def evaluate() -> tuple[dict, dict]:
    with transaction() as conn:
        snapshot(conn)
        validation = run_validation(conn)
        report = score(conn, detect(conn))
    return validation, report


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def validation_markdown(v: dict) -> str:
    t = v["totals"]
    out = ["# Validation summary", "",
           f"{t['checks']} checks: **{t['PASS']} PASS**, **{t['EXPECTED_FAIL']} EXPECTED_FAIL**, **{t['FAIL']} FAIL**.", "",
           "EXPECTED_FAIL = every violation is on an account carrying a labelled anomaly of the listed type(s).", "",
           "| Check | Category | Status | Violations | Explained | Ground truth | Description |",
           "|---|---|---|---:|---:|---:|---|"]
    for c in v["checks"]:
        out.append(f"| `{c['id']}` | {c['category']} | {c['status']} | {c['violations']} | {c['explained']} | "
                   f"{_fmt(c['ground_truth_count']) if c['ground_truth_count'] is not None else ''} | {c['description']} |")
    return "\n".join(out) + "\n"


def anomaly_markdown(r: dict) -> str:
    out = ["# Anomaly detector report", "", f"Detector: {r['detector']}.", "",
           "| Issue type | Ground truth | Flagged | TP | FP | FN | Precision | Recall | F1 |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for m in r["metrics"] + [dict(r["overall"], issue_type="**Overall (micro)**")]:
        out.append(f"| {m['issue_type']} | {m['ground_truth']} | {m['flagged']} | {m['tp']} | {m['fp']} | {m['fn']} | "
                   f"{_fmt(m['precision'])} | {_fmt(m['recall'])} | {_fmt(m['f1'])} |")
    for title, rows in (("False positives", r["false_positives"]), ("False negatives", r["false_negatives"])):
        out += ["", f"## {title}", ""]
        out += [f"- {x['issue_type']} `{x['record_id']}`" for x in rows] or ["None."]
    return "\n".join(out) + "\n"


def write(out: Path, validation: dict, report: dict):
    out.mkdir(parents=True, exist_ok=True)
    for name, data, md in (("validation_summary", validation, validation_markdown(validation)),
                           ("anomaly_report", report, anomaly_markdown(report))):
        (out / f"{name}.json").write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
        (out / f"{name}.md").write_text(md, encoding="utf-8")


def print_summary(validation: dict, report: dict):
    t = validation["totals"]
    print(f"Validation: {t['checks']} checks, {t['PASS']} PASS, {t['EXPECTED_FAIL']} EXPECTED_FAIL, {t['FAIL']} FAIL")
    for c in validation["checks"]:
        if c["status"] != "PASS":
            print(f"  {c['status']:<14} {c['id']:<36} violations {c['violations']}")
    print("Detector:")
    for m in report["metrics"] + [dict(report["overall"], issue_type="OVERALL")]:
        print(f"  {m['issue_type']:<28} TP {m['tp']:>3} FP {m['fp']:>3} FN {m['fn']:>3}  "
              f"P {_fmt(m['precision'])} R {_fmt(m['recall'])} F1 {_fmt(m['f1'])}")


def main():
    parser = argparse.ArgumentParser(description="Run validation and the anomaly detector against the database.")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "starter")
    args = parser.parse_args()
    validation, report = evaluate()
    write(args.out, validation, report)
    print_summary(validation, report)
    print(f"Wrote validation_summary.json/.md and anomaly_report.json/.md to {args.out}")


if __name__ == "__main__":
    main()
