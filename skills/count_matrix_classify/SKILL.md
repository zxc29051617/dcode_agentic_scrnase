---
name: count_matrix_classify
description: Classify count-matrix-like inputs into raw, filtered, or unknown so the workflow can route to the correct downstream branch.
version: 0.2.0
status: implemented
---

# count_matrix_classify

## Purpose
Decide whether a matrix is pre- or post-cell-calling, and decide it **from the
matrix**, not from what it is called.

Upstream steps supply hints — `ingest_validate` reads the Cell Ranger naming
convention, `cellranger_count` knows which file it just wrote. This step is the
router, so it is the one that has to be right: getting it wrong means either
running the mainline on 300,000 empty droplets or re-reviewing cell calling that
Cell Ranger already did.

## The evidence
**Empty barcodes settle it.** A raw matrix is the whole observed barcode list,
so it contains droplets with zero detected genes. A filtered matrix, by
construction, contains none.

The test is cheap in every format:

| format | how |
|---|---|
| 10x `.h5` | `diff(indptr)` — the per-barcode offsets. Never touches the counts. |
| mtx directory | the MatrixMarket header. When `nnz < n_barcodes`, some barcode must be empty (pigeonhole). |
| `.h5ad` | `obs/total_counts` or `n_genes` if already annotated; otherwise cell count alone. |

Falling back on the barcode count when emptiness is unavailable:

- `>= 100,000` → **raw**. No cell caller returns that many; the v3 whitelist alone is ~3M.
- `<= 50,000` → **filtered**. Deliberately far above a normal run (500–20,000) so a superloaded one is not misread.
- in between → **unknown**. The count alone cannot decide, and saying so is the honest answer.

## Hints are checked, never trusted
When a hint disagrees with the matrix, the run **stops**. That combination means
a file was renamed, moved, or pointed at by mistake, and routing on either
belief would be a guess. A hint that agrees is recorded as confirmation.

## Input

| key | meaning |
|---|---|
| `artifacts.cellranger_count.matrix_path` | preferred: the file just written |
| `artifacts.ingest_validate.matrix_path` | the matrix route's own detection |
| `input_bundle.paths` | last resort, for a standalone run |
| `*.matrix_kind_hint` | what upstream believes, cross-checked here |

## Output

| key | meaning |
|---|---|
| `matrix_class` | `raw` / `filtered` / `unknown` — the decision the graph branches on |
| `evidence` | barcode count, empty count, median genes, format, hint confirmation |
| `reasons` | why, in words, for the judge and the audit log |
| `needs_cell_calling` | `True` / `False` / **`None` when undecided** |
| `recommended_next_tool` | `load_raw_counts`, `load_filtered_counts`, or `human_review` |

`needs_cell_calling` is `None`, not `False`, when the class is unknown — "we do
not know" must never be read downstream as "cell calling is already done".

## Failure modes
- no matrix path from any upstream step
- the path does not exist, or the format is not a 10x matrix / h5ad
- an mtx directory missing `barcodes.tsv(.gz)` or `matrix.mtx(.gz)`
- **a hint that contradicts the matrix contents**

## Downstream routing
`raw` → `load_raw_counts` (and possibly `cell_calling_review`);
`filtered` → `load_filtered_counts`; `unknown` → the human gate.

## Standalone

```bash
python skills/count_matrix_classify/count_matrix_classify.py <matrix> [--hint raw|filtered]
```

## Verified against
The real Cell Ranger output for `pbmc_1k_v3`, both matrices:

| | barcodes | empty | median genes | verdict |
|---|---|---|---|---|
| `raw_feature_bc_matrix.h5` | 329,735 | 50,038 | 1 | **raw** |
| `filtered_feature_bc_matrix.h5` | 1,218 | 0 | 3,201 | **filtered** |

The filtered matrix's median of 3,201 matches Cell Ranger's own
`Median Genes per Cell` exactly, which is an independent check that the
`indptr` reading is correct.
