"""Invert the verified coverage curves for targets expressed over all residents."""
import csv
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT/'outputs/长沙养老研究_第十九轮/tables'
LEVELS = (50, 60, 70, 80, 90, 95, 100)


def rows(name):
    with (TABLES/name).open(encoding='utf-8-sig',newline='') as stream:
        return list(csv.DictReader(stream))


def save(name, records):
    with (TABLES/name).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main():
    weights=defaultdict(list)
    for r in rows('routing_origins.csv'):
        if r['scenario']=='strict':
            weights[r['case_id']].append(float(r['older_population']))
    population={case:math.fsum(w) for case,w in weights.items()}
    population['ALL16']=math.fsum(population.values())
    plans=defaultdict(list)
    for r in rows('siting_plan_sites.csv'):
        address=''  # Raw location fields are omitted from the public package.
        plans[r['plan_id']].append(f"{r['site_name']}（{address}）")
    curves=defaultdict(list)
    for r in rows('siting_budget_comparison.csv'):
        curves[r['registry'],r['function'],r['scope']].append(r)
    results=[]
    chinese=[]
    for (g,f,scope),curve in curves.items():
        curve.sort(key=lambda r:int(r['site_budget']))
        total=population[scope]
        unmet=float(curve[0]['initial_uncovered'])
        current=total-unmet
        for m,ml in [('inside_only','限养老站'),('expanded','养老站及站外场所')]:
            series=[float(r[m+'_max_gain']) for r in curve]
            assert all(v>=u-1e-8 for u,v in zip(series,series[1:]))
            for pct in LEVELS:
                required=total*pct/100
                j=next((j for j,gain in enumerate(series) if current+gain+1e-8>=required),None)
                hit=j is not None
                if hit:
                    row=curve[j]
                    k=int(row['site_budget'])
                    assert j==k and (j==0 or current+series[j-1]+1e-8<required)
                    pid=row[m+'_at_most_plan']
                    assert len(plans.get(pid,[]))==k
                else:
                    assert current+max(series)+1e-8<required
                    k=pid=''
                label=curve[0]['circle']
                status='already_achieved' if hit and k==0 else 'achieved' if hit else 'unattainable'
                end=(current+series[j])*100/total if hit else ''
                ceiling=(current+max(series))*100/total
                result=dict(registry=g,function=f,scope=scope,circle=label,menu=m,
                    target_percent=pct,total_population=round(total,8),baseline_covered=round(current,8),
                    baseline_covered_percent=round(current*100/total,8),status=status,minimum_sites=k,
                    newly_covered=series[j] if hit else '',coverage_at_minimum_pct=round(end,8) if hit else '',
                    ceiling_percent=round(ceiling,8),shortfall_people=round(max(0,required-current-max(series)),8),
                    plan_id=pid,locations='；'.join(plans.get(pid,[])))
                results.append(result)
                if g=='strict':
                    chinese.append(dict(zip(
                        ['范围','服务','场所范围','全体老人覆盖目标（%）','全圈老人','现状覆盖率（%）','能否达到','最少新增点','首次覆盖人数','方案总覆盖率（%）','全部候选下总覆盖率上限（%）','距目标仍缺人数','点位与地址'],
                        [label,{'M':'助餐','S':'日间活动'}[f],ml,pct,result['total_population'],result['baseline_covered_percent'],
                         {'already_achieved':'现状已达','achieved':'增设后可达','unattainable':'不可达'}[status],
                         k,result['newly_covered'],result['coverage_at_minimum_pct'],result['ceiling_percent'],result['shortfall_people'],result['locations']])))
    assert len(results)==2*2*17*2*len(LEVELS)
    save('siting_total_targets.csv',results)
    save('全体老人覆盖率目标与最少点位.csv',chinese)


if __name__=='__main__':
    main()
