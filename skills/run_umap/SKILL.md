---
name: run_umap
description: 2D coordinates for visual inspection — UMAP, t-SNE, or both — with the choice left to config.
version: 0.2.0
status: implemented
---

# run_umap

## Purpose
Produce a 2D embedding for plotting. Same shape as `run_clustering`: Scanpy's
own defaults, not values borrowed from a specific tissue, so this step does
not stop for a decision. `config.method` picks the algorithm —
`"umap"` (default), `"tsne"`, or `"both"`.

## The two methods read different things, on purpose
- **UMAP** operates on the neighbor graph in `adata.uns["neighbors"]` — the
  same graph Leiden partitioned in `run_clustering`. This step requires that
  graph to already exist rather than rebuilding it, so the UMAP plot and the
  cluster labels are guaranteed to come from the same neighborhood structure.
- **t-SNE** does not use a neighbor graph; it embeds directly from
  `embedding_key` (`X_pca_harmony` if batches were corrected, `X_pca`
  otherwise — the same field `run_clustering` reads), so it never needs
  clustering to have run first.

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
| `embedding_summary.computed` | which of `["umap", "tsne"]` actually ran |
| `embedding_summary.umap_key`, `.tsne_key` | `"X_umap"` / `"X_tsne"` when present, else `null` |
| `embedding_summary.embedding_key` | which representation t-SNE read (or, for UMAP alone, which representation the neighbor graph was built on) |

## Failure modes
- no AnnData (`run_clustering` did not run)
- the path does not exist
- `method` is not one of `umap` / `tsne` / `both`
- UMAP requested but no neighbor graph is present
- t-SNE requested but `embedding_key` is not in `obsm`
- t-SNE requested with fewer than 3 cells
- `perplexity` is configured but is not a finite number above zero
- fewer than 2 cells

## Downstream routing
`find_markers`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object post-clustering
(2,159 cells), `method="both"`: both computed cleanly, `X_umap` and `X_tsne`
each shaped `(2159, 2)`, correctly reading `X_pca_harmony`, no warnings.
