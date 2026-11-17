"""Independent check of the exported sites and coverage, without importing the optimizer."""
import argparse
import csv
import json
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/"outputs/长沙养老研究_第十九轮/tables"
POOLS = ("inside_only", "expanded")


def rows(path):
    with path.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))


def same(a,b):
    assert math.isclose(float(a),float(b),rel_tol=0.,abs_tol=1e-6), (a,b)


def verify(source, destination):
    actions=rows(source/"routing_candidates.csv")
    gains=rows(source/"routing_gains.csv")
    origins=rows(source/"routing_origins.csv")
    budgets=rows(destination/"siting_budget_comparison.csv")
    targets=rows(destination/"siting_targets.csv")
    ceilings=rows(destination/"siting_ceiling.csv")
    parts=rows(destination/"siting_pooled_distribution.csv")
    plans=defaultdict(set)
    meta={r["site_id"]:(r["site_name"],r["site_address"],r["lon_wgs84"],r["lat_wgs84"],r["pool_type"]) for r in actions}
    for r in rows(destination/"siting_plan_sites.csv"):
        p,s=r["plan_id"],r["site_id"]
        assert s not in plans[p] and s in meta
        assert (r["site_name"],r["site_address"],r["lon_wgs84"],r["lat_wgs84"],r["pool_type"])==meta[s]
        plans[p].add(s)
    caseids=sorted(set(r["case_id"] for r in origins))
    assert len(caseids)==16
    weights={(r["case_id"],r["origin_id"]):float(r["older_population"]) for r in origins}
    gain=defaultdict(set)
    eligible=defaultdict(set)
    den=defaultdict(float)
    severe={}
    for r in origins:
        key=(r["case_id"],r["origin_id"])
        severe[r["scenario"],key]=int(r["initial_deficit"])>=3
        for f in ("M","S"):
            if r["covered_"+f] in ("False","0"):
                den[r["scenario"],f,r["case_id"]]+=weights[key]
                den[r["scenario"],f,"ALL16"]+=weights[key]
    for r in actions:
        for scope in (r["case_id"],"ALL16"):
            idx=(r["scenario"],r["function"],scope)
            for p in POOLS:
                if p=="inside_only" and r["pool_type"]!="inside":continue
                eligible[idx+(p,)].add(r["site_id"])
    for r in gains:
        for scope in (r["case_id"],"ALL16"):
            idx=(r["scenario"],r["function"],scope)
            gain[idx+(r["site_id"],)].add((r["case_id"],r["origin_id"]))
    def union(idx,sites):
        return set().union(*(gain[idx+(s,)] for s in sites))
    def count(covered):
        return math.fsum(weights[o] for o in covered)
    direct={}
    checked_plans=0
    for r in budgets:
        idx=(r["registry"],r["function"],r["scope"])
        same(r["initial_uncovered"],den[idx])
        k=int(r["site_budget"])
        for p in POOLS:
            valid=eligible[idx+(p,)]
            same(r[p+"_eligible"],len(valid))
            for field in ("_at_most_plan","_exact_k_plan"):
                if field=="_exact_k_plan" and k>len(valid):
                    assert r[p+field]=="" and r[p+"_exact_k_feasible"]=="False"
                    continue
                selected=plans[r[p+field]]
                assert selected<=valid
                assert len(selected)==k if field=="_exact_k_plan" else len(selected)<=k
                covered=union(idx,selected)
                value=count(covered)
                same(value,r[p+"_max_gain"])
                if field=="_at_most_plan":
                    same(sum(weights[o] for o in covered if severe[idx[0],o]),r[p+"_priority_gain"])
                checked_plans+=1
            direct[idx+(p,k)]=float(r[p+"_max_gain"])
        expected=float(r["expanded_max_gain"])-float(r["inside_only_max_gain"])
        same(r["at_most_budget_gain"],expected)
        if r["same_number_feasible"]=="True":
            assert k<=len(eligible[idx+("inside_only",)])
            same(r["equal_number_gain"],expected)
        else:
            assert r["equal_number_gain"]==""
    small_subsets=0; two_site_checks=0
    for r in ceilings:
        idx=(r["registry"],r["function"],r["scope"]);p=r["menu"]
        eligible_sites=eligible[idx+(p,)]
        positive=sorted(s for s in eligible_sites if gain[idx+(s,)])
        all_covered=union(idx,eligible_sites)
        same(count(all_covered),r["attainable_population"])
        same(len(plans[r["saturation_plan_id"]]),r["minimum_sites_to_saturate"])
        assert union(idx,plans[r["saturation_plan_id"]])==all_covered
        # Unreduced independent enumeration covers small menus, including duplicate sites.
        if len(positive)<=12:
            best=0.
            for k in range(0,len(positive)+1):
                for group in combinations(positive,k):
                    val=count(union(idx,group));best=max(best,val);small_subsets+=1
                if idx+(p,k) in direct:
                    same(best,direct[idx+(p,k)])
        elif idx+(p,2) in direct:
            best=max(count(gain[idx+(a,)]|gain[idx+(b,)]) for a,b in combinations(positive,2))
            same(best,direct[idx+(p,2)])
            two_site_checks+=1
    for r in targets:
        idx=(r["registry"],r["function"],r["scope"]);p=r["menu"];pct=int(r["target_percent"])
        required=den[idx]*pct/100
        same(required,r["target_people"])
        max_cover=float(r["max_attainable_people"])
        if r["status"]=="unattainable":
            assert required>0 and (max_cover<required or pct==100) and r["plan_id"]=="" and r["minimum_sites"]==""
        else:
            k=int(r["minimum_sites"]);chosen=plans[r["plan_id"]]
            assert len(chosen)==k
            reached=count(union(idx,chosen))
            same(reached,r["coverage_at_minimum"])
            tol=min(1e-8,required*1e-12)
            assert reached+tol>=required and (k==0 or pct==100 or direct[idx+(p,k-1)]<required+tol)
            if required==0:
                assert r["initial_uncovered_status"]=="no_deficit" and k==0
            else:
                assert r["initial_uncovered_status"]=="positive" and k>=1 and reached>0
    summed=defaultdict(float)
    for r in parts:
        idx=(r["registry"],r["function"],"ALL16")
        picked=plans[r["plan_id"]];circle=r["case_id"]
        subset={key for key in union(idx,picked) if key[0]==circle}
        same(count(subset),r["newly_covered"])
        summed[idx+(r["menu"],int(r["site_budget"]))]+=count(subset)
    for key,val in summed.items():
        same(val,direct[key])
    # Cross-check the pooled frontiers via one citywide MILP on raw site columns.
    # This uses neither component decomposition, dominance elimination nor pattern aggregation.
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix
    direct_regional_milps=0
    for g in ("strict","wide"):
        for f in ("M","S"):
            idx=(g,f,"ALL16")
            pool="expanded"
            sites=sorted(eligible[idx+(pool,)])
            at_risk=sorted(union(idx,sites))
            if not sites or not at_risk:
                continue
            row_index={origin:i for i,origin in enumerate(at_risk)}
            m,n=len(at_risk),len(sites)
            row=[];col=[];data_values=[]
            for j,site in enumerate(sites):
                for origin_id in gain[idx+(site,)]:
                    row.append(row_index[origin_id]);col.append(j);data_values.append(-1.)
                row.append(m);col.append(j);data_values.append(1.)
            for i in range(m):
                row.append(i);col.append(n+i);data_values.append(1.)
            matrix=coo_matrix((data_values,(row,col)),shape=(m+1,n+m)).tocsc()
            cost=np.r_[np.zeros(n),[-weights[o] for o in at_risk]]
            for k in (5,10,17):
                if idx+(pool,k) not in direct: continue
                result=milp(cost,integrality=np.r_[np.ones(n),np.zeros(m)],bounds=Bounds(0.,1.),
                    constraints=LinearConstraint(matrix,-np.inf,np.r_[np.zeros(m),k]),
                    options={"mip_rel_gap":0.})
                assert result.status==0 and result.mip_gap<=1e-9
                same(-result.fun,direct[idx+(pool,k)])
                direct_regional_milps+=1
    report={"circles":len(caseids),"target_rows":len(targets),"unique_site_sets_with_positive_size":sum(bool(s) for s in plans.values()),
        "same_budget_rows":len(budgets),"independently_replayed_plans":checked_plans,"unreduced_subsets_independently_enumerated":small_subsets,
        "larger_menus_checked_by_unreduced_two_site_enumeration":two_site_checks,
        "pooled_solution_per_circle_contribution_rows_checked":len(parts),
        "unreduced_pooled_milps_checked_at_5_10_17_sites":direct_regional_milps}
    (destination/"siting_independent_check.json").write_text(json.dumps(report,indent=2)+"\n")
    print(report)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--routing",type=Path,default=OUT);p.add_argument("--output",type=Path,default=OUT)
    a=p.parse_args();verify(a.routing,a.output)
