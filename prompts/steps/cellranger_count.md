## For `cellranger_count` specifically

Cell Ranger aligned each library and called cells on it. Its own top-line table
is carried through per library, unchanged, in `libraries[].metrics_summary`,
with five of those columns lifted into `metrics.per_library` beside a
`disposition` saying whether this library was counted now or reused from an
earlier run whose reference was checked first.

### What to judge

Whether the alignment and the cell call are sound — and that is a question
about **how the numbers sit against each other**, not about any one of them
against a remembered figure.

Three pairings carry most of the meaning:

- **Fraction Reads in Cells against Estimated Number of Cells.** A low fraction
  means most reads came from barcodes not called as cells, which is ambient RNA
  or a cell call that missed. Above roughly 70% is healthy; well below it, say
  so and say which of the two readings the rest of the payload supports.
- **Mean Reads per Cell against Median Genes per Cell.** Deep sequencing that
  did not buy genes means the libraries are complexity-limited rather than
  under-sequenced, and sequencing them further will not help. Shallow
  sequencing with high gene counts is the opposite, and is good news.
- **Reads Mapped Confidently to Transcriptome against the rest.** A low value
  with everything else healthy points at the reference — wrong build, wrong
  species, or an annotation whose gene models do not match these reads. That is
  the one failure here that no downstream step can recover from.

Compare libraries against each other where there is more than one.
`metrics.n_libraries`, `n_counted` and `n_reused` say how many of each there
were, and two libraries of the same tissue that differ several-fold on any of
the pairings above is a finding regardless of whether either is acceptable
alone.

Read `chemistry` and `reference_genomes` as context for all of it: a v2 library
and a v3 library legitimately differ in median genes, and comparing them
without saying so reads as a defect that is not there.

### What the numbers cannot show

**Every value in `metrics_summary` is a string exactly as Cell Ranger wrote it
to CSV, not a number.** `Estimated Number of Cells` arrives as `"1,218"` with a
thousands separator and `Fraction Reads in Cells` as `"95.6%"` with its sign
attached. Nothing in this pipeline parses them. Quote them as they appear and
reason about them qualitatively; do not perform arithmetic that depends on
having read `"1,218"` as one thousand two hundred and eighteen, and never
report a computed figure that is not in the payload.

Three further limits:

- **A `disposition` of reused means these numbers are from an earlier count.**
  The step verified that the genome recorded inside the existing matrix matches
  the reference this run was told to use, and then skipped the work — so the
  metrics are real and are not new. They describe an alignment that happened
  before, which matters if anything about the reads has changed since.
- **There is no barcode-rank curve here.** `Estimated Number of Cells` is Cell
  Ranger's knee plus its EmptyDrops rescue, and this payload holds neither the
  curve nor the ambient profile behind it, so the call cannot be second-guessed
  from what you can see. `cell_calling_review` is where that is done, and only
  when the run takes the raw route — `matrix_kind_hint` says which route this
  is. `web_summary` holds the plot and is not readable from here.
- **Five columns are lifted, about twenty exist.** `metrics.per_library` is a
  selection; sequencing quality — the Q30 fractions, the intronic and
  antisense mapping rates — is in `libraries[].metrics_summary` only. If a
  mapping rate looks wrong, look there before attributing it to the reference.

### Worked examples

- 1,218 cells, 54,681 mean reads per cell, 3,201 median genes, 79.6% confidently
  mapped, 95.6% reads in cells — **pass, and this is the reference run.** Every
  pairing is consistent: deep coverage bought genes, and almost all reads landed
  in called cells.
- Fraction Reads in Cells around 40% with an estimated cell count in the tens of
  thousands — **a finding, and warn.** Both readings point the same way: ambient
  barcodes were called as cells. Quote both, and say the cell call rather than
  the sequencing is what to look at.
- Mean Reads per Cell above 100,000 with Median Genes per Cell near 900 —
  **warn.** The libraries are complexity-limited; more sequencing will not
  recover genes, and the cause is upstream of anything this pipeline can fix.
- Reads Mapped Confidently to Transcriptome near 30% with healthy Q30 values —
  **a finding, and the most serious one.** The reads are fine and the reference
  does not fit them. Name it as a reference problem, not a library problem.
- Two libraries whose `chemistry` differs, one at 1,640 median genes and one at
  3,201 — **state it, do not alarm.** That is the expected v2-versus-v3 gap, and
  it matters downstream only because one QC threshold will be applied to both.

### When to warn

Warn when a pairing above is internally inconsistent, when libraries of the
same tissue diverge without a chemistry difference to explain it, or when a
reused `disposition` means the numbers predate something that has since
changed. Pass when every library's readings agree with each other and with its
chemistry. Judge `fail` only where the step reported an error — a refused reuse,
a missing transcriptome, or `ready_to_count` false — because those are the cases
where no matrix reached the next step.

There is rarely anything to advise: `config.expected_cells` is the only knob
here, and Cell Ranger's own call is better than a guess unless the payload shows
it plainly missing. Leave `advice` empty otherwise.
