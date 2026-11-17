"""Independently reproduce the baseline and benefit sets from archived OD distances."""
from pathlib import Path
from collections import defaultdict
import hashlib
import json
import math

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUTPUT = ROOT/"outputs/长沙养老研究_第十九轮/tables"
ARCHIVE = ROOT/"outputs/长沙养老研究_第十八轮/tables/population_origin_support.csv"
LEDGER = ROOT/"work/round19/venues/offsite_candidates.csv"


def main():
    old = pd.read_csv(ARCHIVE).query("support=='native_whole_circle'")
    status = pd.read_csv(OUTPUT/"routing_origins.csv")
    actions = pd.read_csv(OUTPUT/"routing_candidates.csv")
    ledger = pd.read_csv(LEDGER)
    od = pd.read_csv(OUTPUT/"routing_reachable_od.csv")
    gains = pd.read_csv(OUTPUT/"routing_gains.csv")
    delta = pd.read_csv(OUTPUT/"routing_baseline_delta.csv")
    assert od.distance_m.le(720).all() and od.distance_m.ge(0).all()
    assert not od.duplicated(["case_id","origin_id","site_id"]).any()
    reached = defaultdict(set)
    for x in od.itertuples():
        reached[x.case_id,x.site_id].add(x.origin_id)
    weighted = old.query("scenario=='strict'").set_index("origin_id").older_population
    expected_links = set()
    expected_baseline_count = 0
    for (case,sc), rows in old.groupby(["case_id","scenario"]):
        local = set(rows.origin_id)
        for f in "MS":
            old_covered = set(rows.loc[rows["covered_"+f],"origin_id"])
            confirmed = set(ledger.loc[ledger.existing_service.eq(1) & ledger.function.eq(f)
                & ledger.scenario.isin(["both","all",sc]),"site_id"])
            new_covered = old_covered | set.union(set(),*(reached[case,s] for s in confirmed))
            out = status.loc[status.case_id.eq(case) & status.scenario.eq(sc)]
            assert new_covered == set(out.loc[out["covered_"+f],"origin_id"])
            change = delta.loc[delta.case_id.eq(case) & delta.scenario.eq(sc) & delta.function.eq(f)].iloc[0]
            assert change.origins_newly_in_baseline==len(new_covered-old_covered)
            assert abs(change.population_newly_in_baseline-math.fsum(weighted[o] for o in new_covered-old_covered)) < 1e-6
            eligible = set(actions.loc[actions.case_id.eq(case)&actions.scenario.eq(sc)&actions.function.eq(f),"site_id"])
            assert not confirmed.intersection(eligible)
            for site in eligible:
                for origin in reached[case,site].intersection(local-new_covered):
                    expected_links.add((case,sc,f,site,origin))
            expected_baseline_count += len(local)
    keys = ["case_id","scenario","function","site_id","origin_id"]
    assert not gains.duplicated(keys).any()
    assert expected_links==set(gains[keys].itertuples(index=False,name=None))
    assert (weighted.loc[gains.origin_id].to_numpy() == gains.older_population.to_numpy()).all()
    snap=json.loads((HERE/"routing_checks.json").read_text())
    assert snap["sha256_offsite_ledger"]==hashlib.sha256(LEDGER.read_bytes()).hexdigest()
    check = {"origin_registry_function_baseline_flags_checked":expected_baseline_count,
             "eligible_case_registry_function_site_rows":len(actions),
             "saved_site_origin_od_rows":len(od),
             "exact_incremental_benefit_memberships":len(expected_links),
             "unique_new_service_locations":actions.site_id.nunique(),
             "sha256_offsite_ledger":snap["sha256_offsite_ledger"]}
    (HERE/"saved_routes_independent_check.json").write_text(json.dumps(check,indent=2)+"\n")
    print(json.dumps(check,indent=2))


if __name__=="__main__":
    main()
