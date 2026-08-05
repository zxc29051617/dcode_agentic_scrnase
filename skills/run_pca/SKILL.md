---
name: run_pca
description: Fit PCA on the prepared AnnData object, bounded by the data rather than by request.
version: 0.2.0
status: implemented
---

# run_pca

## Purpose
Reduce the HVG-selected expression matrix to a compact embedding
(`obsm["X_pca"]`) that `run_integration`, `run_clustering` and `run_umap` all
build on. Same shape as `normalize_hvg_prepare`: 50 components is a documented
default (Scanpy's and Seurat's own), not a value borrowed from a specific
tissue, so this step does not stop for a decision.

## Fits on HVGs, embeds and loads on all genes
`mask_var="highly_variable"` restricts which genes drive the fit, the same
reason `normalize_hvg_prepare` flags HVGs instead of subsetting the matrix
outright. The embedding still has a coordinate for every cell, and the
loadings (`varm["PCs"]`) still have a row for every gene — genes that were not
part of the fit just carry a zero loading rather than being absent.

If no `highly_variable` flag is present (`normalize_hvg_prepare` did not run,
or ran with HVG selection off), this step fits on every gene instead and says
so — it does not refuse, because a PCA on the full matrix is still usable, just
less clean.

## The component count is bounded before the call, not after a crash
PCA cannot return more components than `min(n_obs, n_vars_used) - 1`; the
`arpack` solver raises rather than silently truncating. The same instinct as
`detect_doublets`'s `_components_for`: derive the bound from the matrix in
front of it and clamp the request before calling scanpy, with a warning naming
both numbers.

## What it reports

| key | meaning |
|---|---|
| `pca_summary.n_comps` | components actually fit, after any clamp |
| `pca_summary.n_genes_used` | how many genes drove the fit |
| `pca_summary.used_highly_variable` | whether the HVG mask was applied |
| `pca_summary.variance_ratio` | leading per-component variance ratios, for the judge to see the elbow |
| `pca_summary.cumulative_variance_explained` | total variance captured by the kept components |

Below 20% cumulative variance explained, a note says the embedding may be
noisy for clustering — not an error, since that can be a genuine property of
the biology, but worth surfacing rather than passing through silently.

## Failure modes
- no AnnData (`normalize_hvg_prepare` did not run)
- the path does not exist
- fewer than 2 cells or fewer than 2 genes
- the rank bound is below 1 (nothing left to fit on)
- the PCA fit itself errors

## Downstream routing
`run_integration` — the graph's mainline is fixed (see `docs/graph.mmd`), so
every run passes through it; `run_integration` decides internally whether real
batch correction is needed for the samples present.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object after
`normalize_hvg_prepare` (2,159 cells, 21,785 genes, 2,000 flagged HVGs): fit
cleanly at the default 50 components on the 2,000 HVGs, no warnings, 60.5%
cumulative variance explained.
