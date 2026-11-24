"""Check the numbers reported in the Chinese full manuscript for the 16 living circles."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from manuscript_results import circle_results, offsite_reach

ROOT = Path(__file__).resolve().parent
PRIMARY = ROOT / "outputs/长沙养老研究_第十九轮/tables"
SUPP = ROOT / "outputs/Cities_补充计算与图表/tables"


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def check(label, observed, expected):
    if observed != expected:
        raise ValueError(f"{label}: found {observed!r}, manuscript reports {expected!r}")


def check_maps(primary, circles, route):
    pooled = circles.loc["ALL16"]
    local = circles.drop("ALL16")
    meals = [pooled.meal_ten_site_people, pooled.meal_other_only_people, pooled.meal_beyond_sites_people]
    check("Figure 4E meal populations", [round(x, 1) for x in meals], [2537.1, 1645.2, 15382.1])
    check("Figure 4E percentages", [round(100 * x / sum(meals), 1) for x in meals], [13.0, 8.4, 78.6])
    check("Jingwanzi gain", round(local.at["YH-JWZ-3", "meal_ten_site_people"], 1), 819.3)
    tianding = local.loc["YL-TD-4"]
    check("Tianding meal populations",
          [round(x, 1) for x in (tianding.meal_ten_site_people, tianding.meal_other_only_people,
                                tianding.meal_beyond_sites_people)], [0, 251.7, 516.7])
    zero_meal = local.loc[local.meal_ten_site_people == 0]
    best_one = {r["scope"]: float(r["expanded_max_gain"])
                for r in rows(primary / "siting_budget_comparison.csv")
                if r["registry"] == "strict" and r["function"] == "M" and r["site_budget"] == "1"}
    check("Meal circles without a ten-site gain", len(zero_meal), 9)
    check("Circles with a positive local one-site option", sum(best_one[c] > 0 for c in local.index), 16)
    check("Tianding best one-site gain", round(best_one["YL-TD-4"], 1), 136.7)
    check("Activity gap, straight-line reach and walking reach",
          [round(x, 1) for x in (pooled.activity_initially_unserved, pooled.activity_direct_people,
                                pooled.activity_network_people)], [20101.6, 9109.2, 3086.5])
    check("Figure S2D pooled shares",
          [round(pooled.activity_direct_pct, 1), round(pooled.activity_network_pct, 1)], [45.3, 15.4])
    activity_beyond_pct = 100 * (pooled.activity_initially_unserved - pooled.activity_network_people) / pooled.activity_initially_unserved
    check("Section 4.4 residual activity gap", round(activity_beyond_pct, 2), 84.65)
    no_sites = sorted(local.loc[local.activity_eligible_sites == 0, "circle"])
    check("Circles without an eligible activity venue", no_sites,
          ["Jinshanqiao", "Nantuo", "Yueliangdao", "Yuelu"])
    check("Lituo activity venues", local.at["YH-LT-1", "activity_eligible_sites"], 1)
    check("Lituo route example", [route["radius_origins"], round(route["radius_population"], 1),
          route["walkable_origins"], round(route["direct_m"]), round(route["route_m"])], [20, 80.5, 0, 315, 1210])
    boundary = offsite_reach(primary)
    check("Off-site venues and cross-boundary gains", boundary,
          {"eligible": 179, "effective": 80, "effective_outside_circle": 55})
    return dict(meal_gap_population=sum(meals), meal_ten_site_people=meals[0], meal_other_only_people=meals[1],
                meal_beyond_sites_people=meals[2], meal_zero_gain_circles=len(zero_meal),
                activity_gap_population=pooled.activity_initially_unserved,
                activity_direct_people=pooled.activity_direct_people, activity_network_people=pooled.activity_network_people,
                activity_direct_pct=pooled.activity_direct_pct, activity_network_pct=pooled.activity_network_pct,
                activity_beyond_pct=activity_beyond_pct,
                activity_circles_without_candidates=no_sites, lituo=route, offsite_venues=boundary)


def main(primary=PRIMARY, supplementary=SUPP, *, spatial=None):
    activity = {r["scenario"]: r for r in rows(supplementary / "activity_plans.csv")}
    values = {k: float(v["newly_covered"]) for k, v in activity.items()}
    check("Figure 5 activity population", [round(values[k], 1) for k in
          ("station_only", "expanded", "min_7_circles", "min_8_circles", "circle_floors")],
          [2522.5, 2854.9, 2786.3, 2644.2, 2636.8])
    check("Population gained under circle floors", round(values["circle_floors"] - values["station_only"], 1), 114.3)
    check("Coverage cost of circle floors", round(values["expanded"] - values["circle_floors"], 1), 218.1)
    walking = rows(supplementary / "walking_thresholds.csv")
    thresholds = {str(d): [int(next(r["minimum_sites"] for r in walking
                             if r["service"] == "meals" and r["menu"] == m and int(r["threshold_m"]) == d))
                           for m in ("stations", "operator", "all")] for d in (720, 630, 540)}
    check("Table S5", thresholds, {"720": [7, 3, 2], "630": [5, 3, 2], "540": [8, 4, 3]})
    removed = [r for r in rows(supplementary / "meal_site_removals.csv") if r["removed_site_id"] != "none"]
    check("Figure S3A", [len(removed), sum(int(r["minimum_sites"]) == 2 for r in removed)], [199, 198])
    bawan = next(r for r in removed if r["removed_site_id"] == "chain:bawan:142170042b7bd3")
    check("Table S4 Bawan exclusion", [int(bawan["minimum_sites"]), round(float(bawan["best_at_previous"]), 1),
          round(float(bawan["best_at_minimum"]), 1)], [4, 956.2, 1126.8])
    origins = [r for r in rows(primary / "routing_origins.csv") if r["scenario"] == "strict"]
    population = math.fsum(float(r["older_population"]) for r in origins)
    check("Study population", [len(origins), round(population)], [5959, 40068])
    macro = [r for r in rows(ROOT / "data/citywide_400_circle_access.csv") if r["has_mapped_denominator"] == "True"]
    total = math.fsum(float(r["older_population"]) for r in macro)
    percentages = {k: math.fsum(float(r[k + "_people"]) for r in macro) / total * 100
                   for k in ("covered_H", "covered_M", "covered_S", "covered_G", "all_four", "missing_3_or_4")}
    check("Citywide supported circles", len(macro), 274)
    check("Figure S1", [round(v, 2) for v in percentages.values()], [41.30, 56.56, 53.95, 26.08, 5.74, 41.69])
    circles, route = circle_results(primary) if spatial is None else spatial
    return dict(status="passed", origins=len(origins), population=population, meal_minimum_sites=thresholds,
                activity_population=values, individual_exclusions=len(removed), citywide_supported_circles=len(macro),
                citywide_population=total, citywide_percentages=percentages, spatial_diagnostics=check_maps(primary, circles, route))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuscript", type=Path, help="Also verify the SHA-256 of a local Word manuscript")
    args = parser.parse_args()
    report = main()
    if args.manuscript:
        reference = json.loads((ROOT / "provenance/manuscript_version.json").read_text())
        digest = hashlib.sha256(args.manuscript.read_bytes()).hexdigest()
        check("Reference DOCX SHA-256", digest, reference["sha256"])
        report["manuscript_sha256"] = digest
    print(json.dumps(report, indent=2))
