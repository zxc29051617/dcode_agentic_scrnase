## For `find_markers` specifically

This step ranked every gene in each cluster against all the others and wrote the
full table to disk. What reaches you is a preview: the top few genes per cluster
out of the `n_genes_reported` the step kept, with the rest counted rather than
listed. Nothing was filtered — `alpha` is used only to count how many genes
clear it.

### What to judge

Whether each cluster's ranked genes **cohere as one population**, and whether
the clustering they came from looks like it split real groups.

For each entry in `top_markers`, read the genes together rather than one at a
time. A cluster whose leading genes are recognisably one lineage — cytotoxic,
myeloid, B-lineage — is a cluster that will annotate cleanly. A cluster whose
leading genes belong to two unrelated lineages is worth naming, because either
the resolution merged two populations or the cluster is doublet-driven.

Use the effect sizes, not the p-values, to say how distinct a cluster is.
`pct_in_cluster` against `pct_in_rest` is the sharpest single reading: a gene at
0.95 in the cluster and 0.04 outside it marks a population, while 0.62 against
0.48 does not, whatever its `pval_adj`. `logfoldchange` says how much louder,
and the two together say whether the cluster has an identity.

Then read `marker_summary.n_significant_per_cluster` across clusters. Uneven
counts are ordinary; a cluster at or near zero while its neighbours are in the
thousands is the finding, and the step's own notes already name those clusters.
Check `clusters_excluded` too — a cluster dropped for being too small never
entered the comparison at all.

### What the numbers cannot show

**Significance here is a function of cluster size, not of biological
distinctness.** A Wilcoxon test over thousands of cells returns adjusted
p-values near zero for genes with no meaningful separation, so a large
`n_significant_per_cluster` is partly a statement about `n_clusters_tested` and
how many cells each holds — which this payload does not give you. Never rank
clusters by their significant-gene count, and never read a high count as
evidence a cluster is real. The expression fractions are the part that carries
biology; the p-values only say the difference was not noise.

Three further limits:

- **You are reading a truncated list.** When `output_is_abridged` is present it
  says so outright: the preview holds the top few genes of each cluster, and a
  canonical marker sitting at rank 12 is absent from what you can see. Do not
  conclude a gene is missing from a cluster — conclude only about the genes in
  front of you, and say that the full ranking is in `marker_table_path`.
- **Every test is one cluster against all the rest pooled.** Two clusters of one
  cell type dilute each other's markers, because each appears in the other's
  reference group. Weak markers in both halves of a split population look like
  two weak clusters rather than one over-split one, and nothing here
  distinguishes those.
- **A gene name is not a cell type.** The payload carries no annotation and no
  tissue, so name the lineage a gene set suggests only where it is
  unambiguous, and leave the identification to `annotate_cells`.

### Worked examples

- A cluster led by `S100A8`, `S100A9`, `LYZ` at `pct_in_cluster` above 0.9 and
  `pct_in_rest` below 0.1 — **pass, and worth saying.** One coherent myeloid
  programme, cleanly separated. Quote one fraction pair.
- A cluster whose top genes mix a cytotoxic set with a B-lineage set at similar
  `logfoldchange` — **a finding, and warn.** Two lineages in one cluster point
  at an under-resolved split or doublets, and the next steps will annotate it
  with one confident label either way.
- One cluster with `n_significant_per_cluster` 0 while others are in the
  thousands — **warn.** Nothing distinguishes it from the rest of the object,
  which usually means it is a split of a neighbouring population. The step's
  notes name it; say what that implies for the resolution rather than repeating
  the count.
- `clusters_excluded` naming a cluster of one cell — **state it, do not alarm.**
  Ranking needs at least two cells per group and scanpy aborts the whole call
  otherwise, so excluding it is what let the other clusters be tested.

### When to warn

Warn when a cluster's leading genes do not cohere as one population, when a
cluster has no significant genes at all, or when clusters were excluded from
the comparison. Pass when every previewed cluster reads as a single programme
with separating expression fractions — including when the significant-gene
counts vary widely between clusters, which is expected.

There is usually nothing to advise here: the choice that would change this
result is `run_clustering`'s resolution, and it is that step's to suggest.
Leave `advice` empty unless the evidence points at a specific value.
