## For `cross_check_annotation` specifically

This step ran two independent annotators over the same clusters and deliberately
left them unreconciled. CellTypist learned from a labelled reference matrix; the
marker database matched this run's differential expression against a curated
gene list. Their vocabularies do not line up, and deciding whether two names
mean one cell type is not something arithmetic can do — so the step did not try.

### What to judge

For every cluster, compare `celltypist_label` against the first entry of
`database_candidates`, and place each pair in one of three groups:

- **one population under two names** — `CD16+ NK cells` and `CD56-dim natural
  killer cell`; `Non-classical monocytes` and `CD16 Monocyte`. Not a finding.
- **one name broader than the other** — `pDC` against a plain `Dendritic cell`.
  Worth a note, not an alarm: the coarser database entry has less to say, which
  is a limit of the reference rather than a conflict in the data.
- **two different cell types.** This is the finding. Name the cluster and quote
  both labels.

This reconciliation is your job here, and it outranks the numeric flags.

### What the numbers cannot show

**A cluster with an empty `flags` list is not thereby sound.** The flags test
`n_matched_genes` and `relative_margin`; they never read a name. A database that
is confident and confidently at odds with CellTypist produces a wide margin and
no flag at all, so the clearest disagreements arrive unflagged. Read every
cluster's pair, not only the flagged ones.

Two more things the scores cannot express:

- **What the sample can contain.** A blood database offers cell types a
  preparation may exclude — a PBMC gradient leaves granulocytes behind, so
  `Neutrophil` on a PBMC run is a property of the reference, not of the cells.
- **How long a marker list is.** The score divides by the cluster's matched
  genes, not by each cell type's own list, so a cell type with many loosely
  specific markers outscores one with few precise ones.

### Worked examples

- `CD16+ NK cells` against `CD56-dim natural killer cell`, no flags — **not a
  finding.** CD56-dim is the CD16-positive NK compartment; the two names
  describe one population.
- `Classical monocytes` at 0.99 confidence against `Neutrophil`, no flags,
  `n_matched_genes` 169 — **a finding, and the most important one.** Both are
  myeloid, but they are different cell types, and neutrophils are absent from a
  PBMC preparation. No numeric flag marks this cluster.
- `pDC` against `Dendritic cell`, `relative_margin` 0.892 — **a note.** The
  database's label is the coarser one; they do not conflict.

### When to warn

Judge the step `warn` when any pair names two different cell types, and say
which. Judge it `pass` when every pair is a synonym or a granularity
difference, **even if clusters carry `ambiguous` or `low_marker_evidence`
flags** — those describe the database's certainty, not a fault in this step's
work.
