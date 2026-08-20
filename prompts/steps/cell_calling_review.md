## For `cell_calling_review` specifically

This step read a barcode-rank curve and declined to pick a number from it.
`cell_calling_state` `needs_review` means no count was chosen, so nothing was
subset and the run stops here — that is the step working. `resolved` means a
person chose, and `selection.chosen_by` records that it was them.

### What to judge

Whether the curve supports the cutoff on the table, and how wide the defensible
range around it is.

The curve is described by two points that deliberately disagree.
`evidence.knee_rank` is where it bends hardest and keeps fewer, more confident
barcodes; `evidence.inflection_rank` is where counts fall fastest and keeps
more. **The gap between them is the range the operator is actually choosing
within**, so name both ranks and say how wide that range is rather than
treating either as the answer.

`evidence.cliff_drop_ratio` says how hard the curve breaks: counts an octave
either side of the cliff. A large ratio means cells and ambient droplets are
well separated and any cutoff in the range is defensible. A small one means
there is no cliff to find, which is precisely the case this step exists for —
say so, and say that the choice is therefore weakly constrained rather than
pretending the reported rank settles it.

Read `evidence.preview`: each row pairs a candidate cell count with the
`umi_threshold` that count implies and the `median_umi` of what it keeps. A
candidate whose `median_umi` collapses between one row and the next is
admitting ambient barcodes, and that is the number to quote.

When a selection was made, read `evidence.vs_cellranger`:
`added_by_this_selection` and `dropped_by_this_selection` with their
`median_umi_of_added` and `median_umi_of_dropped`. Barcodes added at a low
median UMI are the ones the operator took on.

### What the numbers cannot show

**Choosing a count bypasses EmptyDrops, and nothing in this payload measures
what that costs.** Cell Ranger does not rank alone — it tests low-UMI barcodes
against the ambient profile and rescues those whose expression differs. Taking
the top N barcodes is pure ranking, so a rescued barcode with modest counts and
a distinct profile is dropped, and an ambient barcode with high counts is kept.
`evidence.vs_cellranger` counts the disagreements and reports their median UMI;
it cannot tell you which side was right, because the expression profiles that
would decide it are not in this payload. Report the disagreement and its size.
Do not call either call correct.

Four further limits, and the first is the one most likely to mislead:

- **The top-level `evidence` block describes one library, not the run.** With
  more than one library the step reports each curve under its own key in
  `per_sample`, and lifts a single one — the first undecided library, or the
  first alphabetically once resolved — to the top level for convenience. Every
  curve is different, so judging the run from the top-level block alone means
  judging one library and reporting it as all of them. Read `per_sample` when
  it holds more than one entry, and name the libraries you read.
- **A rank is not a cell type.** The curve knows nothing about what is in the
  droplets, so a count that looks generous on the curve may be right for a
  tissue with genuinely low-RNA populations and wrong for one without. The
  payload does not name the tissue.
- **`cellranger_cells` may be absent, and its absence is not agreement.**
  `evidence.vs_cellranger_unavailable` means no library matched, so there is no
  second opinion here at all — which is weaker evidence than a comparison that
  found no differences.
- **`evidence.preview` stops at the candidates it lists.** It is a table of
  round numbers plus the cliff, not a continuous curve, so the best cutoff may
  sit between two rows. `evidence.rank_curve_path` holds the full vector; you
  cannot read it, and you should not imply a precision the table does not have.

### Worked examples

- `knee_rank` 901 and `inflection_rank` 1,198 with `cliff_drop_ratio` 370 —
  **a well-constrained curve.** Counts fall 370-fold across the cliff, so cells
  and ambient are cleanly separated and 901–1,218 is a narrow, defensible
  range. Any choice inside it is sound; say which end and why.
- `cliff_drop_ratio` near 5 — **warn.** That is a smoothly decaying curve with
  no real cliff, so the reported `inflection_rank` is the steepest point of
  something that is not steep. The number is not wrong, it is weakly
  determined, and the operator should be told that rather than handed a rank.
- A `evidence.preview` row at 2,000 cells whose `median_umi` is 46 against
  9,985 at the cliff — **a finding.** The 800 barcodes added past the cliff are
  visibly ambient. Quote both medians.
- `cell_calling_state` `needs_review` on a curve with a clear cliff — **warn,
  never fail.** The step measured the curve and stopped for a decision that is
  the operator's, and `needs_human_review` is true: without a count there is no
  subset matrix to hand downstream.

### When to warn

Warn when `cell_calling_state` is `needs_review`, when `cliff_drop_ratio` is
small enough that the curve does not determine a cutoff, when a chosen count
sits well past the cliff into low-`median_umi` territory, or when
`evidence.vs_cellranger` shows a substantial disagreement. Pass when a count
was chosen inside the knee-to-inflection range on a curve that breaks sharply,
and the comparison against Cell Ranger is small or absent for a stated reason.

Advise a concrete count only where the curve constrains one. `force_cells` and
`min_umi` are two ways of saying the same thing, so suggest one, not both, and
take the value from a row that is actually in `evidence.preview`. Where the
libraries have different curves, advise per sample.
