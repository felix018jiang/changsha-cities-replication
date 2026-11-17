"""Verify the selected plans and reported optima independently on the frozen OD matrix."""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix


PROJECT = Path(__file__).resolve().parents[2]
FROZEN = PROJECT / "outputs" / "长沙养老研究_第十九轮" / "tables"
OUTPUT = PROJECT / "outputs" / "Cities_补充计算与图表" / "tables"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def compare(observed, expected):
    if not math.isclose(float(observed), float(expected), rel_tol=0, abs_tol=1e-5):
        raise ValueError(f"Mismatch: {observed} versus {expected}")


class FrozenRoutes:
    def __init__(self):
        origins = read_csv(FROZEN / "routing_origins.csv")
        self.origins = {r["origin_id"]: r for r in origins if r["scenario"] == "strict"}
        self.od = read_csv(FROZEN / "routing_reachable_od.csv")
        self.candidates = [r for r in read_csv(FROZEN / "routing_candidates.csv")
                           if r["scenario"] == "strict"]

    def market(self, function, distance, menu):
        eligible = set()
        for row in self.candidates:
            if row["function"] != function:
                continue
            if (menu == "expanded" or row["pool_type"] == "inside" or
                    (menu == "operator" and row["source_family"] == "霸碗运营方门店目录")):
                eligible.add(row["site_id"])
        unserved = {oid: r for oid, r in self.origins.items()
                    if float(r[f"distance_{function}_m"]) > distance}
        catchments = {site: set() for site in eligible}
        for pair in self.od:
            site, oid = pair["site_id"], pair["origin_id"]
            if site in eligible and oid in unserved and float(pair["distance_m"]) <= distance:
                catchments[site].add(oid)
        return CoverageCheck(unserved, catchments)


class CoverageCheck:
    def __init__(self, origins, catchments):
        self.origins = origins
        self.catchments = catchments
        self.population = math.fsum(float(r["older_population"]) for r in origins.values())

    def by_circle(self, sites):
        reached = set().union(*(self.catchments[s] for s in sites))
        gains = defaultdict(float)
        for oid in sorted(reached):
            row = self.origins[oid]
            gains[row["case_id"]] += float(row["older_population"])
        return dict(gains)

    def optimum(self, k, *, remove=None, circles_required=0, floors=None):
        # Demand cells with exactly the same options in a circle can share a coverage variable.
        sites = sorted(s for s, hits in self.catchments.items() if hits and s != remove)
        nearby = defaultdict(list)
        for j, site in enumerate(sites):
            for oid in self.catchments[site]:
                nearby[oid].append(j)
        patterns = defaultdict(float)
        for oid in sorted(nearby):
            row = self.origins[oid]
            patterns[(row["case_id"], tuple(nearby[oid]))] += float(row["older_population"])
        groups = list(patterns)
        circles = sorted({circle for circle, _ in groups})
        n, m = len(sites), len(groups)
        extra = len(circles) if circles_required else 0
        width = n + m + extra
        indices = []
        values = []
        indptr = [0]
        lower, upper = [], []

        def constrain(entries, low, high):
            for col, value in entries:
                indices.append(col)
                values.append(value)
            indptr.append(len(values))
            lower.append(low)
            upper.append(high)

        constrain(((j, 1) for j in range(n)), -np.inf, k)
        circle_sites = defaultdict(set)
        circle_patterns = defaultdict(list)
        for i, (circle, options) in enumerate(groups):
            constrain([(n+i, 1)] + [(j, -1) for j in options], -np.inf, 0)
            circle_sites[circle].update(options)
            circle_patterns[circle].append((n+i, patterns[groups[i]]))
        if circles_required:
            for i, circle in enumerate(circles):
                # A flagged circle needs an opened address that reaches some unserved cell.
                constrain([(n+m+i, 1)] + [(j, -1) for j in circle_sites[circle]], -np.inf, 0)
            constrain(((n+m+i, 1) for i in range(extra)), circles_required, np.inf)
        for circle, minimum in (floors or {}).items():
            if minimum > 0:
                constrain(circle_patterns[circle], minimum, np.inf)
        constraints = csr_matrix((values, indices, indptr), shape=(len(lower), width))
        objective = np.r_[np.zeros(n), [-patterns[g] for g in groups], np.zeros(extra)]
        result = milp(objective, integrality=np.r_[np.ones(n), np.zeros(m), np.ones(extra)],
                      bounds=Bounds(np.zeros(width), np.ones(width)),
                      constraints=LinearConstraint(constraints, lower, upper),
                      options={"mip_rel_gap": 0.0})
        if not result.success:
            raise RuntimeError(result.message)
        selected = [site for site, opened in zip(sites, result.x[:n]) if opened > 0.5]
        actual = self.by_circle(selected)
        total = math.fsum(actual.values())
        compare(total, -result.fun)
        if len(actual) < circles_required or any(actual.get(c, 0) + 1e-5 < minimum
                                                  for c, minimum in (floors or {}).items()):
            raise ValueError("Reconstructed union violates geographic constraints")
        return total, frozenset(selected)

def site_ids(value):
    return set(value.split("|")) if value else set()


def check_plan(market, ids, k, reported, removed=None):
    selected = site_ids(ids)
    if len(selected) > k or removed in selected or not selected <= market.catchments.keys():
        raise ValueError(f"Ineligible selection at budget {k}: {ids}")
    gains = market.by_circle(selected)
    compare(math.fsum(gains.values()), reported)
    return gains


def verify():
    routes = FrozenRoutes()
    totals = read_csv(OUTPUT / "activity_plans.csv")
    details = read_csv(OUTPUT / "activity_circles.csv")
    floors = {r["case_id"]: float(r["newly_covered"]) for r in details if r["scenario"] == "station_only"}
    if len(floors) != 16 or len(details) != 80 or len(totals) != 5:
        raise ValueError("Incomplete activity plans or circle breakdown")
    for row in totals:
        menu = "stations" if row["scenario"] == "station_only" else "expanded"
        market = routes.market("S", 720, menu)
        actual = check_plan(market, row["selected_site_ids"], 10, row["newly_covered"])
        compare(len(actual), row["benefiting_circles"])
        protected = floors if row["scenario"] == "circle_floors" else {}
        for detail in details:
            if detail["scenario"] == row["scenario"]:
                circle = detail["case_id"]
                compare(detail["newly_covered"], actual.get(circle, 0))
                if actual.get(circle, 0) + 1e-5 < protected.get(circle, 0):
                    raise ValueError(f"Circle floor violation for {circle}")
        optimum, _ = market.optimum(10, circles_required=int(row["min_positive_circles"]), floors=protected)
        compare(optimum, row["newly_covered"])

    walking = read_csv(OUTPUT / "walking_thresholds.csv")
    for row in walking:
        menu = {"stations": "stations", "operator": "operator", "all": "expanded"}[row["menu"]]
        function = {"meals": "M", "activities": "S"}[row["service"]]
        market = routes.market(function, int(row["threshold_m"]), menu)
        compare(market.population, row["initial_unserved"])
        compare(0.05*market.population, row["target_people"])
        compare(len(market.catchments), row["eligible_sites"])
        k = int(row["minimum_sites"])
        for budget, ids, value in ((k-1, "previous_site_ids", "best_at_previous"),
                                   (k, "selected_site_ids", "best_at_minimum"),
                                   (10, "ten_site_ids", "ten_site_gain")):
            check_plan(market, row[ids], budget, row[value])
            compare(market.optimum(budget)[0], row[value])
        if not float(row["best_at_previous"]) < 0.05*market.population <= float(row["best_at_minimum"]):
            raise ValueError("Reported walking target does not bracket the minimum")

    market = routes.market("M", 720, "expanded")
    removals = read_csv(OUTPUT / "meal_site_removals.csv")
    removed_ids = {r["removed_site_id"] for r in removals}
    if removed_ids != (set(market.catchments) | {"none"}) or len(removals) != len(removed_ids):
        raise ValueError("Expected a baseline and every individual eligible address exactly once")
    budgets = {int(row["minimum_sites"]) - adjustment for row in removals for adjustment in (0, 1)}
    best_full = {k: market.optimum(k) for k in budgets}
    for row in removals:
        removed = None if row["removed_site_id"] == "none" else row["removed_site_id"]
        k = int(row["minimum_sites"])
        compare(row["target_people"], market.population*0.05)
        for budget, ids, value in ((k-1, "previous_site_ids", "best_at_previous"),
                                   (k, "selected_site_ids", "best_at_minimum")):
            check_plan(market, row[ids], budget, row[value], removed)
            optimum, witness = best_full[budget]
            # Deleting an unused location leaves the full-inventory optimum feasible and optimal.
            if removed in witness:
                optimum, _ = market.optimum(budget, remove=removed)
            compare(optimum, row[value])
        if not float(row["best_at_previous"]) < market.population*0.05 <= float(row["best_at_minimum"]):
            raise ValueError("Reported deletion target does not bracket the minimum")
    if len(walking) != 15:
        raise ValueError("Expected five service-menu combinations for each of three speeds")
    return {"activity_policies_verified": len(totals), "circle_outcomes_verified": len(details),
            "walking_scenarios_verified": len(walking), "single_deletions_verified": len(removals)-1}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))


