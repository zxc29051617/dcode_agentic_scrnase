---
name: load_filtered_counts
description: Load a post-cell-calling matrix into AnnData for the Scanpy mainline.
version: 0.2.0
status: implemented
---

# load_filtered_counts

## Purpose
The short route into the mainline. Cell calling has already happened — by Cell
Ranger, or by `cell_calling_review` on this run — so there is nothing to decide
here, only to read and to record where the counts came from.

## Output

| key | meaning |
|---|---|
| `adata_path` | `<run_dir>/load_filtered_counts/adata.h5ad` |
| `source_state` | format, shape, and that cell calling was applied upstream |
| `metrics` | cells, genes, median UMI and genes per cell |
| `cell_calling_resolved` | always True |

## An empty barcode is reported, not repaired
A filtered matrix should contain no barcode with zero counts. If one is there,
the file was not filtered by anything and the upstream classification is wrong.
That is surfaced as a warning rather than quietly dropped, because silently
fixing it would hide a routing bug.

## Failure modes
- no matrix path from `count_matrix_classify`
- the path does not exist, or cannot be read as a matrix
- the matrix contains no barcodes

## Downstream routing
`run_qc_metrics`.
