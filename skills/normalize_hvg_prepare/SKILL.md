---
name: normalize_hvg_prepare
description: Normalize counts, log-transform them, and flag highly variable genes — with a defensible default, not a blocking gate.
version: 0.2.0
status: implemented
---

# normalize_hvg_prepare

## Purpose
Turn raw counts into something PCA can use: depth-normalized, log-transformed
values in `X`, and a `highly_variable` flag on the genes that carry the most
signal. Raw counts stay in `layers["counts"]`, untouched.

## Why this step does not stop for a decision
`apply_cell_qc_filter`'s thresholds are tissue- and protocol-specific — a
published "standard" value applied silently is how good cells get thrown away.
Normalizing to median depth and selecting ~2,000 HVGs is not that kind of
choice: it is a documented, widely used default (Scanpy's own, and Seurat's).
Same shape as `detect_doublets`: sensible defaults produce a usable result, and
config can override them, but nobody has to make a call before this step runs.

## Genes are filtered here, not in `apply_cell_qc_filter`
`apply_cell_qc_filter` filters cells only, on purpose (see its SKILL.md). A
gene detected in a handful of cells distorts the mean-variance fit HVG
selection relies on, so it is dropped here with `min_cells_per_gene`
(default 3, Scanpy's own default).

## HVG selection reads raw counts, and is per sample
`flavor="seurat_v3"` (the default) fits variance on `layers["counts"]`, which
Scanpy recommends over the classic `"seurat"` flavor for UMI data. When a
`sample` column carries more than one library, `batch_key="sample"` scores
variability within each library and combines the votes — the same instinct as
`detect_doublets` running per library: a gene that looks variable only because
two libraries differ in depth or chemistry is a batch artefact, not biology.

## seurat_v3 has a numerical floor, checked before the fit
Its loess fit (via scikit-misc) needs enough genes spread across
mean-expression bins to be stable. Below `MIN_GENES_FOR_SEURAT_V3` (50) the
same near-degenerate input was observed to fail two different ways: a clean
`ValueError` in isolation, and a hard interpreter crash inside the full test
suite — the same data, not always the same failure mode. That is not something
a `try/except` around the fit can be trusted to catch, so the gene count is
checked *before* calling it, and the step falls back to `flavor="seurat"`
(binned dispersion, no loess) with a warning naming both the requested and the
used flavor. On real data this floor is never close — thousands of genes
survive filtering — but a small custom gene panel (a targeted spatial panel,
say) can land under it for real, not only in a synthetic fixture.

## Nothing is subsetted
HVGs are flagged in `var["highly_variable"]`, not used to drop genes.
`run_pca` reads the flag to choose which genes drive the embedding;
`find_markers` and `annotate_cells` still need the full gene set, and
subsetting here would take that choice away from them before they exist.

## No scaling, no regression
Current Scanpy guidance drops `sc.pp.scale` and `sc.pp.regress_out` for
log-normalized UMI data — they change downstream clustering little while
adding parameters nobody sets deliberately. Not implemented here; a later
sample showing a real need (a strong cell-cycle signal) is a config addition
to make with evidence in front of it, not a default to carry now.

## What it reports

| key | meaning |
|---|---|
| `hvg_summary` | genes in/after filter, HVG count, flavor requested vs used, batch_key |
| `prep_summary` | normalization target and its source, whether scaled/subsetted |

## Failure modes
- no AnnData (`detect_doublets` did not run)
- the path does not exist
- no `layers["counts"]` (`post_load_validate` did not run)
- `min_cells_per_gene` leaves no genes
- HVG selection selects no genes

## Downstream routing
`run_pca`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object (2,159 cells after QC and
doublet steps): 39,048 → 21,785 genes after filtering, 2,000 HVGs selected with
`batch_key="sample"`, normalized to a median depth of 6,780, `layers["counts"]`
unchanged. The seurat_v3 fallback was found and verified on a fixture matrix
where filtering left only 6 genes — realistic for a small targeted panel, not
just a test artefact.
