---
name: resolve_species
description: Resolve the species constants both routes need, from a table, without touching the filesystem.
version: 0.1.0
status: implemented
---

# resolve_species

## Why this is separate from `resolve_reference`
They answer different questions and only one applies everywhere:

| | needed by | is |
|---|---|---|
| species constants | **both** routes, down to annotation | a table lookup |
| Cell Ranger reference | the FASTQ route only | a 32 GB STAR index on disk |

Keeping them in one step meant a count-matrix run passed through something
called `resolve_reference` for a reference it never opens. This one runs before
the route splits; `resolve_reference` runs on the FASTQ branch after it.

Nothing here reads the filesystem, so nothing here can fail on a missing file.

## Input / Output

| in | |
|---|---|
| `config.species` | `human`, `mouse`, `小鼠`, `Mus musculus`, … |
| `config.mito_prefix`, `config.erythroid_genes` | override the table |

| out | |
|---|---|
| `species` | the canonical name, or `None` |
| `mito_prefix` | how `run_qc_metrics` finds mitochondrial genes |
| `erythroid_genes` | for `apply_cell_qc_filter` |
| `marker_db` | for annotation, `None` where PanglaoDB has no coverage |
| `constants_source` | `config` or `species table`, per field |

Config always wins. A curated table is a default, not an override of the person
who knows their own annotation.

## warnings vs notes
- **warning** — might be wrong, needs a decision now (unrecognised species, no
  mitochondrial prefix so QC would silently measure nothing)
- **note** — true and worth knowing, but not a decision point (mouse QC defaults
  were derived from human data)

Mouse's borrowed thresholds are a note on purpose: as a warning, every mouse run
stopped at step two, which is how people learn to click through gates.

## Downstream routing
`sample_qc_triage` when enabled, then `resolve_reference` for FASTQ or
`count_matrix_classify` for a matrix.
