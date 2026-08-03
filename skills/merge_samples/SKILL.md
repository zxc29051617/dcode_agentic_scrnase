---
name: merge_samples
description: Concatenate the loaded samples into one labelled AnnData — where per-sample work ends and the shared mainline begins.
version: 0.1.0
status: implemented
---

# merge_samples

## Purpose
Everything before this point is per-sample: Cell Ranger counts each library,
each matrix is classified and loaded on its own, and cell calling reads a
barcode-rank curve that belongs to one library. Everything after is one object —
QC, normalisation, clustering, and `run_integration`, which exists precisely to
correct the batch effect this step creates.

It runs even for a single sample. Downstream then never has to ask how many
there were, and the `sample` column is there either way.

## The two things that go silently wrong

**Barcodes repeat between samples.** `AAACCCAAGAAACACT-1` is a valid 10x barcode
in every library ever made. Concatenating without disambiguating merges cells
that have nothing to do with each other, and nothing downstream can tell. Every
barcode is suffixed with its sample.

**Gene sets can differ.** `anndata.concat` defaults to an inner join, so two
matrices counted against different references quietly become their
intersection. Merging the human and mouse test sets would leave **17 genes of
39,048** — and report success. This step refuses instead, naming both gene
counts and the size of the overlap.

## Input / Output

| in | |
|---|---|
| `artifacts.<producer>.adata_paths` | `{sample: path}` from `cell_calling_review`, `load_filtered_counts` or `load_raw_counts` |
| `artifacts.<producer>.adata_path` | a lone path is accepted as one sample |

| out | |
|---|---|
| `adata_path` | one AnnData, all samples, `obs["sample"]` set |
| `sample_key` | `"sample"` — the batch key `run_integration` will correct on, named once here so no downstream step has to guess |
| `per_sample` | cells, genes and source for each |
| `n_samples`, `n_cells`, `n_genes` | |

## Refusals
- the samples do not share a gene set
- they record different genomes
- a loaded matrix is missing, unreadable, or has no cells

## Notes it raises
Cell counts differing by more than tenfold: the largest sample will dominate
clustering unless integration accounts for it. Reported, not corrected — that is
`run_integration`'s job and the operator's decision.

## Downstream routing
`post_load_validate`.

## Verified against
`pbmc_1k_v2` and `pbmc_1k_v3`, both counted against the same T2T reference:

| | |
|---|---|
| merged | 1,015 + 1,218 = **2,233 cells** × 39,048 genes |
| barcodes | all unique after suffixing (`...-1-pbmc_1k_v2`) |
| labels | `sample` matches the per-library counts exactly |

And the guard, on the human and mouse matrices: refused, with
"39,048 genes / 33,696 genes; only 17 in common".
