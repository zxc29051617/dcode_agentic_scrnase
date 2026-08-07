---
name: human_review_decision
description: Assemble the run-level picture a person needs to sign off on the analysis, and say plainly what accepting would produce.
version: 0.2.0
status: implemented
---

# human_review_decision

## Purpose
The mainline gate (H2). It builds the question a person answers before the
report is written: what the analysis found, which values were chosen and by
whom, what is still uncertain, and what each of accept / revise / stop would
actually do.

## Why it exists as a step at all
There are two gates and they ask different questions.

| | `human_gate` (H1) | `human_review_decision` (H2) |
|---|---|---|
| asks | this one step warned — continue? | here is the analysis — publish it? |
| scope | one step | the whole run |
| built from | the last judge verdict | every step's recorded output |

Both used to be built the same way, from the last verdict. On a real run that
meant the final gate showed `annotate_cells`' warning about model choice
alongside a catalogue of 61 CellTypist models — 8,188 characters of it — and
asked a person to accept the entire analysis on that basis. It never mentioned
how many cells survived, what cell types were found, or which thresholds had
been applied.

## It reads, it does not compute
Deterministic, and reads only what earlier steps recorded — the same rule
`build_report` follows. Recomputing anything here would let the review
disagree with the report it precedes.

## It does not decide
`run()` returns the question. The gate node puts it to a person and records
the answer. The name is the decision this step *supports*, not one it makes.

## It does not block either
`apply_cell_qc_filter` and `cell_calling_review` refuse to pass an unresolved
choice downstream, because without it they have no output at all. This gate is
the opposite case: a run that stopped short still produced real QC, clustering
and markers, and a person may reasonably want that report. So an incomplete
analysis is stated as plainly as possible and the choice stays theirs:

```
report_would_be_missing: ["cell type annotation — the report will have
                          clusters but no cell types"]
```

## What it reports

| key | meaning |
|---|---|
| `findings` | cells analysed and removed, doublets, samples, clusters, cell types |
| `decisions_made` | every value that could have been different, with its source |
| `open_concerns` | warnings from **every** step, unmade choices, low-confidence clusters |
| `accepting_would` | what accept / revise / stop each do, and what the report would and would not contain |
| `metrics.analysis_complete` | whether anything is still unresolved |

Concerns are gathered across the whole run, not from the last step: the reason
to stop at a final gate is usually something that went past several steps ago.

## Failure modes
- no artifacts at all — nothing has run, so there is nothing to review

## Downstream routing
`build_report` on accept; the step the gate was reached from on revise; `END`
on stop.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` run:

```
findings         2,159 cells · 74 removed by QC · 19 doublets · 2 samples
                 integrated · 15 clusters · 13 cell types
decisions made   min_genes=200 (operator) · max_pct_mito=15 (operator)
                 doublet rate 0.0077/0.0087 (10x loading table)
                 resolution 1.0 · Immune_All_Low.pkl (operator)
open concerns    0
analysis_complete  True
```

And on a fixture run where no CellTypist model was chosen, the same gate
reports `analysis_complete: False` and names what the report would lack.
