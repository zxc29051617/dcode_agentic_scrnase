---
name: run_umap
description: 2D or 3D coordinates for visual inspection — UMAP, t-SNE, or both — with the choice left to config.
version: 0.3.0
status: implemented
---

# run_umap

## Purpose
Produce a 2D or 3D embedding for plotting. Same shape as `run_clustering`:
Scanpy's own defaults, not values borrowed from a specific tissue, so this step
does not stop for a decision. `config.method` picks the algorithm —
`"umap"` (default), `"tsne"`, or `"both"`; `config.dimensions` is `2` by
default and accepts `2`, `3`, or `[2, 3]`.

The existing 2D keys remain stable: `X_umap` and `X_tsne`. A 3D request is
stored separately as `X_umap_3d` and `X_tsne_3d`, so a frontend can switch
views without recomputing an embedding or changing the report's 2D contract.

## The two methods read different things, on purpose
- **UMAP** operates on the neighbor graph in `adata.uns["neighbors"]` — the
  same graph Leiden partitioned in `run_clustering`. This step requires that
  graph to already exist rather than rebuilding it, so the UMAP plot and the
  cluster labels are guaranteed to come from the same neighborhood structure.
- **t-SNE** does not use a neighbor graph; it embeds directly from
  `embedding_key` (`X_pca_harmony` if batches were corrected, `X_pca`
  otherwise — the same field `run_clustering` reads), so it never needs
  clustering to have run first. The 3D variant uses scikit-learn directly
  because the Scanpy t-SNE wrapper only exposes a 2D result.

Requesting `"tsne"` alone works on an object that never went through
`run_clustering`; requesting `"umap"` or `"both"` does not.

## Perplexity is bounded before the call, not after
`sklearn.manifold.TSNE` requires `0 < perplexity < n_samples` and raises
otherwise. Clamped here to `min(30, (n_obs - 1) // 3)` — scikit-learn's own
rule-of-thumb bound — with a warning naming both numbers. Below 30 cells, a
note says t-SNE structure is unreliable at that size regardless of the
perplexity used.

Two cells is the case the clamp cannot rescue: the floor of 2 is not below
`n_obs`, so it produced an illegal perplexity that only failed once sklearn
saw it. t-SNE now needs **at least 3 cells** and refuses below that, before
anything is embedded — on `method="both"` that matters, since the refusal used
to arrive after a UMAP had already been computed and stored.

A configured `perplexity` must be a finite number greater than zero. `0`,
negatives, NaN and infinity are refused rather than clamped: there is no
defensible value to substitute, and NaN in particular passes every `<`
comparison a clamp could make.

## What it reports

| key | meaning |
|---|---|
| `embedding_summary.computed` | which method/dimension combinations actually ran, e.g. `"umap"` and `"tsne_3d"` |
| `embedding_summary.embeddings` | frontend-friendly records containing `method`, `dimensions`, and AnnData `key` |
| `embedding_summary.embedding_keys` | method-to-dimension mapping, with `X_umap`, `X_tsne`, `X_umap_3d`, or `X_tsne_3d` |
| `embedding_summary.umap_key`, `.tsne_key` | legacy 2D keys when present, else `null` |
| `embedding_summary.umap_3d_key`, `.tsne_3d_key` | 3D keys when requested, else `null` |
| `embedding_summary.embedding_key` | which representation t-SNE read (or, for UMAP alone, which representation the neighbor graph was built on) |

## Failure modes
- no AnnData (`run_clustering` did not run)
- the path does not exist
- `method` is not one of `umap` / `tsne` / `both`
- `dimensions` is not `2`, `3`, or a non-empty combination of those values
- UMAP requested but no neighbor graph is present
- t-SNE requested but `embedding_key` is not in `obsm`
- t-SNE requested with fewer than 3 cells
- `perplexity` is configured but is not a finite number above zero
- fewer than 2 cells

## Downstream routing
`find_markers`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object post-clustering
(2,159 cells), `method="both"`, `dimensions=[2, 3]`: all four embeddings
computed cleanly, with `X_umap`/`X_tsne` shaped `(2159, 2)` and
`X_umap_3d`/`X_tsne_3d` shaped `(2159, 3)`, correctly reading
`X_pca_harmony` for t-SNE, no warnings.
