## For `apply_cell_qc_filter` specifically

This step costs out every candidate cut before making one, and makes none unless
it was told to. `filter_state` `needs_review` means no threshold was given, so
nothing was removed and the numbers you are reading describe an unfiltered
object — that is the step working, not failing. `applied` means a person chose,
and `thresholds.chosen_by` says so.

### What to judge

Whether the cut on the table can be defended against **this run's own
distribution**, rather than against a value from a paper.

Read `evidence.distributions` first: each criterion carries `percentiles` on a
0–100 scale for the percentage cuts. Then read `evidence.preview`, where each
row gives a `threshold` with the `cells_removed`, `pct_removed` and `cells_kept`
it would produce. Say what a specific value costs on this data, in cells.

Where `evidence.per_sample_distributions` is present, compare the libraries
against each other. One number applied to libraries whose medians differ
two-fold lands at a different percentile in each, and one library absorbs the
loss. Name both values when that is the case.

When `filter_state` is `applied`, judge the cut that was actually made:
`filter_summary.n_removed` against `n_before`, the per-criterion split in
`removed_by_criterion`, and `per_sample` for libraries that lost far more than
the rest.

### What the numbers cannot show

**Every `evidence.preview` row is that criterion applied alone, and the cuts
overlap.** The rows do not add up and were never meant to: 26 cells failing
`min_genes` and 72 failing `max_pct_mito` produced 74 removals on the real test
object, because 24 cells failed both. So `min_genes` independently cost 2 cells,
not 26 — and nothing in the preview says so. Only `filter_summary` reports it,
in `n_removed_by_more_than_one`, and only after a cut has been made. Never add
two preview rows together, and never present a single row's cost as the cost of
a threshold set.

Three further limits of this payload:

- **A percentile table hides the shape between its entries.** A median of 5.4
  with a maximum of 99.28 is consistent with three dying cells and with four
  hundred. `evidence.preview` is the only thing here that answers how many, and
  only at the values it happens to list.
- **How many cells a cut removes is not how much it costs.** Losing 3% spread
  across every cluster and losing 3% that are one population are the same
  number here. Composition is not in this payload — no clustering has run — so
  do not claim a cut is safe because the percentage is small.
- **`criteria_available` decides what can be judged at all.** A criterion
  missing from it has no column in the matrix, usually because the species went
  unresolved upstream, and a threshold naming it cannot be applied however
  reasonable the number is.

### Worked examples

- `max_pct_mito` 5 removing 1,220 of 2,233 cells (54.6%) where the pooled
  median is 5.4 — **a finding, and warn.** The published 5% figure sits below
  this run's median, so it cuts into the body of the distribution rather than
  its tail. Quote the median beside the count.
- `max_pct_mito` 15 removing 72 cells (3.2%) — **pass.** It clears the high
  tail and leaves the distribution intact. This is what a defensible cut looks
  like on this object.
- `filter_state` `needs_review` with all four entries in `criteria_available` —
  **warn, never fail.** The step measured everything it could and stopped for a
  decision that is the operator's. `needs_human_review` is true; the run cannot
  continue without a number, because there is no filtered object to hand on.
- One library keeping 95% and another 11% under the same `thresholds` —
  **warn even when the pooled `pct_removed` looks unremarkable.** Name the two
  libraries and their counts; a per-sample mapping is the shape of answer.

### When to warn

Warn when a threshold sits inside the body of a distribution rather than its
tail, when libraries would be treated unequally by one value, when
`filter_state` is `needs_review`, or when a criterion the run needs is absent
from `criteria_available`. Pass when a cut was applied that removes a tail this
run's own percentiles show to be a tail, and every library survives it
comparably.

Advise only inside the observed range: a `max_pct_mito` below this run's median,
or a `min_genes` above its 75th percentile, is a mistake rather than a
suggestion. Where the libraries disagree, advise per sample — a single number is
the wrong shape of answer.
