## For `run_qc_metrics` specifically

This step only measures. It removes nothing — `apply_cell_qc_filter` does that,
next, using thresholds a person supplies. So low-quality cells being present
here is the expected state, not a fault, and saying "many low-quality cells
remain" describes the step working correctly.

### What to judge

Whether the operator can pick **one set of thresholds that suits every library
in this run**, from what is reported here.

Compare `per_sample` entries against each other, not just the pooled
`qc_metrics`. For each library look at `median_genes_per_cell`,
`median_umi_per_cell` and `median_pct_mito`, and say whether one global cutoff
would treat them alike. Where two libraries differ by more than roughly
two-fold on any of these, say which threshold is at risk and which library
would absorb the loss.

Also confirm the flags: `mito_computed` and `erythroid_computed` false means
those metrics are absent, and any threshold depending on them cannot be applied.

### What the numbers cannot show

**Every value here is a median or an extreme; the shape between them is not
reported.** `median_pct_mito` 5.4 with `max_pct_mito` 99.28 is consistent with
three dying cells and with four hundred — the payload cannot distinguish them,
and the cost of a mitochondrial threshold depends entirely on which it is. Do
not infer how many cells a cutoff would remove. `apply_cell_qc_filter` reports
that, per criterion, and it is the step where that question belongs.

Two more things absent from these numbers:

- **A low `min_genes_per_cell` is not a defect.** Nothing has been filtered
  yet. It is the floor of an unfiltered distribution, and its distance from the
  median says nothing about how many cells sit near it.
- **Whether a difference between libraries is technical or biological.**
  Different median gene counts can come from chemistry versions, sequencing
  depth, or genuinely different cell composition. The payload does not say
  which, so name the difference and its consequence for thresholding — do not
  attribute a cause.

### Worked examples

- `median_pct_mito` 3.13 in one library against 6.92 in another, pooled median
  5.4 — **worth a warn.** A single `max_pct_mito` sits at a different
  percentile in each library, so one absorbs most of the loss. Name both
  values.
- `median_genes_per_cell` 1640 against 3201 — **worth stating, not alarming on
  its own.** It is the expected v2-versus-v3 chemistry gap. It matters here
  only because one minimum-genes cutoff will be applied to both libraries.
- `max_pct_mito` 99.28 with a median of 5.4 — **not a finding by itself.** A
  long tail is what an unfiltered object looks like; the next step exists to
  cut it.

### When to warn

Warn when libraries differ enough that one threshold set cannot serve them all,
or when a metric the run will need was not computed. Pass when the libraries
are comparable and every metric is present — including when the distributions
have wide tails, which is what this step is supposed to find.
