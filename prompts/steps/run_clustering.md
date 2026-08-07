## For `run_clustering` specifically

Leiden clustering ran on the embedding an earlier step chose, at a resolution
from config. There is no ground truth for how many clusters is right, so the
question is not whether the count is correct — it is whether this clustering
can support the steps that come after it.

### What to judge

**First, that the clustering used the basis it should have.** Three fields
answer this together and none of them answers it alone:

- `integration_ran` — was a batch correction made at all
- `integration_recommended` — the embedding that correction produced
- `embedding_key` — what clustering actually used, with `embedding_source`
  saying where that came from

`integration_ran` true and `embedding_key` different from
`integration_recommended` is the defect: a batch effect was corrected and then
clustered around, so the clusters can be libraries wearing the costume of cell
types. It is invisible in every other number — fifteen clusters either way —
and it outranks anything about cluster sizes.

`integration_ran` false with `embedding_key` `X_pca` is correct, not a finding:
there was no batch to correct.

Then whether each cluster can carry the analysis downstream. `find_markers`
runs next and needs enough cells per group to rank genes at all. Read
`cluster_sizes` and say which clusters are too thin to produce a marker list
anyone would trust.

Finally, whether `resolution` and `n_clusters` are consistent with the number of
cells. Fifteen clusters from 2,000 cells is a different claim from fifteen
clusters from 200,000.

### What the numbers cannot show

**A small cluster is not evidence of over-clustering.** Real populations are
rare: pDCs are well under 1% of PBMC, plasma cells often fewer, and a cluster of
eleven cells in a two-thousand-cell run is the expected size for one. Size alone
cannot separate a genuine rare population from a fragment split off a larger
one — that needs the marker overlap between clusters, which `find_markers`
produces and this step does not have. Do not call a clustering over-split
because `smallest_cluster` is small.

`largest_cluster / smallest_cluster` carries the same problem, more strongly:
a wide ratio is normal in immune data, where monocytes and pDCs differ in
abundance by two orders of magnitude in the tissue itself.

Two further blind spots:

- **Whether a cluster is one population.** Nothing here measures within-cluster
  homogeneity. A cluster of 431 cells may be one cell type or three that the
  resolution failed to separate, and `cluster_sizes` looks identical either way.
- **Whether clusters follow sample boundaries.** Clusters that are each drawn
  from one library are an integration failure, not biology — but the
  composition per sample is not in this payload.

### Worked examples

- `integration_ran` true, `integration_recommended` `X_pca_harmony`,
  `embedding_key` `X_pca_harmony` — **correct basis, not a finding.**
- `integration_ran` true, `integration_recommended` `X_pca_harmony`,
  `embedding_key` `X_pca`, `embedding_source` `config override` — **warn, and
  say so first.** The correction was computed and then discarded.
- `smallest_cluster` 11 out of 2,159 cells across 15 clusters — **not a finding
  on its own.** That is 0.5%, the expected abundance of a rare dendritic or
  plasma cell population. Note it as one to confirm at annotation; do not warn.
- A cluster of 3 cells — **worth a warn**, but for a stated reason:
  `find_markers` cannot rank genes for a group that size, so the cluster will
  reach annotation with no evidence behind it.

### When to warn

Warn when `embedding_key` differs from `integration_recommended` while
`integration_ran` is true, or when a cluster is too small for the marker step
to say anything about it. Pass when the basis is right and every cluster can be
characterised — including when some are small, which in immune data is the
expected shape.
