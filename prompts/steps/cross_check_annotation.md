## For `cross_check_annotation` specifically

This step ran two independent annotators over the same clusters and deliberately
left them unreconciled. CellTypist learned from a labelled reference matrix; the
marker database matched this run's differential expression against a curated
gene list. Their vocabularies do not line up, and deciding whether two names
mean one cell type is not something arithmetic can do — so the step did not try.

That reconciliation is your job here, and it outranks the numeric flags.

For every cluster, compare `celltypist_label` against the first entry of
`database_candidates`, and place each pair in one of three groups:

- **one population under two names** — `CD16+ NK cells` and `CD56-dim natural
  killer cell`; `Non-classical monocytes` and `CD16 Monocyte`. Not a finding.
- **one name broader than the other** — `pDC` against a plain `Dendritic cell`.
  Worth a note, not an alarm: the coarser database entry has less to say, which
  is a limit of the reference rather than a conflict in the data.
- **two different cell types.** This is the finding. Name the cluster and quote
  both labels.

**A cluster with an empty `flags` list is not thereby sound.** The flags test
matched-gene counts and score margins; they never read a name. A database that
is confident and confidently at odds with CellTypist produces a wide margin and
no flag at all, so the clearest disagreements arrive unflagged. Read every
cluster's pair, not only the flagged ones.

Two things to weigh before calling a disagreement serious:

- **What the sample can contain.** A blood database offers cell types a
  preparation may exclude — a PBMC gradient leaves granulocytes behind, so
  `Neutrophil` on a PBMC run is a property of the reference, not of the cells.
- **How long the marker list is.** The score divides by the cluster's matched
  genes, not by each cell type's own list, so a cell type with many loosely
  specific markers outscores one with few precise ones. Where `n_matched_genes`
  is large and the winner is a broad myeloid or lymphoid category, say so.

Judge the step `warn` when any pair names two different cell types, and say
which. Judge it `pass` when every pair is a synonym or a granularity
difference, even if clusters carry `ambiguous` or `low_marker_evidence` flags —
those describe the database's certainty, not a fault in this step's work.
