"""Recompute the three siting analyses from the frozen Changsha pedestrian distances.

Run from the project root:
    work/round3/.venv/bin/python work/cities_revision_20261116/supplementary_analysis.py

Requires Python 3.10+, NumPy and SciPy with scipy.optimize.milp. The source tables
contain candidate routes up to 720 m, so they also support every shorter cut-off.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "work/round19/optimization"))
from solve import CoverageSolver, EMPTY, Plan, better, meets_target, read, save

SOURCE = ROOT / "outputs/长沙养老研究_第十九轮/tables"
OUTPUT = ROOT / "outputs/Cities_补充计算与图表/tables"
BUDGET = 10
NAMES = {
    "CSX-XS-4": "Xingsha", "FR-XH-1": "Wulipai", "KF-HS-1": "Hongshan",
    "KF-XF-4": "Xiufeng", "TX-NT-5": "Nantuo", "TX-XF-1": "Xianfeng",
    "WC-GTL-21": "Gaotangling", "WC-JSQ-1": "Jinshanqiao",
    "WC-YLD-3": "Yueliangdao", "YH-JWZ-3": "Jingwanzi", "YH-LT-1": "Lituo",
    "YL-GSL-3": "Guanshaling", "YL-LG-5": "Lugu", "YL-TD-4": "Tianding",
    "YL-WCP-1": "Wangchengpo", "YL-YL-5": "Yuelu",
}
LABELS = {
    "chain:bawan:142170042b7bd3": "Bawan Rice Bowl (Shaoshan South Road)",
    "osm:node:7071192121": "Koufulou",
    "osm:node:7071192140": "Laoshi Noodle Shop",
}


def same(a, b):
    if not math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-6):
        raise ValueError(f"Reconstruction mismatch: {a} versus {b}")


def covered_origins(plan, options, weights):
    reached = set().union(*(options[s] for s in plan.sites))
    same(plan.value, math.fsum(weights[o] for o in reached))
    return reached


def selection_fields(plan, catalog, prefix=""):
    return {prefix + "site_ids": "|".join(plan.sites),
            prefix + "site_names": "|".join(catalog[s]["site_name"] for s in plan.sites)}


class FrozenInputs:
    def __init__(self, source):
        self.origins = {r["origin_id"]: r for r in read(source / "routing_origins.csv")
                        if r["scenario"] == "strict"}
        assert len(self.origins) == 5959 and {r["case_id"] for r in self.origins.values()} == NAMES.keys()
        self.weights = {oid: float(r["older_population"]) for oid, r in self.origins.items()}
        assert all(w > 0 for w in self.weights.values())
        self.catalog = {"M": {}, "S": {}}
        for r in read(source / "routing_candidates.csv"):
            if r["scenario"] == "strict":
                assert r["site_id"] not in self.catalog[r["function"]]
                self.catalog[r["function"]][r["site_id"]] = r
        self.pairs = read(source / "routing_reachable_od.csv")
        self.case = {o: r["case_id"] for o, r in self.origins.items()}
        assert all(self.case[r["origin_id"]] == r["case_id"] for r in self.pairs)
        self.solver = CoverageSolver(self.weights)

    def rebuild(self, function, distance):
        if not 0 < distance <= 720:
            raise ValueError("The archived candidate OD pairs support thresholds up to 720 m.")
        unmet = {o for o, r in self.origins.items() if float(r[f"distance_{function}_m"]) > distance}
        if distance == 720:
            recorded = {o for o, r in self.origins.items() if r[f"covered_{function}"] == "False"}
            assert unmet == recorded
        sites = {s: set() for s in self.catalog[function]}
        for r in self.pairs:
            s, o = r["site_id"], r["origin_id"]
            if s in sites and o in unmet and float(r["distance_m"]) <= distance:
                sites[s].add(o)
        return unmet, {s: frozenset(os) for s, os in sites.items()}

    def menus(self, function, options):
        catalog = self.catalog[function]
        stations = {s: os for s, os in options.items() if catalog[s]["pool_type"] == "inside"}
        yield "stations", stations
        if function == "M":
            brand = {s: os for s, os in options.items() if catalog[s]["source_family"] == "霸碗运营方门店目录"}
            assert len(brand) == 23
            yield "operator", stations | brand
        yield "all", options


def target_statistics(curve, options, weights, catalog, initial_unserved):
    target = initial_unserved * 0.05
    k = next((k for k, plan in enumerate(curve) if meets_target(plan.value, target)), None)
    result = dict(initial_unserved=initial_unserved, target_people=target, minimum_sites=k,
                  best_at_previous=None, best_at_minimum=None,
                  previous_site_ids="", previous_site_names="", selected_site_ids="", selected_site_names="")
    if k is not None:
        plan = curve[k]
        covered_origins(plan, options, weights)
        assert len(plan.sites) == k
        result.update(best_at_minimum=plan.value, **selection_fields(plan, catalog, "selected_"))
        if k:
            prev = curve[k - 1]
            assert not meets_target(prev.value, target)
            covered_origins(prev, options, weights)
            result.update(best_at_previous=prev.value, **selection_fields(prev, catalog, "previous_"))
    return result


def walking_analysis(data, source):
    budgets = {(r["function"], r["site_budget"]): r for r in read(source / "siting_budget_comparison.csv")
               if r["registry"] == "strict" and r["scope"] == "ALL16"}
    operator = {r["site_budget"]: r for r in read(source / "siting_operator_directory_sensitivity.csv")
                if r["registry"] == "strict" and r["scope"] == "ALL16"}
    targets = {(r["function"], r["menu"]): r for r in read(source / "siting_targets.csv")
               if r["registry"] == "strict" and r["scope"] == "ALL16" and r["target_percent"] == "5"}
    rows = []
    for distance in (720, 630, 540):
        for function, label in (("M", "meals"), ("S", "activities")):
            unmet, options = data.rebuild(function, distance)
            gap = math.fsum(data.weights[o] for o in unmet)
            for menu, choices in data.menus(function, options):
                curve = data.solver.frontier(choices)
                target = target_statistics(curve, choices, data.weights, data.catalog[function], gap)
                ten = curve[min(BUDGET, len(curve) - 1)]
                covered_origins(ten, choices, data.weights)
                if distance == 720:
                    if menu == "operator":
                        same(ten.value, operator["10"]["within_budget_gain"])
                        same(target["best_at_minimum"], operator[str(target["minimum_sites"])]["within_budget_gain"])
                    else:
                        prefix = {"stations": "inside_only", "all": "expanded"}[menu]
                        same(ten.value, budgets[function, "10"][prefix + "_max_gain"])
                        ref = targets[function, prefix]
                        same(target["minimum_sites"], ref["minimum_sites"])
                        same(target["best_at_minimum"], ref["coverage_at_minimum"])
                rows.append(dict(service=label, menu=menu, threshold_m=distance, speed_mps=distance / 900,
                                 eligible_sites=len(choices), **target, ten_site_gain=ten.value,
                                 **selection_fields(ten, data.catalog[function], "ten_"),
                                 coverage_ceiling=curve[-1].value))
    return rows


def removal_analysis(data):
    unmet, options = data.rebuild("M", 720)
    gap = math.fsum(data.weights[o] for o in unmet)
    catalog = data.catalog["M"]
    assert len(options) == 199 and LABELS.keys() <= options.keys()
    # Check and retain all best two-site alternatives on the original columns.
    # Dominance reduction in the faster frontier solver can hide equally good addresses.
    ranked_pairs = sorted(
        ((math.fsum(data.weights[o] for o in options[a] | options[b]), (a, b))
         for a, b in combinations(sorted(options), 2)), reverse=True
    )
    rows = []
    for removed in [None, *sorted(options)]:
        choices = {s: os for s, os in options.items() if s != removed}
        curve = data.solver.frontier(choices)
        target = target_statistics(curve, choices, data.weights, catalog, gap)
        best_pairs = []
        for value, pair in ranked_pairs:
            if value < curve[2].value - 1e-8:
                break
            if removed not in pair:
                same(value, curve[2].value)
                best_pairs.append(pair)
        assert best_pairs
        rec = catalog.get(removed, {})
        rows.append(dict(removed_site_id=removed or "none", removed_site_name=rec.get("site_name", ""),
                         removed_site_label=LABELS.get(removed, removed or "No removal"),
                         removed_pool=rec.get("pool_type", ""), remaining_candidates=len(choices),
                         **target, best_single=curve[1].value, best_pair=curve[2].value,
                         best_pair_count=len(best_pairs),
                         all_best_pair_site_ids=json.dumps(sorted(best_pairs)),
                         all_best_pair_names=json.dumps(
                             [[catalog[s]["site_name"] for s in pair] for pair in sorted(best_pairs)],
                             ensure_ascii=False)))
    return rows


def circle_allocation(curves, min_circles, floors):
    """DP state (slots used, positive-gain circles), with optional local gain floors."""
    states = {(0, 0): EMPTY}
    for case, curve in curves.items():
        next_states = {}
        for (used, breadth), pooled in states.items():
            for local in curve[:BUDGET + 1]:
                slots = used + len(local.sites)
                if slots > BUDGET or not meets_target(local.value, floors.get(case, 0)):
                    continue
                key = slots, breadth + int(local.value > 0)
                plan = Plan(pooled.value + local.value, tuple(sorted(pooled.sites + local.sites)))
                if better(plan, next_states.get(key)):
                    next_states[key] = plan
        states = next_states
    best = None
    for (_, breadth), plan in states.items():
        if breadth >= min_circles and better(plan, best):
            best = plan
    if best is None:
        raise ValueError("The requested allocation is infeasible.")
    return best


def verify_activity_milp(data, options, plan, min_circles, floors):
    """Independent pooled MILP with the unreduced origin-by-site incidence matrix."""
    sites = sorted(options)
    origins = sorted(set().union(*options.values()))
    cases = sorted(NAMES)
    n, m, c = len(sites), len(origins), len(cases)
    origin_index = {o: i for i, o in enumerate(origins)}
    case_index = {case: i for i, case in enumerate(cases)}
    terms, lower, upper = [], [], []

    def constraint(entries, lo=-np.inf, hi=np.inf):
        terms.extend((len(lower), col, val) for col, val in entries)
        lower.append(lo)
        upper.append(hi)

    serving = defaultdict(list)
    local_sites = defaultdict(list)
    for j, s in enumerate(sites):
        for o in options[s]:
            serving[o].append(j)
        for case in {data.case[o] for o in options[s]}:
            local_sites[case].append(j)
    for i, o in enumerate(origins):
        constraint([(n + i, 1)] + [(j, -1) for j in serving[o]], hi=0)
    constraint([(j, 1) for j in range(n)], hi=BUDGET)
    for case in cases:
        z = n + m + case_index[case]
        constraint([(z, 1)] + [(j, -1) for j in local_sites[case]], hi=0)
        if floors.get(case, 0) > 0:
            constraint([(n + origin_index[o], data.weights[o]) for o in origins if data.case[o] == case],
                       lo=floors[case])
    constraint([(n + m + i, 1) for i in range(c)], lo=min_circles)
    r, col, values = zip(*terms)
    matrix = coo_matrix((values, (r, col)), shape=(len(lower), n + m + c)).tocsc()
    objective = np.r_[np.zeros(n), [-data.weights[o] for o in origins], np.zeros(c)]
    result = milp(objective, integrality=np.r_[np.ones(n), np.zeros(m), np.ones(c)],
                  bounds=Bounds(0, 1), constraints=LinearConstraint(matrix, lower, upper),
                  options={"mip_rel_gap": 0})
    if not result.success or result.mip_gap > 1e-9:
        raise ValueError(f"Activity MILP did not certify optimality: {result.message}")
    same(-result.fun, plan.value)
    return -float(result.fun)


def activity_analysis(data, source):
    unmet, expanded = data.rebuild("S", 720)
    menus = dict(data.menus("S", expanded))
    controls = {r["case_id"]: float(r["newly_covered"])
                for r in read(source / "siting_pooled_distribution.csv") if
                (r["registry"], r["function"], r["menu"], r["site_budget"]) == ("strict", "S", "inside_only", "10")}
    assert controls.keys() == NAMES.keys()
    local_options = {case: {} for case in sorted(NAMES)}
    for s, origins in expanded.items():
        cases = {data.case[o] for o in origins}
        if len(cases) > 1:
            raise ValueError("Circle-specific dynamic programming requires disjoint beneficiary circles per site.")
        if origins:
            local_options[cases.pop()][s] = origins
    curves = {case: data.solver.frontier(opts) for case, opts in local_options.items()}
    summaries, details = [], []
    floors = {}
    for scenario, menu, count in (("station_only", "stations", 0), ("expanded", "all", 0),
                                  ("min_7_circles", "all", 7), ("min_8_circles", "all", 8),
                                  ("circle_floors", "all", 0)):
        choices = menus[menu]
        required = floors if scenario == "circle_floors" else {}
        if count or required:
            plan = circle_allocation(curves, count, required)
        else:
            plan = data.solver.frontier(choices)[BUDGET]
        optimum = verify_activity_milp(data, choices, plan, count, required)
        reached = covered_origins(plan, choices, data.weights)
        gains = {case: math.fsum(data.weights[o] for o in reached if data.case[o] == case) for case in NAMES}
        if scenario == "station_only":
            for case, value in gains.items():
                same(value, controls[case])
            floors = gains
        breadth = sum(v > 0 for v in gains.values())
        assert breadth >= count and len(plan.sites) <= BUDGET
        assert all(meets_target(gains[case], value) for case, value in required.items())
        summaries.append(dict(scenario=scenario, newly_covered=plan.value, benefiting_circles=breadth,
                              sites_used=len(plan.sites), min_positive_circles=count, protect_station_gains=bool(required),
                              gain_over_stations=math.fsum(gains.values())-math.fsum(floors.values()),
                              independent_milp_optimum=optimum,
                              **selection_fields(plan, data.catalog["S"], "selected_")))
        for case, gain in gains.items():
            details.append(dict(scenario=scenario, case_id=case, circle=NAMES[case], newly_covered=gain,
                                station_reference_gain=floors[case], required_gain=required.get(case, 0),
                                initial_unserved=math.fsum(data.weights[o] for o in unmet if data.case[o] == case),
                                selected_site_ids="|".join(s for s in plan.sites if s in local_options[case])))
    return summaries, details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE, help="Frozen round-19 tables")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    data = FrozenInputs(args.source)
    thresholds = walking_analysis(data, args.source)
    removals = removal_analysis(data)
    activities, circles = activity_analysis(data, args.source)
    outputs = {"activity_plans": activities, "activity_circles": circles,
               "meal_site_removals": removals, "walking_thresholds": thresholds}
    args.output.mkdir(parents=True, exist_ok=True)
    for stem, rows in outputs.items():
        save(args.output / (stem + ".csv"), rows)
    print(json.dumps({stem: len(rows) for stem, rows in outputs.items()}, indent=2))


if __name__ == "__main__":
    main()
