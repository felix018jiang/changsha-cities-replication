"""Check principal reported manuscript-v2 values against the public tables."""
import csv
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PRIMARY=ROOT/'outputs/长沙养老研究_第十九轮/tables'
SUPP=ROOT/'outputs/Cities_补充计算与图表/tables'

def rows(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def main():
    activity={r['scenario']:r for r in rows(SUPP/'activity_plans.csv')}
    values={k:float(v['newly_covered']) for k,v in activity.items()}
    assert round(values['circle_floors']-values['station_only'],1)==114.3
    assert round(values['expanded']-values['circle_floors'],1)==218.1
    assert [round(values[k],1) for k in ['station_only','expanded','circle_floors']]==[2522.5,2854.9,2636.8]
    walking=rows(SUPP/'walking_thresholds.csv')
    thresholds={str(d):[int(next(r['minimum_sites'] for r in walking if
                    r['service']=='meals' and r['menu']==m and int(r['threshold_m'])==d))
                    for m in ['stations','operator','all']] for d in [720,630,540]}
    assert thresholds=={'720':[7,3,2],'630':[5,3,2],'540':[8,4,3]}
    removed=[r for r in rows(SUPP/'meal_site_removals.csv') if r['removed_site_id']!='none']
    assert len(removed)==199 and sum(int(r['minimum_sites'])==2 for r in removed)==198
    assert next(int(r['minimum_sites']) for r in removed if r['removed_site_id']=='chain:bawan:142170042b7bd3')==4
    origins=[r for r in rows(PRIMARY/'routing_origins.csv') if r['scenario']=='strict']
    population=math.fsum(float(r['older_population']) for r in origins)
    assert len(origins)==5959 and round(population)==40068
    macro=[r for r in rows(ROOT/'data/citywide_400_circle_access.csv') if r['has_mapped_denominator']=='True']
    total=math.fsum(float(r['older_population']) for r in macro)
    percentages={k:math.fsum(float(r[k+'_people']) for r in macro)/total*100
                 for k in ['covered_H','covered_M','covered_S','covered_G','all_four','missing_3_or_4']}
    assert len(macro)==274
    assert [round(v,2) for v in percentages.values()]==[41.30,56.56,53.95,26.08,5.74,41.69]
    result=dict(status='passed',origins=len(origins),population=population,meal_minimum_sites=thresholds,
                activity_population=values,individual_exclusions=len(removed),
                citywide_supported_circles=len(macro),citywide_population=total,citywide_percentages=percentages)
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':main()
