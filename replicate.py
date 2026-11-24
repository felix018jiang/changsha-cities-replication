"""Reproduce the Changsha Chinese full manuscript analyses from frozen distances."""
import argparse
import csv
import importlib.util
import json
import math
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from check_manuscript import main as check_manuscript
from manuscript_results import circle_results, supplement_tables

ROOT = Path(__file__).resolve().parent
PRIMARY = ROOT / 'outputs/长沙养老研究_第十九轮/tables'
SUPPLEMENT = ROOT / 'outputs/Cities_补充计算与图表/tables'

def module(name, relative):
    spec=importlib.util.spec_from_file_location(name, ROOT/relative)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj

def rows(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

def compare(reference, observed):
    """Compare every published result field, including stable site/plan membership."""
    left,right=rows(reference),rows(observed)
    if len(left)!=len(right): raise ValueError(f'Row count differs: {reference.name}')
    cells=0
    for i,(a,b) in enumerate(zip(left,right),2):
        for key,value in a.items():
            other=b.get(key)
            if value==other: cells+=1; continue
            try:
                equal=math.isclose(float(value),float(other),rel_tol=1e-10,abs_tol=1e-6)
            except (TypeError,ValueError): equal=False
            if not equal: raise ValueError(f'{reference.name}:{i}:{key}: {value!r} != {other!r}')
            cells+=1
    return dict(rows=len(left),compared_cells=cells)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reproduced')
    parser.add_argument('--skip-figures',action='store_true')
    args=parser.parse_args()
    destination=args.output.resolve()
    if destination==PRIMARY or destination==SUPPLEMENT:
        parser.error('Choose an output directory separate from archived results.')
    primary=destination/'primary';supp=destination/'supplementary'
    primary.mkdir(parents=True,exist_ok=True);supp.mkdir(parents=True,exist_ok=True)
    for name in ['routing_origins.csv','routing_candidates.csv','routing_gains.csv','routing_reachable_od.csv']:
        shutil.copy2(PRIMARY/name,primary/name)
    solver=module('solve','work/round19/optimization/solve.py')
    print('Recomputing both registries and all circle/pooled frontiers',flush=True)
    solver.run(primary,primary)
    independent=module('primary_verify','work/round19/optimization/verify.py')
    independent.verify(primary,primary)
    total=module('total_targets','work/round19/manuscript/total_targets.py')
    total.TABLES=primary;total.main()
    operator=module('operator_sensitivity','work/round19/manuscript/source_check.py')
    operator.TABLES=primary;operator.main()
    print('Recomputing removals, walking speeds and circle guarantees',flush=True)
    subprocess.run([sys.executable,str(ROOT/'work/cities_revision_20261116/supplementary_analysis.py'),
                    '--source',str(primary),'--output',str(supp)],cwd=ROOT,check=True)
    check=module('supplement_verify','work/cities_revision_20261116/verify_supplementary_analysis.py')
    check.FROZEN=primary;check.OUTPUT=supp
    verified=check.verify()
    comparisons={}
    for name in ['siting_budget_comparison.csv','siting_targets.csv','siting_total_targets.csv',
                 'siting_ceiling.csv','siting_plan_sites.csv','siting_pooled_distribution.csv',
                 'siting_operator_directory_sensitivity.csv']:
        comparisons[name]=compare(PRIMARY/name,primary/name)
    for name in ['activity_plans.csv','activity_circles.csv','walking_thresholds.csv','meal_site_removals.csv']:
        comparisons[name]=compare(SUPPLEMENT/name,supp/name)
    print('Recomputing map populations and supplementary manuscript tables', flush=True)
    circle_table, lituo_route = circle_results(primary)
    figures_data = destination / 'figure_data'
    figures_data.mkdir(exist_ok=True)
    circle_file = figures_data / 'circle_figure_data.csv'
    circle_table.to_csv(circle_file, index_label='case_id', float_format='%.7f')
    comparisons[circle_file.name] = compare(ROOT / 'data/figure_data' / circle_file.name, circle_file)
    tables_dir = destination / 'manuscript_tables'
    tables_dir.mkdir(exist_ok=True)
    for name, table in supplement_tables(primary, supp).items():
        table.to_csv(tables_dir / name, index=False, float_format='%.1f')
        comparisons[name] = compare(ROOT / 'data/manuscript_tables' / name, tables_dir / name)
    if not args.skip_figures:
        subprocess.run([sys.executable,str(ROOT/'work/cities_revision_20261116/plot_supplementary_analysis.py'),
                        '--tables',str(supp),'--output',str(destination/'figures')],cwd=ROOT,check=True)
        plot=module('frontier_plot','work/round19/optimization/plot_frontiers.py')
        plot.TABLES=primary;plot.ROUND=destination;plot.main()
    import numpy,scipy
    manuscript = check_manuscript(primary, supp, spatial=(circle_table, lituo_route))
    report=dict(status='passed',python=platform.python_version(),numpy=numpy.__version__,scipy=scipy.__version__,
                supplementary_independent_checks=verified,archived_result_comparisons=comparisons,
                manuscript_checks=manuscript)
    (destination/'replication_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__': main()
