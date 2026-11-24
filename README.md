# Walking access to meals and daytime activities for older residents in Changsha

Replication data and code for **From Spatial Vision to Implementation: Governance Rules, Siting Trade-offs, and Equity Commitments in Using Neighborhood Venues to Expand Older Adults' Walking Access to Meals and Daytime Activities in Changsha**.

Release **v2.1.0** accompanies `Changsha_Cities_Full_Manuscript_中文完整稿.docx`. The package covers 16 living circles, 5,959 residential origins and an estimated 40,068 residents aged 60 or older. The manuscript record identifies the reference Word file by SHA-256 and matches its ten embedded images to the published figure files. [Download v2.1.0](https://github.com/felix018jiang/changsha-cities-replication/releases/tag/v2.1.0) · [Manuscript and figure record](provenance/manuscript_version.json) · [Version history](CHANGELOG.md)

## New in the Chinese full manuscript

| Manuscript figure | Result at the 720 m walking threshold | Files |
|---|---|---|
| 4C–E · Meal access after selecting ten sites | Of 19,564.4 initially unserved residents, the plan reaches 2,537.1. Other eligible sites could reach another 1,645.2. The remaining 15,382.1 live beyond all eligible sites. The maps compare Jingwanzi and Tianding at the same scale. | [PDF](figures/fig4c-e_meal_plan_and_remaining_gaps.pdf) · [Manuscript image](figures/fig4c-e_meal_plan_and_remaining_gaps.png) |
| S2C–D · Straight-line and network access to activities | Of the same 20,101.6 initially unserved residents, 9,109.2 are within a 720 m radius of an eligible venue and 3,086.5 are within a 720 m walk. In Lituo the nearest unserved origin is 315 m from the centre by straight line and 1,210 m along the mapped route. | [PDF](figures/figS2c-d_activity_radius_and_network.pdf) · [Manuscript image](figures/figS2c-d_activity_radius_and_network.png) |

Both figures have results for all 16 circles and a population-weighted pooled row. The offline calculation uses frozen distances, including the longer Lituo routes. Redrawing the geographic panels uses the original spatial inputs. [Circle-level data](data/figure_data/circle_figure_data.csv) · [Methods and manuscript rounding note](REPRODUCIBILITY.md)

## Run the replication

From the extracted release directory, with Python 3.12 installed, run

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python verify_integrity.py
python replicate.py
```

Results are written to `reproduced/`. Use `python replicate.py --skip-figures` for a numerical run. To check a local copy of the reference manuscript as well, run `python check_manuscript.py --manuscript /path/to/document.docx`.

The full run compares 11,492 rows and 97,181 published cells in 15 result files. It also tests all 199 meal-site exclusions, 15 walking scenarios and five activity policies with 80 circle outcomes. [Validation report](validation/replication_report.json)

## Find data and code

| Component | Location |
|---|---|
| Frozen origin weights, walking distances, eligible venues, coverage curves, targets and selected sites | [Primary inputs and results](outputs/长沙养老研究_第十九轮/tables) · [Optimizer](work/round19/optimization/solve.py) |
| Candidate exclusions, walking speeds and circle-level activity guarantees | [Supplementary results](outputs/Cities_补充计算与图表/tables) · [Analysis](work/cities_revision_20261116/supplementary_analysis.py) |
| Meal gap decomposition, activity radius comparison and cross-boundary venues | [Numerical code](manuscript_results.py) · [Geographic map generator](upstream/make_diagnostic_maps.py) · [Frozen distance and boundary inputs](data) |
| Tables 1–3 and S1–S5, figures 1–5 and S1–S3 | [Manuscript tables](data/manuscript_tables) · [Figures](figures) · [Figure data](data/figure_data) |
| Candidate-specific gains and citywide context | [Site results](data/site_level_results.csv) · [400 historical circles](data/citywide_400_circle_access.csv) |
| Source acquisition, population allocation, venue screening and routing | [Source records](provenance/sources) · [Original analysis code](provenance/original_code) · [Upstream scripts](upstream) |

Table S2 uses stable site IDs and resident-circle IDs for the 7-, 3- and 2-site meal plans. The unpublished Word file and original registry and operator address compilations remain with the authors. The public files retain eligibility, weights, distances and plan membership. [Data dictionary](DATA_DICTIONARY.md) · [Source versions and access](DATA_SOURCES.md) · [Reuse terms](LICENSE.md) · [Data availability statement in Chinese and English](DATA_AVAILABILITY.md)
