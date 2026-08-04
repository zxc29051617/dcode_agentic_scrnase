---
name: apply_cell_qc_filter
description: Remove low-quality cells — with the operator choosing the thresholds, after seeing what each one costs.
version: 0.2.0
status: implemented
---

# apply_cell_qc_filter

## Purpose
`run_qc_metrics` measured. This is where a number becomes a cut, and cutting is
destructive: a cell removed here is gone from every plot, marker test and
cluster downstream.

So the same rule as `cell_calling_review`:

1. **Measure.** What each candidate threshold would cost, per criterion and per
   sample.
2. **Apply, only when told.** With no thresholds it filters nothing and stops at
   the human gate.

## There is no default threshold in this file
Published "standard" values — 200 genes, 20% mitochondrial — come from specific
tissues and specific protocols. Applying one silently to a different one is how
good cells get thrown away without anybody noticing.

Your own standard values belong in **config**, not in the code:

| | a default in the code | a value in config |
|---|---|---|
| who chose it | nobody, it just ran | you |
| in the audit log | no | yes |
| changing tissue | quietly wrong | visible, and you change it |

## The four cuts

| threshold | reads | |
|---|---|---|
| `min_genes` | `n_genes_by_counts` | drop cells below |
| `min_counts` | `total_counts` | drop cells below |
| `max_pct_mito` | `pct_counts_mt` | drop cells above |
| `max_pct_erythroid` | `pct_counts_erythroid` | drop cells above |

Each accepts a single value for every sample, or a mapping per sample
(`{"A": 500, "B": 200}`) — the same shape `cell_calling_review` uses, and for the
same reason: one number is rarely right for two libraries.

## A threshold on a metric that was not computed is an error
`run_qc_metrics` leaves `pct_counts_mt` out entirely when the species was
unresolved or no gene matched the prefix, rather than reporting a false 0. Given
`max_pct_mito` against a matrix with no such column, this step **fails** — passing
every cell would look identical to a filter that found nothing to remove.

## What it reports

| key | meaning |
|---|---|
| `filter_state` | `applied` / `needs_review` — the graph routes on this |
| `filter_summary.removed_by_criterion` | which cut did the work; **counts overlap**, since a cell can fail two |
| `per_sample` | before/after per library, so an uneven cut is visible |
| `evidence.distributions` | percentiles of every available metric |
| `evidence.preview` | cells removed at each candidate value, per criterion |

Two things it says out loud rather than doing quietly:

- more than half the cells removed — may be right for a contaminated sample, but
  never silent
- one sample keeping under a quarter of its cells — a threshold set for the whole
  run can be far harsher on one library than another

## Filtering cells, not genes
Genes with too few cells are dropped in `normalize_hvg_prepare`, where HVG
selection needs them gone anyway. Doing it here as well would let two steps each
silently shrink the feature space.

## Failure modes
- no AnnData (`run_qc_metrics` did not run)
- the matrix carries no QC columns at all
- a threshold names a metric that was never computed
- the thresholds remove every cell

## Downstream routing
`detect_doublets`. `needs_review` goes to the human gate, and the mainline cannot
be reached from there by accepting — there is no filtered object yet.

## Verified against
The merged real `pbmc_1k_v2` + `pbmc_1k_v3` object (2,233 cells). The evidence
table shows why a published default is dangerous here:

| `max_pct_mito` | cells removed |
|---|---|
| 5 | 1,220 (55%) |
| 10 | 180 (8%) |
| 15 | 72 (3%) |
| 20 | 50 (2%) |

The pooled median is 5.4%, so a "5%" cutoff removes over half the data — and
unevenly: `pbmc_1k_v3` keeps 139 of 1,218 cells while `pbmc_1k_v2` is barely
touched, because the two chemistries differ. `min_genes=200, max_pct_mito=15`
removes 74 cells (3.3%), attributed 26 to `min_genes` and 72 to `max_pct_mito`.
