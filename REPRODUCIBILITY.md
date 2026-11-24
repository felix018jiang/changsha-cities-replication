# Reproducing the Chinese full manuscript

Version 2.1.0 corresponds to the Chinese full manuscript dated 22 November 2026. The title, DOCX checksum and embedded figure checksums identify the reference exactly. Version 2.0.0 archives the preceding English manuscript analysis. [Manuscript record](provenance/manuscript_version.json) · [Previous release](https://github.com/felix018jiang/changsha-cities-replication/releases/tag/v2.0.0)

| Manuscript component | Calculation | Public results |
|---|---|---|
| Sections 2.1–2.4 and 3.1–3.3, Tables 1–3, S1 and S3 | Exact circle and pooled optimization, total coverage targets and operator-directory comparison | [Primary results](outputs/长沙养老研究_第十九轮/tables) · [Manuscript tables](data/manuscript_tables) |
| Figure 3 and Table S2 | Representative minimum-site meal plans at 5% of the initial gap, strict registry and 720 m | [Figure data](data/figure_data/fig3_meal_target_data.csv) · [Selected site and resident-circle IDs](data/manuscript_tables/tableS2.csv) |
| Figure 4A–B | Attribute gains from ten pooled meal or activity sites to the residents' home circles | [Circle gains](data/figure_data/fig4_spatial_distribution_data.csv) |
| Figure 4C–E | Partition initially unserved meal origins by reach of the selected ten sites and the other eligible sites | [Circle-level counts](data/figure_data/circle_figure_data.csv) |
| Section 3.1, cross-boundary reach | Join off-site eligibility and strict-registry gains with each venue's position relative to its resident circle | [Boundary flags](data/offsite_circle_membership.csv) |
| Figure 5 | Ten activity sites under station-only, expanded, seven-circle, eight-circle and circle-floor policies | [Plans and circle outcomes](outputs/Cities_补充计算与图表/tables) |
| Figure S1 | Four service-access measures on mapped residential land in 274 of the 400 historical circles | [Citywide numerators and denominators](data/citywide_400_circle_access.csv) |
| Figure S2A–B | Circle-specific optimal single-site gains for meals in Guanshaling and activities in Wangchengpo | [Table S1](data/manuscript_tables/tableS1.csv) · [Map](figures/figS2_one_site_locations.pdf) |
| Figure S2C–D | Compare a 720 m straight line and a 720 m walk to the 46 eligible activity venues for the same residents. Route the 20 Lituo origins within the radius on the full graph. | [Origin distances](data/activity_origin_distances.csv) · [Circle-level counts](data/figure_data/circle_figure_data.csv) |
| Figure S3 and Tables S4–S5 | Remove each of 199 meal candidates in turn. At 0.8, 0.7 and 0.6 m/s, rebuild the initial gap and 5% target separately. | [All scenarios](outputs/Cities_补充计算与图表/tables) · [Table S4](data/manuscript_tables/tableS4.csv) · [Table S5](data/manuscript_tables/tableS5.csv) |

## Numerical reproduction

`python replicate.py` recalculates both registries, all circle and pooled frontiers, total-population targets, operator sensitivity, candidate exclusions, walking speeds and circle-level activity policies. It reconstructs the two new figures' population counts from origin weights, candidate sets, distances and selected sites, then checks the tables against the manuscript. The 15 comparisons cover every published field in their reference CSVs. Numeric tolerance is 1e-6 absolute or 1e-10 relative. Stable site and plan IDs must match. The script also regenerates the numerical frontier and sensitivity charts. [Validation report](validation/replication_report.json)

In Figure 4E each initially unserved meal origin contributes its population to one category. First count origins reached by a selected site. Of the remainder, count those reached by another eligible site, then those beyond all candidates. In Figure S2D both percentages use the same strict-registry population without existing activity access. The pooled calculation sums weighted people across all 16 circles, including the four with no eligible venue.

**Manuscript rounding.** The share of the activity gap beyond every eligible venue is 84.6453198% using unrounded weights. Section 4.4 reports 84.65%. Direct rounding to one decimal gives 84.6%, while the supplied abstract prints 84.7%. The validation report retains the unrounded result. [Headline checks](validation/manuscript_checks.json)

The origin weights represent estimated residents aged 60+ in the 2020 population distribution. The 16-circle analysis uses whole-circle weights. The citywide context uses residents on mapped residential land. The 126 circles without a citywide denominator are unevaluable. Walking distances include both endpoint connectors. The general candidate OD table retains pairs through 720 m. Additional distances beyond 720 m are supplied for the Lituo illustration.

The activity floors protect the gained population count for each resident circle under its representative station-only plan. Individual beneficiaries can differ across plans. The dynamic program tests that no activity candidate reaches positive-weight initially unserved origins in multiple sampled circles. Independent pooled integer programs verify the five optima.

## Redraw the two new geographic figures

The public numerical workflow runs offline with the standard requirements. The geographic generator also needs the historical origin and candidate coordinates, circle polygons and walking graph. With the complete spatial archive and the replication environment active, run

```sh
python -m pip install geopandas==1.2.0 shapely==2.2.0 pyogrio==0.13.0 pyproj==3.8.0
python upstream/make_diagnostic_maps.py --project /path/to/spatial-archive --output reproduced/spatial
```

The generator reads the unredacted round-19 tables in `outputs/长沙养老研究_第十九轮/tables/`, the `exclusive_circles` layer in `work/round4/spatial/exclusive_circle_zones.gpkg` and the `walking_segments` layer in `work/round4/analysis/macro_network.gpkg`. It uses the routing code distributed under `upstream/work/round3/code/`. It writes two vector PDFs, two PNGs, the 17-row figure table and the two location-redacted distance and membership inputs. During release validation, both regenerated PNGs matched the Word image bytes and the regenerated figure table matched the archived CSV. [Map generator](upstream/make_diagnostic_maps.py) · [Spatial input provenance](provenance/spatial_inputs.json)

ASPECT's source metadata use persons per hectare. The authors' allocation code and district-total checks support the persons-per-native-cell interpretation used in the study. The upstream scripts preserve both unit readings, the original population allocation and the route-building procedure. Public solver adapters retain numerical distances, eligibility, site-ID ordering and tie-breaking while rendering stable IDs in place of addresses. [Population allocation](upstream/work/round13/population/build_weights.py) · [Source versions](DATA_SOURCES.md)
