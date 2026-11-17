"""Extend the R17 native-grid routing and single-action comparison to both frozen batches.

Run from the project root with work/round3/.venv/bin/python work/round18/population/full_circle.py.
"""
import importlib.util
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs/长沙养老研究_第十八轮/tables"
SPEC = importlib.util.spec_from_file_location(
    "population_routing", ROOT / "work/round17/population/full_circle.py"
)
routing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(routing)


def check_previous_circles():
    """Compare every column of the two-circle archive after aligning record order."""
    origin_key = ["case_id", "origin_id"]
    scenario_key = ["case_id", "scenario", "support"]
    keys = {
        "native_weights": origin_key,
        "site_distances": origin_key + ["site_id"],
        "origin_support": scenario_key + ["origin_id"],
        "newly_reached_origins": scenario_key + ["origin_id", "plan_id"],
        "action_scores": scenario_key + ["plan_id"],
        "choices": scenario_key + ["plan_id"],
    }
    checked = {}
    for table, key in keys.items():
        name = f"population_{table}.csv"
        previous = pd.read_csv(routing.OUT / name)
        current = pd.read_csv(OUTPUT / name)
        current = current[current.case_id.isin(previous.case_id.unique())]
        previous = previous.set_index(key).sort_index()
        current = current.set_index(key).sort_index()
        assert previous.index.is_unique and current.index.is_unique
        pd.testing.assert_frame_equal(previous, current, check_dtype=False, check_exact=True)
        checked[table] = len(previous)
    return checked


def main():
    cases = pd.read_csv(routing.R15 / "decision_circle_comparison.csv").query(
        "scenario == 'strict' and k == 1"
    )[["batch", "case_id"]]
    assert cases.case_id.is_unique and cases.groupby("batch").size().eq(8).all() and len(cases) == 16
    checks = HERE / "checks.json"
    summary = routing.main(cases.case_id.tolist(), OUTPUT, checks)
    assert len(summary[routing.DIMS].drop_duplicates()) == 16 * 2 * 3
    audit = json.loads(checks.read_text())
    audit["round17_records_matched_by_table"] = check_previous_circles()
    temp = checks.with_suffix(".tmp")
    temp.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    temp.replace(checks)
    return summary


if __name__ == "__main__":
    print(main().groupby(["scenario", "support"]).case_id.nunique().to_string())
