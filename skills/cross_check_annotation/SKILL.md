---
name: cross_check_annotation
description: Score the clusters a second time from a marker database and report where that disagrees with CellTypist.
version: 0.1.0
status: implemented
---

# cross_check_annotation

## Purpose
Annotate the same clusters by a completely different route — matching this
run's own differential-expression table against the curated scMayoMap marker
database — and report where the two methods part company. It changes no label.
`annotate_cells` still owns `obs["cell_type"]`.

## Why a second opinion is worth a step
CellTypist is a logistic regression trained on labelled reference cells. It
reads the expression matrix and cannot tell you which genes it keyed on.
scMayoMap looks up marker genes and never sees the matrix. Their blind spots
are unrelated, which is what makes agreement mean something.

On the PBMC test object the disagreement is real and instructive. CellTypist
calls clusters 0 and 1 classical monocytes at confidence 1.00 and 0.99; the
database calls both `Neutrophil`. Neutrophils are not in a PBMC preparation at
all — a Ficoll gradient leaves granulocytes in the pellet — and the reason is
recoverable from the database itself:

```
Neutrophil      84 markers          shared: CD14, IL1B, LYZ, LYZ1, S100A8, S100A9
CD14 Monocyte   12 markers

cluster 0 summed score:   Neutrophil 3,459   vs   CD14 Monocyte 3,164
```

The genes carrying that 9% lead are pan-myeloid, not neutrophil-specific:
ITGAM, ITGAX, CD33, TLR2, FCGR1A, CSF3R. Because the score divides by the
number of database-matched genes in the *cluster* rather than by the cell
type's own marker count, a cell type with a long, loose marker list outscores
one with a short, tight list. Row normalization does not fix this: it makes
clusters comparable, not cell types.

That is a property of the method, not a bug in this step, and it is exactly why
this step reports rather than decides.

## The tissue is a decision, not a default
Scored against all 28 tissues instead of `blood`, **14 of the 15 clusters change
their top hit** — to `skin:Macrophage`, `pancreas:T cell`, `lung:Club cell`,
`liver:Epithelial cell`. Only cluster 13 survives. The database cannot infer
the tissue, and the wrong one does not error: it returns confident, wrong
labels.

So with no `scmayomap_tissue` in config this step **compares nothing**. It
returns `cross_check_state: not_compared`, lists the 28 tissues under
`evidence.available_tissues`, and stops at the human gate — the same refusal
`annotate_cells` makes without a model.

## What it will not do: decide who is right
The two vocabularies do not line up.

| CellTypist | database | relationship |
|---|---|---|
| CD16+ NK cells | CD56-dim natural killer cell | same population, unlike strings |
| Non-classical monocytes | CD16 Monocyte | same population |
| pDC | Dendritic cell | different granularity |
| Classical monocytes | Neutrophil | genuinely different |

Resolving these needs either a synonym table — a third source of truth that
drifts away from both vocabularies — or a reader who knows the biology. So the
flags computed here are only the ones that need no vocabulary at all:

| flag | test | reads a name? |
|---|---|---|
| `low_marker_evidence` | fewer than 20 database-matched genes | no, a count |
| `ambiguous` | more than one candidate, or top-two relative margin < 0.10 | no, a comparison |
| `confidence_conflict` | ambiguous here while CellTypist was ≥ 0.90 sure | no, two numbers |

Both label strings go into the payload untouched, so whoever reconciles them
can be checked against the payload they were handed — which is not true of
biology invented from nothing.

Consequence worth stating plainly: the one genuine disagreement on the test
object, cluster 1, carries **no numeric flag**. It has 169 matched genes and a
0.435 margin — the database is confident, and confidently at odds with
CellTypist. Only a reader comparing `Classical monocytes` with `Neutrophil`
catches it.

### Handing that to the judge took an instruction, not just a payload

This was built assuming the judge would make the comparison because both
strings were in front of it. Measured against the real endpoint, it does not:

| arm | runs finding the disagreement |
|---|---|
| payload as first built | **0 / 3** |
| payload plus an explicit `label_pairs` field and a warning posing the question | **0 / 3** |
| payload as first built, plus `prompts/steps/cross_check_annotation.md` | **3 / 3** |

In the two failing arms the judge read the payload accurately and quoted the
flag counts back; it simply never compared the names, because the base prompt
asks whether a step ran soundly and by that measure this one did. Adding data
did not change that. Adding the instruction did, and the middle arm is what
rules out the data being the cause.

So the reconciliation lives in `prompts/steps/cross_check_annotation.md`, which
tells the judge to sort every pair into synonym, granularity difference, or
genuine disagreement, and warns it that an unflagged cluster is not thereby
sound. With it, the judge finds clusters 0 and 1 every time, and correctly
leaves `CD16+ NK cells` against `CD56-dim natural killer cell` alone.

## The algorithm
A port of `scMayoMap.R:88-142`. Filters follow the paper so the scores mean
what the published benchmark measured.

1. Keep marker rows with `pvals_adj <= 0.05` and `pct_nz_group >= 0.25`
2. Per gene: `score = (2^log2FC * pct.1) / pct.2`, with a zero `pct.2` replaced
   by the smallest non-zero one in the table
3. Multiply by the database's 0/1 indicator for each cell type
4. Per cluster, divide each cell type's total by the number of matched genes
5. Normalize each cluster's scores to sum to 1
6. Report the top *n*, where *n* is the largest jump in cumulative variance

The published formula reads `2^(l * p1 / p2)`; the code computes
`(2^l * p1) / p2`. They are different, and the code is what the benchmark ran,
so the code is what is ported. `cumvar` in R centres on a randomly chosen
element for numerical stability; variance is shift invariant, so centring on
the mean is equivalent and deterministic.

## The database
`marker_db/scmayomap/markers.csv` — 26,486 markers, 340 cell types, 28 tissues,
committed as plain text. `scripts/fetch_scmayomap_db.py` produced it from the
upstream `.rda` and records the source hash in `PROVENANCE.json`.

Converting once means the pipeline needs no `.rda` reader: this step imports
only the standard library. scMayoMap is MIT licensed; the citation is in
`PROVENANCE.json`.

## What it reports
| field | |
|---|---|
| `cross_check_state` | `compared`, `not_compared`, `unavailable` |
| `tissue` | which tissue was scored against |
| `per_cluster` | matched-gene count, candidates with scores, margin, both labels, flags |
| `flagged` | cluster to flag list, for the clusters with any |
| `cross_check_summary` | totals and a count per flag |
| `score_table_path` | every cell type's score for every cluster, as CSV |

## Failure modes
| | |
|---|---|
| no `scmayomap_tissue` | `not_compared`, tissues listed as evidence, gate |
| unknown tissue name | error listing the 28 valid names |
| `find_markers` did not run | error; there is nothing to score |
| `annotate_cells` produced no labels | scores reported, comparison columns empty |
| database file missing | error naming the script that writes it |

## Downstream routing
`human_review_decision`. This is the last mainline step, so a `warn` from its
judge is the last chance the run has to stop before the report is written.

## Verified against
The merged `pbmc_1k_v2` + `pbmc_1k_v3` object, 2,159 cells, 15 clusters. The
port reproduces the standalone reference implementation exactly. 5 clusters
flagged: 4 `ambiguous` (0, 3, 4, 5), 1 `low_marker_evidence` (cluster 6, which
reaches 0.85 on ten genes). `confidence_conflict` fires on the same four as
`ambiguous` here, because CellTypist's median confidence is ≥ 0.93 on every
cluster of this object — the flag separates the two methods only when
CellTypist is unsure, which never happens on this one.
