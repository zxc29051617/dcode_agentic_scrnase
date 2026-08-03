---
name: cell_calling_review
description: Review which barcodes are real cells from the barcode-rank curve, the UMI distribution, and the knee/inflection points — with the operator, not instead of them.
version: 0.2.0
status: implemented
---

# cell_calling_review

## Purpose
Decide which barcodes are cells — and make that a decision someone *makes*
rather than one that happens to them.

Cell Ranger picks a number on its own: a knee on the barcode-rank curve, then an
EmptyDrops test that rescues low-UMI barcodes whose expression profile differs
from ambient RNA. That is a good default and a bad mandate. When the curve has no
sharp cliff, or the tissue is one where the algorithm is known to be
conservative, the number should be the operator's.

## The evidence it reviews
Three readings of the same curve, because they disagree and the disagreement is
the useful part:

| | what it is | on pbmc_1k_v3 |
|---|---|---|
| **knee** | where the curve bends hardest — furthest above the chord joining its ends | rank 901 @ 7,831 UMI |
| **inflection** | where it falls fastest — the steepest log-log slope | rank 1,198 @ 929 UMI |
| **UMI distribution** | counts at sampled ranks, and the drop across the cliff | 370x drop |

The knee keeps fewer, more confident cells; the inflection keeps more. Cell
Ranger called 1,218 here, just past the inflection. **901–1,218 is the range
the operator is actually choosing within**, and no algorithm can settle it.

## Two things, kept apart
1. **Measure.** Where the cliff is, how far counts fall across it, and what each
   candidate cell count would cost in UMIs.
2. **Apply, only when told.** With no instruction it resolves nothing and routes
   to the human gate. A cell count is not guessed on someone's behalf.

## Input

| key | meaning |
|---|---|
| `artifacts.load_raw_counts.adata_path` | the raw matrix, all barcodes |
| `artifacts.cellranger_count.libraries[]` | Cell Ranger's own call, to compare against |
| `config.force_cells` | keep the top N barcodes by UMI |
| `config.min_umi` | keep barcodes at or above a UMI count |

`force_cells` and `min_umi` are two ways to say the same thing; setting both is
an error rather than a silent precedence rule.

## Output

| key | meaning |
|---|---|
| `cell_calling_state` | `resolved` / `needs_review` — the graph routes on this |
| `adata_path` | the subset matrix, **only** when resolved |
| `n_cells`, `selection` | what was kept and by which rule, marked `chosen_by: operator` |
| `evidence.preview` | a table of candidate counts with their UMI thresholds |
| `evidence.knee_rank` / `inflection_rank` | the two candidate cutoffs, and their UMI |
| `evidence.cliff_drop_ratio` | how hard the curve breaks between them |
| `evidence.vs_cellranger` | shared / added / dropped barcodes, with their median UMI |

## What choosing a count gives up
`force_cells` is `--force-cells` semantics applied to the raw matrix already on
disk — seconds instead of a 20-minute recount, and identical, because
`--force-cells N` is itself the top N barcodes by UMI.

What it bypasses is **EmptyDrops**. On `pbmc_1k_v3` at N=1,218 the two agree on
1,206 barcodes; the other 12 are exactly where the ambient-profile test overruled
the ranking — 12 rescued at a median 635 UMI, 12 rejected at a median 1,194. The
step reports that comparison rather than presenting a bare number.

## Why the drop ratio and not a slope
A log-log slope depends on where in the searched range the cliff sits, so the
same curve scores differently for reasons that have nothing to do with the data.
The ratio of counts an octave either side is comparable between runs and means
something out loud: `pbmc_1k_v3` falls 370x across its cliff, a smoothly decaying
curve falls 5x.

## Failure modes
- no raw AnnData (`load_raw_counts` has not run)
- both `force_cells` and `min_umi` set
- a threshold that keeps no barcodes at all

## Downstream routing
`resolved` → `run_qc_metrics`. `needs_review` → the human gate, and the mainline
cannot be reached from there even by accepting: there is no subset matrix yet.

## Standalone

```bash
python skills/cell_calling_review/cell_calling_review.py <raw.h5ad> --run-dir <out>
python skills/cell_calling_review/cell_calling_review.py <raw.h5ad> --run-dir <out> \
  --force-cells 1500 --cellranger-filtered <filtered.h5>
```

## Verified against
`pbmc_1k_v3`. Cliff found at rank 1,198 (929 UMI, 370x drop) where Cell Ranger
called 1,218 cells — a 2% independent agreement. The candidate table makes the
choice legible:

| cells | UMI threshold | median UMI |
|---|---|---|
| 500 | 10,722 | 15,241 |
| 1,000 | 6,786 | 10,721 |
| **1,198 (cliff)** | **929** | **9,985** |
| 2,000 | 32 | 6,785 |
| 5,000 | 19 | 26 |

At 2,000 cells the 782 barcodes added over Cell Ranger's call have a median of
46 UMI — visibly ambient, and reported as such.
