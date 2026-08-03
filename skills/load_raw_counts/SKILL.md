---
name: load_raw_counts
description: Load a pre-cell-calling matrix into AnnData and measure the barcode-rank curve that cell calling needs.
version: 0.2.0
status: implemented
---

# load_raw_counts

## Purpose
Read every barcode the sequencer saw, and measure the barcode-rank curve so the
next step can decide which of them are cells. For `pbmc_1k_v3` that is 329,735
barcodes, about 1,200 of which are real.

## Output

| key | meaning |
|---|---|
| `adata_path` | `<run_dir>/load_raw_counts/adata.h5ad` — AnnData travels as a path |
| `source_state` | format and shape of what was read |
| `barcode_rank` | UMI at sampled ranks, cliff position, drop ratio |
| `cell_calling_resolved` | **always False** |

`cell_calling_resolved` is always False on purpose. A raw matrix has not been
through a cell caller, and reporting anything else would route 300,000 empty
droplets into the mainline.

## Behavior
- Reads 10x `.h5`, an mtx directory, or `.h5ad` through `src/matrix_io.py`.
- Warns when no barcode has zero counts — unusual for raw data, and a sign the
  upstream classification may be wrong.
- Warns when counts fall less than 10x across the cliff: the curve slopes rather
  than breaks, and no cutoff on it is obviously right.

## Failure modes
- no matrix path from `count_matrix_classify`
- the path does not exist, or cannot be read as a matrix
- the matrix contains no barcodes

## Downstream routing
`cell_calling_review`, always.
