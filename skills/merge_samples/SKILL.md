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
there were, and the `library_id` column is there either way.

## The design is written here, or it is nowhere
This is where a validated `--sample-manifest` reaches the cells. `sample_id`,
`donor_id`, `condition` and `technical_batch` are joined onto `obs` by
`library_id` — exact match, no positional fallback, since `study_design` was
already checked against the libraries this run found.

`obs["library_id"]` means the library and nothing else. It is **not** the key
`run_integration` corrects on: that is `technical_batch`, which exists only
because somebody declared it. Until this change the two were the same column,
so a study design was read off a FASTQ filename and Harmony removed whatever
the libraries differed by — see `docs/study_design.md`.

Values are written as categories with real nulls. A blank stays blank rather
than becoming the string `"unknown"`, which would be a value, and a value can
become a batch to correct on.

Merging several libraries with no manifest is not an error — the run still
produces QC, clustering and markers — but it warns, because donor, condition
and batch are then unknown for every cell and integration cannot run.

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
| `adata_path` | one AnnData, all libraries, `obs["library_id"]` set |
| `sample_key` | `"sample"` — kept as an alias of `library_id` for callers written before the two were told apart |
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
