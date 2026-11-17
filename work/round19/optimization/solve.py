"""Budget frontiers and minimum-site plans for first access to each service.

Input is the common round-19 pedestrian network routing and audited candidate ledger.
Run with the project's GIS Python from the repository root.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix, eye, hstack, vstack

ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT / "outputs/长沙养老研究_第十九轮/tables"
R18 = ROOT / "outputs/长沙养老研究_第十八轮/tables"
TARGET_PCTS = (1, 5, 10, 20, 30, 50, 75, 100)
MENUS = ("inside_only", "expanded")
REGISTRIES = ("strict", "wide")
FUNCTIONS = {"M": "助餐", "S": "日间活动"}
TOL = 1e-8


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as src:
        return list(csv.DictReader(src))


def save(path, rows):
    with NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False) as dst:
        staged = Path(dst.name)
        try:
            writer = csv.DictWriter(dst, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
    staged.chmod(0o644)
    staged.replace(path)


@dataclass(frozen=True)
class Plan:
    value: float
    sites: tuple


EMPTY = Plan(0., ())


def better(plan, best):
    if best is None:
        return True
    tol=min(TOL,min(best.value,plan.value)*1e-12)
    if plan.value > best.value + tol:
        return True
    if abs(plan.value - best.value) <= tol:
        return (len(plan.sites), plan.sites) < (len(best.sites), best.sites)
    return False


def components(options):
    """Groups of sites with overlapping positive-benefit origins across any circle."""
    parent = {s: s for s in options}

    def find(site):
        while site != parent[site]:
            parent[site] = parent[parent[site]]
            site = parent[site]
        return site

    first_site = {}
    for s, origins in options.items():
        for oid in origins:
            if oid in first_site:
                a, b = find(s), find(first_site[oid])
                if a != b:
                    parent[b] = a
            else:
                first_site[oid] = s
    groups = defaultdict(list)
    for s in sorted(options):
        groups[find(s)].append(s)
    return groups.values()


def undominated(options):
    """A site whose entire first-access set is contained in one other site can be replaced."""
    retained = {}
    for s in sorted(options, key=lambda key: (-len(options[key]), key)):
        o = options[s]
        if o and not any(o <= other for other in retained.values()):
            retained[s] = o
    return retained


def enumerate_small(options, weights):
    full = set.union(set(), *options.values())
    curve = [EMPTY]
    for k in range(1, len(options) + 1):
        best = curve[-1]
        covers_all = False
        for subset in combinations(sorted(options), k):
            reached = set.union(set(), *(options[s] for s in subset))
            plan = Plan(math.fsum(weights[o] for o in reached), subset)
            covers_all |= reached == full
            if better(plan, best):
                best = plan
        curve.append(best)
        if covers_all:
            # In the unusual case of tied floating-point weights, certify actual set coverage.
            if set.union(set(), *(options[s] for s in best.sites)) != full:
                sites = next(t for t in combinations(sorted(options), k)
                             if set.union(set(), *(options[s] for s in t)) == full)
                curve[-1] = Plan(math.fsum(weights[o] for o in full), sites)
            break
    return curve


def solve_integer(options, weights):
    sites = sorted(options)
    by_origin = defaultdict(list)
    for i, site in enumerate(sites):
        for o in options[site]:
            by_origin[o].append(i)
    # All origins accessible at the same set of candidate sites are covered together.
    patterns = defaultdict(list)
    for o, covering in sorted(by_origin.items()):
        patterns[tuple(covering)].append(weights[o])
    patterns = dict(sorted(patterns.items()))
    n, m = len(sites), len(patterns)
    pop = np.array([math.fsum(w) for w in patterns.values()])
    i, j = zip(*[(row, col) for row, pattern in enumerate(patterns) for col in pattern])
    a = csr_matrix((np.ones(len(i)), (i, j)), shape=(m, n))

    def optimise(c, integ, constraint):
        result = milp(c, integrality=integ, bounds=Bounds(0., 1.), constraints=constraint,
                      options={"mip_rel_gap": 0., "presolve": True})
        if result.status != 0 or result.x is None or result.mip_gap > 1e-9:
            raise RuntimeError(f"Uncertified covering model: {result.message}; gap={result.mip_gap}")
        return result

    minimum = optimise(np.ones(n), np.ones(n), LinearConstraint(a, 1., np.inf))
    full_subset = tuple(s for s, x in zip(sites, minimum.x) if x > .5)
    full = set(by_origin)
    assert set.union(set(), *(options[s] for s in full_subset)) == full
    assert abs(len(full_subset)-minimum.fun) < 1e-6
    curve = [EMPTY]
    matrix = vstack((hstack((-a, eye(m))), csr_matrix([[1.]*n+[0.]*m]))).tocsr()
    c = np.r_[np.zeros(n), -pop]
    integrality = np.r_[np.ones(n), np.zeros(m)]
    for k in range(1, len(full_subset)):
        bound = np.r_[np.zeros(m), k]
        result = optimise(c, integrality, LinearConstraint(matrix, -np.inf, bound))
        selected = tuple(s for s, x in zip(sites, result.x[:n]) if x > .5)
        assert len(selected) <= k
        reached = set.union(set(), *(options[s] for s in selected))
        plan = Plan(math.fsum(weights[o] for o in reached), selected)
        assert math.isclose(plan.value, -result.fun, rel_tol=1e-9, abs_tol=1e-6)
        curve.append(plan if better(plan, curve[-1]) else curve[-1])
    curve.append(Plan(math.fsum(weights[o] for o in full), full_subset))
    return curve


def combine(left, right):
    """Max-convolution over the available budget for components with disjoint beneficiaries."""
    curve = []
    for k in range(len(left) + len(right) - 1):
        best = None
        for j in range(max(0, k-len(left)+1), min(k, len(right)-1)+1):
            a, b = left[k-j], right[j]
            plan = Plan(a.value+b.value, tuple(sorted(a.sites+b.sites)))
            if better(plan, best):
                best = plan
        curve.append(best)
    return curve


class CoverageSolver:
    def __init__(self, weights):
        self.weights = weights
        self.enumerated = 0
        self.milp = 0
        self.component = lru_cache(maxsize=2048)(self._component)

    def _component(self, signature):
        options = dict(signature)
        if len(options) <= 10:
            self.enumerated += 1
            return enumerate_small(options, self.weights)
        self.milp += 1
        return solve_integer(options, self.weights)

    def frontier(self, available):
        options = undominated(available)
        curve = [EMPTY]
        for group in components(options):
            signature = tuple((s, options[s]) for s in sorted(group))
            curve = combine(curve, self.component(signature))
        assert all(a.value <= b.value+TOL for a, b in zip(curve,curve[1:]))
        full = set.union(set(), *available.values())
        reached = set.union(set(), *(available[s] for s in curve[-1].sites))
        assert full == reached
        return curve


def flag(value):
    assert value in {"True","False","1","0"}, value
    return value in {"1","True"}


class Data:
    def __init__(self, path):
        origin = read(path/"routing_origins.csv")
        candidate = read(path/"routing_candidates.csv")
        gains = read(path/"routing_gains.csv")
        self.names = {r["case_id"]:r["street"] for r in read(R18/"circle_choice_summary.csv")}
        self.cases = sorted(self.names)
        assert len(self.cases)==16
        self.weights = {}
        self.baseline, self.severe = {}, {}
        for r in origin:
            g, case = r["scenario"], r["case_id"]
            key = case, r["origin_id"]
            w = float(r["older_population"])
            assert w>0 and g in REGISTRIES and case in self.cases
            if key in self.weights:
                assert math.isclose(self.weights[key],w,rel_tol=0,abs_tol=1e-8)
            self.weights[key]=w
            assert (g,key) not in self.severe
            self.severe[g,key] = int(r["initial_deficit"]) >= 3
            for f in FUNCTIONS:
                self.baseline[g,f,key] = flag(r["covered_"+f])
        assert len(self.weights)==5959 and len(self.severe)==2*5959
        self.eligibility = defaultdict(dict)
        self.info = {}
        for r in candidate:
            g,f,case,s = r["scenario"], r["function"], r["case_id"], r["site_id"]
            assert g in REGISTRIES and f in FUNCTIONS and case in self.cases and s
            meta=r["site_name"], r["site_address"], r["lon_wgs84"], r["lat_wgs84"], r["pool_type"]
            assert meta[4] in {"inside","offsite"} and meta[0]
            old=self.info.setdefault(s,meta)
            assert old==meta, (s,old,meta)
            pool=self.eligibility[g,f,case]
            assert s not in pool
            pool[s]=set()
        for r in gains:
            g,f,case,s = r["scenario"], r["function"], r["case_id"], r["site_id"]
            key=case,r["origin_id"]
            assert not self.baseline[g,f,key]
            assert abs(float(r["older_population"])-self.weights[key])<1e-6
            assert float(r["distance_m"]) <= 720.000001
            members=self.eligibility[g,f,case][s]
            assert key not in members
            members.add(key)


def meets_target(value, required):
    # A zero baseline has no people left to serve; a positive target always requires a positive gain.
    if required == 0:
        return True
    return value > 0 and value + min(TOL, required*1e-12) >= required


def rounded(x):
    return round(x,9)


def run(source, out):
    data=Data(source)
    solver=CoverageSolver(data.weights)
    targets=[]; comparisons=[]; breakdown=[]; manifest={}
    benchmark=[]
    details=[]

    def plan_id(sites):
        if not sites:
            return ""
        pid="P"+hashlib.sha256("\n".join(sites).encode()).hexdigest()[:16]
        known=manifest.setdefault(pid,sites)
        assert known==sites
        return pid

    def labels(sites):
        return "；".join(data.info[s][0] for s in sites)

    def locations(sites):
        outloc=[]
        for s in sites:
            name,address,lon,lat,_ = data.info[s]
            text=name  # Public site ID; raw address and coordinates are withheld.
            outloc.append(text)
        return "；".join(outloc)

    for g in REGISTRIES:
        for f in FUNCTIONS:
            for sid in data.cases+["ALL16"]:
                cases=[sid] if sid!="ALL16" else data.cases
                label=data.names.get(sid,"All 16 circles")
                all_sites=defaultdict(set)
                for case in cases:
                    for s, origins in data.eligibility[g,f,case].items():
                        all_sites[s].update(origins)
                avail={menu:{s:frozenset(origins) for s,origins in all_sites.items()
                             if menu=="expanded" or data.info[s][4]=="inside"} for menu in MENUS}
                scope=set(cases)
                unmet={key for key in data.weights if key[0] in scope and not data.baseline[g,f,key]}
                denom=math.fsum(data.weights[o] for o in unmet)
                curves={}
                for menu, options in avail.items():
                    curve=solver.frontier(options)
                    curves[menu]=curve
                    full_members=set.union(set(),*options.values())
                    assert full_members<=unmet
                    maximum=math.fsum(data.weights[o] for o in full_members)
                    actual=set.union(set(),*(options[s] for s in curve[-1].sites))
                    assert actual==full_members and abs(curve[-1].value-maximum)<1e-6
                    assert len(curve)-1==len(curve[-1].sites)
                    benchmark.append(dict(registry=g,function=f,scope=sid,circle=label,menu=menu,
                        eligible_sites=len(options),positive_sites=sum(bool(v) for v in options.values()),
                        minimum_sites_to_saturate=len(curve)-1,initial_uncovered=rounded(denom),
                        attainable_population=rounded(maximum),remaining_unreachable=rounded(denom-maximum),
                        attainable_percent=rounded(100*maximum/denom) if denom else "",
                        saturation_plan_id=plan_id(curve[-1].sites)))
                    for pct in TARGET_PCTS:
                        needed=denom*pct/100
                        first=next(((k,p) for k,p in enumerate(curve)
                                    if meets_target(p.value,needed) and (pct!=100 or
                                        (full_members==unmet and k==len(curve)-1))),None)
                        if first is None:
                            k=None;plan=None
                        else:
                            k,plan=first
                            assert len(plan.sites)==k and (not k or pct==100 or not meets_target(curve[k-1].value,needed))
                        targets.append(dict(registry=g,function=f,scope=sid,circle=label,menu=menu,
                            target_percent=pct,initial_uncovered=rounded(denom),initial_uncovered_status="no_deficit" if denom==0 else "positive",target_people=rounded(needed),
                            status="achieved" if plan is not None else "unattainable",
                            minimum_sites=k if plan is not None else "",coverage_at_minimum=rounded(plan.value) if plan else "",
                            max_attainable_people=rounded(maximum),shortfall_to_target=rounded(max(needed-maximum,0.)),
                            plan_id=plan_id(plan.sites) if plan else "",
                            sites=labels(plan.sites) if plan else "",locations=locations(plan.sites) if plan else ""))
                    print(g,f,sid,menu,'sites',len(options),'sat',len(curve)-1,round(maximum,2),flush=True)
                # Past the saturation budget of both menus, all other budgets have the same gain.
                kmax=max(len(curves[m])-1 for m in MENUS)
                kmax=max(1, kmax, min(len(avail[m]) for m in MENUS))
                for k in range(kmax+1):
                    row=dict(registry=g,function=f,scope=sid,circle=label,site_budget=k,initial_uncovered=rounded(denom))
                    values={}
                    for menu in MENUS:
                        curve=curves[menu];options=avail[menu]
                        chosen=curve[min(k,len(curve)-1)]
                        selected=chosen.sites
                        members=set.union(set(),*(options[s] for s in selected))
                        assert len(selected)<=k and abs(math.fsum(data.weights[o] for o in members)-chosen.value)<1e-6
                        exact=len(options)>=k
                        # Add eligible redundant sites only for an explicitly requested exactly-k plan.
                        extra=sorted(options.keys()-set(selected))[:k-len(selected)] if exact else []
                        exact_set=tuple(sorted(selected+tuple(extra))) if exact else ()
                        if exact:
                            assert len(exact_set)==k and set.union(set(),*(options[s] for s in exact_set))==members
                        values[menu]=chosen.value
                        row.update({menu+"_eligible":len(options),menu+"_useful_sites":len(selected),
                            menu+"_max_gain":rounded(chosen.value),menu+"_priority_gain":rounded(math.fsum(
                                data.weights[o] for o in members if data.severe[g,o])),
                            menu+"_gain_pct":rounded(chosen.value*100/denom) if denom else "",
                            menu+"_at_most_plan":plan_id(selected),menu+"_exact_k_feasible":exact,
                            menu+"_exact_k_plan":plan_id(exact_set) if exact else ""})
                        if sid=="ALL16":
                            for c in cases:
                                gained=math.fsum(data.weights[o] for o in members if o[0]==c)
                                breakdown.append(dict(registry=g,function=f,menu=menu,site_budget=k,plan_id=plan_id(selected),
                                                      case_id=c,circle=data.names[c],newly_covered=rounded(gained)))
                    diff=values["expanded"]-values["inside_only"]
                    assert diff >= -TOL
                    equal=k<=len(avail["inside_only"]) and k<=len(avail["expanded"])
                    row.update(same_number_feasible=equal,equal_number_gain=rounded(max(0,diff)) if equal else "",
                               at_most_budget_gain=rounded(max(0,diff)),
                               equal_number_gain_pp=rounded(100*max(0,diff)/denom) if equal and denom else "")
                    comparisons.append(row)
    for pid,ids in sorted(manifest.items()):
        for site in ids:
            n,a,x,y,t=data.info[site]
            details.append(dict(plan_id=pid,site_id=site,site_name=n,site_address=a,lon_wgs84=x,lat_wgs84=y,pool_type=t))
    out.mkdir(parents=True,exist_ok=True)
    for filename,records in [("siting_budget_comparison.csv",comparisons),("siting_targets.csv",targets),
                             ("siting_ceiling.csv",benchmark),("siting_plan_sites.csv",details),
                             ("siting_pooled_distribution.csv",breakdown)]:
        save(out/filename,records)
    main_rows=[r for r in comparisons if r["registry"]=="strict"]
    cn_budget=[]
    for r in main_rows:
        a,b=r["inside_only_exact_k_plan"],r["expanded_exact_k_plan"]
        cn_budget.append({"范围":r["circle"],"功能":FUNCTIONS[r["function"]],"新增点数":r["site_budget"],
            "原未覆盖人数":r["initial_uncovered"],"两种范围均可恰开这些点":"是" if r["same_number_feasible"] else "否",
            "原站范围最多新增人数（预算内）":r["inside_only_max_gain"],"含站外最多新增人数（预算内）":r["expanded_max_gain"],
            "同点数多覆盖人数":r["equal_number_gain"],"同点数多覆盖百分点":r["equal_number_gain_pp"],
            "原站范围恰开这些点的方案":locations(manifest.get(a,())),
            "含站外恰开这些点的方案":locations(manifest.get(b,())),
            "原站范围可达到同一收益的有效点数":r["inside_only_useful_sites"],
            "含站外可达到同一收益的有效点数":r["expanded_useful_sites"]})
    save(out/"同点数新增覆盖与点位.csv",cn_budget)
    cn_targets=[]
    for r in targets:
        if r["registry"]!="strict":continue
        cn_targets.append({"范围":r["circle"],"功能":FUNCTIONS[r["function"]],
            "候选范围":"仅原养老站" if r["menu"]=="inside_only" else "原养老站及站外",
            "原未覆盖人口目标百分比":r["target_percent"],"原未覆盖人数":r["initial_uncovered"],
            "分母状态":"现状已全覆盖" if r["initial_uncovered_status"]=="no_deficit" else "有未覆盖人口",
            "目标人数":r["target_people"],"可达目标":"是" if r["status"]=="achieved" else "否",
            "最少新增点数":r["minimum_sites"],"该最少点数最多新增人数":r["coverage_at_minimum"],
            "本候选范围上限":r["max_attainable_people"],"未达目标的人数缺口":r["shortfall_to_target"],
            "一套最优方案的名称和地址":r["locations"],"方案编号":r["plan_id"]})
    save(out/"覆盖目标最少点数与地址.csv",cn_targets)
    checks={"circles":len(data.cases),"population_origins":len(data.weights),"target_percents":TARGET_PCTS,
            "budget_rows":len(comparisons),"target_rows":len(targets),"saturation_rows":len(benchmark),
            "unique_representative_site_sets_including_all_budgets":len(manifest),
            "small_components_exact_enumeration":solver.enumerated,"larger_components_HiGHS_optimal":solver.milp,
            "solver_gap_setting":0.,"scenarios":REGISTRIES,"total_population":math.fsum(data.weights.values()),
            "routing_input_sha256":{n:hashlib.sha256((source/n).read_bytes()).hexdigest()
                                     for n in ["routing_candidates.csv","routing_gains.csv","routing_origins.csv"]}}
    (out/"siting_checks.json").write_text(json.dumps(checks,indent=2)+"\n")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--routing",type=Path,default=TABLES)
    parser.add_argument("--output",type=Path,default=TABLES)
    args=parser.parse_args()
    run(args.routing,args.output)
