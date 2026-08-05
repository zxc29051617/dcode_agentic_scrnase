# Report artifact contract

What `build_report` is allowed to read, what it must render, and what it must
say when something is missing. Written before `build_report` so that a figure
is never designed around evidence no step actually saved.

## Three tiers, three audiences

Conflating these is the usual mistake. They answer different questions for
different readers, and mixing them produces a report that is too detailed to
skim and too shallow to defend.

| Tier | Reader | Question | Where it would live in a paper |
|---|---|---|---|
| **1. Main results** | collaborator, PI, reviewer | what did we find | main figures |
| **2. QC / methods** | analyst, reviewer checking rigour | is it sound | supplementary figures, methods |
| **3. Pipeline audit** | you, six months later; an agent | who decided what, and can it be rerun | rarely published, but the reason this pipeline exists |

Tier 3 is what almost no published pipeline provides. It is not a nice-to-have
here: every `*_source` field, `chosen_by`, `requested` vs `used` pair and judge
verdict was recorded specifically so this tier can exist.

## Figure groups, not a figure count

A group is one figure with one or more panels. Groups are **conditional**: each
has a stated availability condition, and when it is not met the report says so
explicitly rather than omitting the section silently or failing.

> An absent figure with a stated reason is evidence. An absent figure with no
> explanation is indistinguishable from an oversight.

### Tier 1 — main report

| # | Group | Reads | Available when |
|---|---|---|---|
| M1 | Cell-retention funnel: barcodes → called cells → QC-passed → doublet-filtered → clustered | step summaries only | always |
| M2 | QC summary (genes, UMI, %mito), before vs after filtering | `run_qc_metrics/adata.h5ad` + `apply_cell_qc_filter/adata.h5ad` | QC metrics computed |
| M3 | Embedding by cell type, cluster, sample, confidence | final `adata.obsm[X_umap\|X_tsne]` | `run_umap` ran |
| M4 | Marker dotplot, top N per cluster | final `uns["rank_genes_groups"]` + `X` | `find_markers` ran |
| M5 | Cell-type composition per sample (stacked bar) | `obs["sample"]` × `obs["cell_type"]` | annotated **and** >1 sample |
| M6 | Annotation confidence and consensus per cluster | `annotate_cells.per_cluster` | annotated |

### Tier 2 — technical appendix

| # | Group | Reads | Available when |
|---|---|---|---|
| A1 | Barcode-rank curve with knee, inflection and chosen cutoff | `cell_calling_review` `evidence.rank_curve_path` (npz) | raw route only |
| A2 | Per-sample QC distributions | `run_qc_metrics/adata.h5ad` | always |
| A3 | QC filtering reasons: per-criterion counts, overlap, per-sample fail rate | `filter_summary.cell_flags_path` | thresholds applied |
| A4 | Doublet score distribution, threshold, doublets on the embedding | `obs["doublet_score"]` + `detect_doublets.per_sample` | doublets assessed |
| A5 | PCA elbow / cumulative variance; HVG mean–variance | `uns["pca"]`, `var["means"/"variances_norm"]` | `run_pca` ran |
| A6 | Integration diagnostic: embedding by sample, before vs after | `X_umap_unintegrated` and `X_umap` | integration ran on >1 batch |

**A6 wording is fixed by this contract**: it is an *integration diagnostic*. It
shows whether libraries mix; it cannot distinguish successful correction from
over-correction that has erased biological signal. Claiming it proves Harmony
worked would require batch-mixing and biological-conservation metrics
(iLISI/cLISI, silhouette, or an scIB-style panel), which this pipeline does not
compute. Do not call it proof.

### Tier 3 — audit

| # | Group | Reads | Available when |
|---|---|---|---|
| P1 | Decision table: every threshold, its value, and its source (`operator` / `config` / derived / default) | step summaries | always |
| P2 | Judge verdicts per step, with score and reasons | `state["judge_results"]` | always |
| P3 | Human decisions: gate, step, accept/revise/stop, rationale | `state["human_decisions"]` | always |
| P4 | Warnings and notes, grouped by step | step summaries | always |
| P5 | Reproducibility: run id, command, git commit + dirty, seed, package versions, reference and model hashes | `run_metadata.json` + `annotate_cells.model_sha256` | always |

## The ReportModel

`report.md` and `report.html` are two renderings of **one** intermediate
structure, never two independent assemblies of the same numbers. Two assemblies
drift: a field gets added to the HTML, the Markdown keeps reporting the old
value, and nothing catches it because both "work".

```
build_report
  ├── collect()  artifacts + run_metadata + state → ReportModel   (no plotting, no analysis)
  ├── src/plots.py  ReportModel → PNG/SVG paths                   (plotting only)
  └── render()   ReportModel + figure paths → report.md, report.html
```

Each group in the tables above becomes a section on the `ReportModel` carrying
its own `available: bool` and, when false, a `reason: str`.

## What build_report may not do

These are the boundary, not style preferences. Everything here was either
already gotten wrong once or is the reason a prerequisite step was changed.

1. **No analysis.** No embedding, clustering, neighbour graph, normalization or
   statistical test. Anything with a seed, a parameter or a package version
   behind it belongs to the step that owns it, so the figure describes the run
   it claims to. If a figure needs something not recorded, the fix is to record
   it upstream — not to compute it here.
2. **No re-reading of raw matrices.** The barcode-rank curve exists as an npz
   precisely so the 330,000-barcode matrix is never reloaded to draw it.
3. **No silent omission.** A group whose condition is unmet is rendered with
   its reason.
4. **No failing on missing evidence.** A run that stopped at `needs_review`
   before annotation still has QC, clustering and markers worth reading. The
   report describes how far the run got.
5. **No copying of large artifacts.** `markers.csv` (326,775 rows on the real
   object) is linked, not embedded.

## Figures are files

PNG or SVG written next to the report. `report.html` may inline them as data
URIs for a single self-contained file; `report.md` always references them by
relative path. `annotate_cells` already writes its own confidence figures —
`build_report` links those rather than redrawing them.

## Conditions on the real test object

The merged `pbmc_1k_v2` + `pbmc_1k_v3` run, as a worked example of which groups
appear:

| Condition | Value | Consequence |
|---|---|---|
| route | filtered matrix | **A1 unavailable** — no raw matrix, so no barcode-rank curve |
| samples | 2 | M5, A6 available |
| integration | ran (Harmony, 2 batches) | A6 available |
| thresholds | `min_genes=200, max_pct_mito=15` | A3 available |
| annotation | `Immune_All_Low.pkl` | M5, M6 available |
| embeddings | `X_umap`, `X_tsne`, `X_umap_unintegrated` | M3 renders both bases |

So on this object: 6 of 6 main groups, 5 of 6 appendix groups (A1 stated
unavailable), 5 of 5 audit groups.
