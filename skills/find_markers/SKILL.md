---
name: find_markers
description: Rank the genes that distinguish each cluster from the rest, across every gene rather than only the HVGs.
version: 0.2.0
status: implemented
---

# find_markers

## Purpose
For each cluster, rank genes by how strongly they separate it from all other
cells, producing the evidence `annotate_cells` needs to name a cell type.

The first downstream step that reads expression (`X`) rather than an
embedding — everything from `run_pca` to `run_umap` worked on coordinates.

## Every gene is tested, not just the HVGs
`normalize_hvg_prepare` flagged highly variable genes without subsetting the
matrix, and this is the step that collects on that decision. A canonical
marker is not always among the top 2,000 most variable genes: a gene expressed
crisply in one small cluster and nowhere else can have modest variance across
the whole object. Restricting the test to HVGs would silently hide exactly the
genes annotation depends on most.

## `method="wilcoxon"`, not scanpy's default
`sc.tl.rank_genes_groups` defaults to a t-test; scanpy's own clustering
tutorial uses and recommends Wilcoxon, as a rank test makes no normality
assumption about log-normalized counts. `use_raw=False` is explicit — `.raw`
is never set in this pipeline, and `X` is the log-normalized matrix.

Both tests treat cells as independent replicates, which overstates
significance when the real unit of replication is the sample. That is an
accepted limitation of per-cluster marker ranking rather than something this
step can fix, and a reason to read the effect sizes (`logfoldchange`,
`pct_in_cluster` vs `pct_in_rest`) alongside the p-values rather than sorting
on significance alone.

## A one-cell cluster would take every other cluster down with it
scanpy raises `Could not calculate statistics for groups ... only contain one
sample`, and that failure aborts the *entire* call — one stray singleton means
no markers for any of the other clusters. Clusters below
`MIN_CELLS_PER_CLUSTER` (2, scanpy's technical floor) are excluded from
`groups=` before the call, with a warning naming them. This is a crash guard,
not a quality bar; `run_clustering` already flags clusters under 10 cells.

## The full table goes to disk, a summary goes to the state
Fifteen clusters over twenty thousand genes is a third of a million rows,
which has no business in an audit log or a judge's context. The returned dict
carries the top `n_genes_reported` (default 25) per cluster; the complete
ranking is written beside the AnnData as `markers.csv`. The same rule the
AnnData itself follows — large results travel as paths, summaries travel in
state.

## What it reports

| key | meaning |
|---|---|
| `marker_table_path` | CSV of every gene for every tested cluster |
| `top_markers` | per cluster: gene, logfoldchange, adjusted p, and the fraction of cells expressing it inside vs outside the cluster |
| `marker_summary.n_significant_per_cluster` | genes below adjusted p 0.05, one number per cluster |
| `marker_summary.clusters_excluded` | which clusters were too small to test, and their sizes |

A cluster with no gene below the threshold gets a note — it may be a split of
a single population rather than a distinct one.

## Failure modes
- no AnnData (`run_umap` did not run)
- the path does not exist
- no cluster labels in `obs` (`run_clustering` did not run)
- fewer than two clusters large enough to test
- the ranking itself errors

## Downstream routing
`annotate_cells`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object (2,159 cells, 15 clusters,
21,785 genes tested). The top markers are textbook PBMC populations, which is
the strongest end-to-end check in this pipeline so far — every earlier step
had to be right for these to come out:

| cluster | top markers | population |
|---|---|---|
| 0 | S100A8, S100A9, S100A12 | classical monocyte |
| 3 | GNLY, NKG7, PRF1 | NK |
| 5 | CD8B, NELL2 | CD8 T |
| 7 | BANK1, MS4A1 | B |
| 9 | KLRB1, SLC4A10 | MAIT |
| 10 | TCF4, PTPRS | pDC |
| 11 | FCGR3A, LST1 | CD16+ monocyte |
| 12 | CD79A, IGHM | B |
| 14 | TUBB1, CAVIN2 | platelet |

The expression fractions separate cleanly too — GNLY is detected in 99% of
cluster 3 and 8% of everything else. 326,775 rows written to disk, 25 per
cluster returned in state.
