# Changsha older-adult services: manuscript-v2 replication

Data and code accompanying **Using neighborhood venues to expand walking access to meals and daytime activities for older residents in Changsha**.

**Release: [v2.0.0](https://github.com/felix018jiang/changsha-cities-replication/releases/tag/v2.0.0).** This package follows `Changsha_Cities_Expanded_Full_Manuscript_v2.docx`, rather than earlier manuscript outlines. The reference Word file is identified by its SHA-256 in [manuscript_version.json](provenance/manuscript_version.json); the unpublished manuscript itself is not included.

The analysis combines the frozen round-19 siting experiment with the 16 November 2026 supplemental analysis. It covers 16 living circles, 5,959 weighted origins and approximately 40,068 estimated residents aged 60 or older. It includes both baseline registries, circle-specific and pooled coverage curves, inverse coverage targets, operator-directory sensitivity, all 199 individual meal-site exclusions, three walking speeds, and five activity-allocation policies.

## Reproduce the results

Use Python 3.12 and run these commands from this directory:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python replicate.py
```

The calculation runs offline after dependencies are installed. It writes recalculated tables, figures and `replication_report.json` to `reproduced/`. `--skip-figures` runs all numerical checks without rendering figures. The published [validation report](validation/replication_report.json) records a successful run against **11,456 archived result rows and 96,917 published cells**, plus independent mixed-integer checks of the five activity policies, 80 circle outcomes, 15 walking scenarios and 199 individual exclusions.

## Files

| Location | Contents |
|---|---|
| [Primary inputs and results](outputs/长沙养老研究_第十九轮/tables) | Origin population weights and existing-service distances, candidate eligibility, candidate–origin walking distances, incremental gains, budgets, targets, ceilings and plan membership |
| [Supplemental results](outputs/Cities_补充计算与图表/tables) | Site exclusions, walking speeds and activity-allocation comparisons used in manuscript v2 |
| [Site-level results](data/site_level_results.csv) | Candidate-specific origin counts and newly reached population under each registry and service |
| [Citywide context](data/citywide_400_circle_access.csv) | 400 historical circles, including 274 with residential population support and 126 with no evaluable denominator |
| [Manuscript tables](data/manuscript_tables) and [figure data](data/figure_data) | Main tables 1–3, supplementary tables S1 and S3, and source values for figures 3 and 4 |
| [Figures](figures) | Manuscript figures 1–5 and S1–S3 in PDF; regenerated numerical charts are written separately |
| [Main optimizer](work/round19/optimization/solve.py) | Exact component enumeration, coverage optimization and pooled allocation |
| [Supplemental analysis](work/cities_revision_20261116/supplementary_analysis.py) | Walking-speed tests, individual exclusions and circle-level guarantees |
| [Source provenance](provenance/sources) | Original source URLs, versions, retrieval dates, road queries and checksums |
| [Original analysis code](provenance/original_code) | Unmodified source copies for comparison with the public adapters |
| [Upstream reference code](upstream) | Acquisition, population allocation, venue screening and routing reference scripts; these require separately acquired source records and historical intermediates |

Full names, street addresses and precise candidate coordinates are omitted from the public tabular inputs. `site_id` values, their lexical ordering, full numerical distances, weights and eligibility are retained. They preserve the original representative solutions. Empty coordinate fields mean **withheld**, not zero or missing routing. Site identifiers can reveal a public source identifier; this is location redaction, not a claim of anonymization. The few English venue labels in the exclusion plot identify cases already discussed in the manuscript.

The public package reproduces the numerical siting and sensitivity analyses **from frozen walking distances**. It does not independently reconstruct the street graph or source-address matching, or regenerate geographic basemaps. Those steps require the original spatial inputs. Their source records, retrieval channels, restrictions and reference code are documented in [DATA_SOURCES.md](DATA_SOURCES.md) and [REPRODUCIBILITY.md](REPRODUCIBILITY.md). Table S2's original address column is not redistributed; the corresponding selected site IDs remain in the plan tables.

## Sources, access and reuse

Read [DATA_SOURCES.md](DATA_SOURCES.md) for exact source versions and dates, [DATA_DICTIONARY.md](DATA_DICTIONARY.md) for schemas, and [LICENSE.md](LICENSE.md) for component-specific terms. ASPECT and Fu's public facility dataset declare CC BY 4.0; OpenStreetMap data are © OpenStreetMap contributors and available under ODbL 1.0. No open redistribution license was established for the municipal/national registry and commercial operator address compilations, so their raw address databases are not included. This does not assert a verified prohibition on redistribution.

The ready-to-use manuscript statement is in [DATA_AVAILABILITY.md](DATA_AVAILABILITY.md). GitHub provides a versioned URL; **no DOI has been assigned to this repository**. The two source datasets retain their own DOIs.
