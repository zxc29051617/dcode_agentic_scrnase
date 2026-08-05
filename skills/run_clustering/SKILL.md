---
name: run_clustering
description: Leiden clustering on whichever embedding run_integration said to use, at a configurable resolution.
version: 0.2.0
status: implemented
---

# run_clustering

## Purpose
Partition cells into clusters via a neighbor graph and Leiden, on whichever
embedding `run_integration` produced. Same shape as `run_pca`: resolution 1.0
is Scanpy's own default, not a value borrowed from a specific tissue, so this
step does not stop for a decision — `resolution` is a config knob, not a
threshold the operator must supply before anything runs.

`resolution` is also, in practice, the number worth revisiting most: too low
merges biologically distinct populations, too high splits one population into
noise, and there is no universal right answer for a new tissue.

## Reads `embedding_key`, not a hardcoded obsm name
`run_integration` records which embedding downstream steps should use —
`X_pca_harmony` if batches were corrected, `X_pca` if there was nothing to
correct. This step reads that field rather than assuming a name, so it is
correct whichever happened, without needing to know why.

## `flavor="igraph"`, not `sc.tl.leiden`'s own default
The default (`flavor="leidenalg"`) is Scanpy's original implementation;
`flavor="igraph"` is what Scanpy's own docs now recommend — faster, and
deterministic given `n_iterations` and a fixed seed, where the leidenalg path
is not. Requires `directed=False`, since Leiden's modularity optimization is
defined on undirected graphs.

## What it reports

| key | meaning |
|---|---|
| `clustering_summary.embedding_key` | which embedding was clustered |
| `clustering_summary.n_clusters`, `.cluster_sizes` | the partition found |
| `clustering_summary.smallest_cluster` | flagged in a note below 10 cells — may be noise rather than a population |

A resolution that produces only one cluster does not error — it may be a
genuine property of a homogeneous sample — but it is worth a note rather than
passing through silently.

## Failure modes
- no AnnData (`run_integration` did not run)
- the path does not exist
- the recorded `embedding_key` is not in `obsm`
- fewer than 2 cells
- the neighbor graph or Leiden fit itself errors

## Downstream routing
`run_umap`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object post-integration
(2,159 cells): correctly read `X_pca_harmony`, 15 clusters at the default
resolution, sizes from 11 to 431 cells, no warnings.
