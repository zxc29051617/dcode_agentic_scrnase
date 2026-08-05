---
name: annotate_cells
description: Name each cluster's cell type with CellTypist, and plot the confidence behind every label.
version: 0.2.0
status: implemented
---

# annotate_cells

## Purpose
Turn cluster numbers into cell type names using CellTypist — a set of logistic
regression models trained on large annotated reference atlases — and record how
confident the model was about each one.

## The model is a decision, not a default
CellTypist ships 61 models, each trained on a particular tissue and species.
Handing an immune model a mouse brain does not error: it confidently sorts
neurons into T cells and monocytes, and the run looks entirely successful. That
puts model choice in the same category as the species mismatch
`resolve_reference` refuses to guess at.

So with no `celltypist_model` in config this step **annotates nothing**. It
returns `annotation_state: needs_review`, writes no object, and reports the full
catalogue with descriptions under `evidence.models` — the same shape as
`apply_cell_qc_filter` reporting what each threshold would cost before applying
one. That catalogue is also precisely what an advisor model needs in order to
argue for one model over another.

## Expression is rebuilt at 10,000 counts, never reused from `X`
CellTypist requires log1p expression normalized to 10,000 counts per cell and
checks it (`classifier.py`):

```
⚠️ Warning: invalid expression matrix, expect ALL genes and log1p normalized
   expression to 10000 counts per cell. The prediction result may not be accurate
```

It then returns normal-looking predictions anyway. `normalize_hvg_prepare`
normalizes to *median* depth, which on the real PBMC object is **6,780** — so
passing `X` through would trip that warning and quietly degrade every label.

This step rebuilds expression from `layers["counts"]` at `target_sum=1e4` into a
throwaway object and leaves the mainline `X` untouched. This is the payoff for
`post_load_validate` insisting raw counts stay in a layer.

## Majority voting runs over our clusters, not CellTypist's own
CellTypist predicts per cell, then smooths those predictions by majority vote
within an over-clustering. Left alone it builds its own graph at resolution 5;
pointed at `obs["leiden"]` it votes within the clusters the rest of the pipeline
already uses. That yields one label per cluster, directly comparable to
`find_markers`' per-cluster table and to the UMAP the report shows — the
difference between an answer that can be defended and one that can only be
displayed.

Both are kept: `cell_type` is the cluster-level consensus, `cell_type_per_cell`
is the raw per-cell prediction.

## Two numbers worth more than the label
- **`median_conf_score`** — the model's own confidence. Below 0.5 raises a
  warning: there is a label, but not one to quote without checking markers.
- **`per_cell_consensus`** — what fraction of the cluster's cells individually
  agreed with the consensus label. A cluster can be confidently labelled and
  still be internally split; on the real PBMC object cluster 8 scored 0.95
  confidence but only 0.39 consensus, which is a cluster merging two
  populations rather than a bad label.

## Figures
For each embedding present (`X_umap`, `X_tsne`), one PNG with three panels:
cell type, cluster, and `conf_score`. The confidence panel is the point — a
label alone cannot be argued with, a label beside its confidence can.

## What it reports

| key | meaning |
|---|---|
| `annotation_state` | `annotated` / `needs_review` |
| `annotation_summary` | model used, label source, cell type counts, median confidence, `normalized_to` |
| `per_cluster` | per cluster: label, median confidence, per-cell consensus, runner-up type |
| `figure_paths` | `{"umap": ..., "tsne": ...}` |
| `evidence.models` | the catalogue, when no model was chosen or the chosen one failed |

## Failure modes
- no AnnData (`find_markers` did not run)
- the path does not exist
- no `layers["counts"]` (`post_load_validate` did not run)
- no cluster labels (`run_clustering` did not run)
- the named model cannot be loaded or downloaded — the catalogue is attached to the error

## Downstream routing
`build_report`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object (2,159 cells, 15 clusters)
with `Immune_All_Low.pkl`. CellTypist is trained on external reference data and
never sees `find_markers`' output, so its agreement is an independent check on
every upstream step — and it agreed on all 15:

| cluster | `find_markers` | CellTypist |
|---|---|---|
| 0, 1 | S100A8, S100A9, S100A12 | Classical monocytes |
| 3 | GNLY, NKG7, PRF1 | CD16+ NK cells |
| 5 | CD8B, NELL2 | Tcm/Naive cytotoxic T cells |
| 6 | ITM2C, XBP1 | Plasma cells |
| 7 | BANK1, MS4A1 | Memory B cells |
| 9 | KLRB1, SLC4A10 | MAIT cells |
| 10 | TCF4, PTPRS | pDC |
| 11 | FCGR3A, LST1 | Non-classical monocytes |
| 12 | CD79A, IGHM | Naive B cells |
| 13 | HLA-DRB5, HLA-DPA1 | DC2 |
| 14 | TUBB1, CAVIN2 | Megakaryocytes/platelets |

Median confidence 0.999 across all cells; 13 cell types; both figures written.
