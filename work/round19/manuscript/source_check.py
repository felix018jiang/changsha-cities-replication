"""Retain non-station meal candidates only when listed by their operator."""
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'work/round19/optimization'))
from solve import Data, CoverageSolver, read, save, TABLES


def main():
    data=Data(TABLES)
    solver=CoverageSolver(data.weights)
    brand={r['site_id'] for r in read(ROOT/'work/round19/venues/offsite_candidates.csv')
           if r['source_family']=='霸碗运营方门店目录' and r['existing_service']=='0'}
    result=[]
    for g in ('strict','wide'):
        for scope in ['ALL16',*data.cases]:
            cases=data.cases if scope=='ALL16' else [scope]
            choices=defaultdict(set)
            for case in cases:
                for site,origins in data.eligibility[g,'M',case].items():
                    if site in brand or data.info[site][4]=='inside':
                        choices[site].update(origins)
            curve=solver.frontier({s:frozenset(os) for s,os in choices.items()})
            unmet=math.fsum(w for o,w in data.weights.items() if o[0] in cases and not data.baseline[g,'M',o])
            for k in sorted({0,1,2,3,5,10,len(curve)-1}):
                plan=curve[min(k,len(curve)-1)]
                members=set.union(set(),*(choices[s] for s in plan.sites))
                assert abs(math.fsum(data.weights[o] for o in members)-plan.value)<1e-6
                result.append(dict(registry=g,scope=scope,site_budget=k,eligible=len(choices),
                    uncovered=unmet,within_budget_gain=plan.value,
                    ceiling=curve[-1].value,minimum_sites_to_ceiling=len(curve)-1,
                    selected_sites='|'.join(plan.sites)))
    save(TABLES/'siting_operator_directory_sensitivity.csv',result)


if __name__=='__main__':
    main()
