---
name: run_qc_metrics
description: Compute deterministic QC metrics — the first mainline step with real numbers for the judge to score.
version: 0.2.0
status: implemented
---

# run_qc_metrics

## Purpose
Everything before this point checked structure, identity and format. This
measures the biology: UMIs and genes detected per cell, and the mitochondrial /
erythroid fraction that `apply_cell_qc_filter` will threshold on.

**This step only measures.** No cell or gene is removed here, and no verdict
about "good" or "bad" is made — that split is the project's own rule. Threshold
decisions belong to `apply_cell_qc_filter` or the judge, not to a step whose job
is to report what is there.

## Input

| key | meaning |
|---|---|
| `artifacts.post_load_validate.adata_path` | the standardized AnnData |
| `artifacts.resolve_reference` / `artifacts.matrix_preflight` | whichever entry step ran; both emit `mito_prefix` and `erythroid_genes` from `species.constants_for` |

## Output

| key | meaning |
|---|---|
| `adata_path` | same AnnData, `obs` annotated with `n_genes_by_counts`, `total_counts`, `pct_counts_mt`, `pct_counts_erythroid` |
| `qc_metrics` | medians, min/max, and whether mito/erythroid were actually computed |
| `per_sample` | the same medians broken out by `obs["sample"]`, when present |
| `mito_computed`, `erythroid_computed` | `False` means absent, never a silent 0 |

## Absent is not zero
No mitochondrial prefix known, or a prefix that matches nothing in this
matrix — either way `mito_computed` is `False` and `median_pct_mito` is left out
of `qc_metrics` entirely, rather than reported as `0`. A run with unresolved
species and a run with genuinely no mitochondrial contamination must not look
identical; the first is a gap, the second is a finding.

## Why per-sample
`merge_samples` can put several libraries in one object. Their QC profiles can
differ — `pbmc_1k_v2` and `pbmc_1k_v3` in this project's own test data have
different median genes per cell (1,640 vs 3,201) from chemistry alone. Pooling
first would hide that difference inside a single threshold; the breakdown keeps
it visible before `apply_cell_qc_filter` has to choose one.

## Failure modes
- no AnnData path (`post_load_validate` did not run)
- the path does not exist, or cannot be read
- the matrix has no cells

## Downstream routing
`apply_cell_qc_filter`.

## Standalone

```bash
python skills/run_qc_metrics/run_qc_metrics.py <adata.h5ad> --run-dir <out> --species human
```

## Verified against
The real `pbmc_1k_v3` filtered matrix: **median 3,201 genes per cell**, matching
Cell Ranger's own `metrics_summary.csv` exactly — an independent check that the
scanpy computation and the upstream pipeline agree. On the merged
`pbmc_1k_v2` + `pbmc_1k_v3` object (2,233 cells), the per-sample breakdown
correctly separates the two chemistries' different QC profiles.
