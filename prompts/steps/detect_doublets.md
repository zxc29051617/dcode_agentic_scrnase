## For `detect_doublets` specifically

Scrublet scored every cell for being two cells in one droplet, per library, at
the rate that library's own loading implies. `doublets_removed` false means the
flags are recorded and nothing was dropped — a decision left to a person, not a
failure.

### What to judge

Whether the detected rate is consistent with what the loading predicts, **per
library rather than pooled**.

For each `per_sample` entry compare `pct_doublets` against `expected_rate`
(a fraction: 0.0077 is 0.77%). Roughly agreeing is the healthy case. Say which
way any discrepancy runs and what it implies:

- Detected far **below** expected — the threshold is likely too high, or the
  score distribution had no clear valley for Scrublet to cut at.
- Detected far **above** expected — either the library was overloaded beyond
  what `expected_rate_source` assumed, or heterotypic diversity is inflating
  the simulated scores.

Check `threshold_source`. `scrublet` means the cut was picked automatically
from the score histogram; an operator-supplied value means it was chosen
deliberately, and the two deserve different confidence.

### What the numbers cannot show

**Scrublet cannot see homotypic doublets — two cells of the same type — so
`pct_doublets` is a floor, never the true rate.** Two T cells in one droplet
produce a profile that looks like one T cell, and no simulation of transcriptome
pairs will separate it. A low detected rate is therefore not evidence of a clean
sample; it is evidence about the heterotypic fraction only. Never read a small
`pct_doublets` as confirmation that doublets are not a problem.

Two more limits of this payload:

- **`median_score` and `threshold_used` do not describe the gap between them.**
  Scrublet's automatic threshold is only trustworthy when the score histogram
  is bimodal, and bimodality is not reported here. A threshold far above the
  median is consistent with a clean valley and with an arbitrary cut into a
  single smooth mode.
- **Which cells were flagged is not here.** Doublets concentrated in one
  cluster mean something quite different from doublets spread evenly, and that
  distinction only becomes visible after clustering.

### Worked examples

- `pct_doublets` 0.99 against `expected_rate` 0.0077 — **agreement, pass.**
  0.99% detected where 0.77% was predicted is within what the loading table
  resolves.
- `pct_doublets` 0.15 against `expected_rate` 0.08 with `threshold_source`
  `scrublet` — **worth a warn.** Nearly double, and the threshold was automatic,
  so the cut deserves a look before anything is removed.
- `doublets_removed` false with 19 cells flagged — **not a fault.** The step
  reports and waits; the notes already say the flags can be used as a covariate
  instead of dropping cells.

### When to warn

Warn when a library's detected rate diverges materially from its expected rate,
when `assessed` is false for any library, or when an automatic threshold is
about to drive a removal. Pass when every library was assessed and each rate is
close to what its loading implies.
