---
name: detect_doublets
description: Score every cell for being two cells in one droplet, per library, at the rate its own loading implies.
version: 0.2.0
status: implemented
---

# detect_doublets

## Purpose
A doublet is two cells captured in one GEM. It looks like a real cell — good UMI
count, good gene count — so `apply_cell_qc_filter` cannot see it. Left in, it
becomes a cluster that expresses two lineages at once and gets annotated as a
"transitional" or "novel" cell type that does not exist.

## Per library, never pooled
Scrublet detects doublets by simulating them: it takes pairs of observed
transcriptomes, adds them, and asks which real cells look like the simulated
sums. Doublets form **inside one GEM well**, so pairing a cell from sample A with
a cell from sample B simulates an event that cannot happen. Every library is
scored on its own and gets its own threshold.

## The expected rate comes from the loading, not from a default
Scrublet's `expected_doublet_rate` defaults to 0.06 — the rate 10x publishes for
recovering about 8,000 cells. On a 1,200-cell library that searches for **seven
times** more doublets than the chemistry can produce.

10x's multiplet table runs from 0.4% at 500 cells to 7.6% at 10,000, near enough
linear that one slope covers it:

```
expected_rate = 0.00076% per cell recovered   (capped at 25%)
```

| cells recovered | rate used |
|---|---|
| 1,000 | 0.76% |
| 5,000 | 3.8% |
| 10,000 | 7.6% |

`expected_doublet_rate` in config overrides it — a single value, or per sample
(`{"A": 0.05, "B": 0.01}`). Whichever was used is recorded as
`expected_rate_source`: `10x loading table` or `config`.

## Annotate by default, remove only when asked
Removal is destructive and the call is a probability, not a fact. So the default
is to **annotate and continue**:

| `obs` column | |
|---|---|
| `doublet_score` | Scrublet's score, NaN where a library could not be assessed |
| `predicted_doublet` | the call at the threshold used |
| `doublet_assessed` | whether this cell's library was scored at all |

`remove_doublets: true` drops the called cells. Either way `doublets_removed`
says which happened, and if they were kept a note says so out loud — cells
flagged and silently carried forward are how a doublet cluster reaches the
report.

## Why this step does not stop the pipeline
`cell_calling_review` and `apply_cell_qc_filter` stop without an operator
decision, because without one they have **no output** — no chosen cell set, no
filtered matrix. This step always has one: a fully annotated object. Whether to
also delete those cells is a separate question that `build_report` can still
answer honestly if nobody ever chooses.

## What it refuses
- **Removing every cell.** A threshold that calls 100% is a failed fit, not a
  finding, and the step errors rather than emptying the matrix.
- **Scoring a library too small to score.** Under 50 cells Scrublet has nothing
  to build a neighbour graph from. The sample is marked `assessed: false` with
  `doublet_score` left NaN — an honest gap beats a number nobody should trust.

It warns, but continues, when more than 30% of a library is called: possible in
a badly overloaded run, far more often a fit that did not converge.

## The PCA is bounded by the data
Scrublet runs a fixed 30-component PCA after keeping only the most variable 15%
of expressed genes. On a real library that is thousands of genes; on a small or
shallow one it can fall below 30, and arpack raises instead of returning a
smaller basis. `_components_for()` mirrors that filter and lowers the request to
what the matrix supports. It only ever lowers — both real PBMC libraries still
get all 30.

## What it reports

| key | meaning |
|---|---|
| `doublet_summary` | cells in, assessed, called, percentage, whether removed |
| `per_sample` | per library: cells, calls, threshold used, expected rate and its source |
| `doublets_removed` | `true` only if cells were actually dropped |
| `adata_path` | the annotated (or filtered) object |

## Failure modes
- no AnnData (`apply_cell_qc_filter` did not run)
- the path does not exist
- the threshold would remove every cell

## Downstream routing
`normalize_hvg_prepare`.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object (2,233 cells):

| library | called | rate | expected | threshold |
|---|---|---|---|---|
| `pbmc_1k_v2` | 11 / 1,015 | 1.08% | 0.77% | 0.032 |
| `pbmc_1k_v3` | 11 / 1,218 | 0.90% | 0.93% | 0.040 |

Both land near what the loading predicts. Scrublet's own 0.06 default would have
searched for roughly 134 doublets across the same object.
