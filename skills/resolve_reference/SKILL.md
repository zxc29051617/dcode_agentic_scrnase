---
name: resolve_reference
description: Resolve which Cell Ranger reference this run uses from the declared species, verify it is on disk, and check it really is that species.
version: 0.1.0
status: implemented
---

# resolve_reference

## Purpose
Decide **which** reference this run counts against, and prove it matches the
species the user declared. Runs right after `ingest_validate`, before the route
splits, so both the FASTQ and the count-matrix route pass through it.

Division of labour with `fastq_preflight`:

| | owns |
|---|---|
| `resolve_reference` | *which* reference, and does it match the declared species |
| `fastq_preflight` | can *this* reference and *these* FASTQs run `cellranger count` |

## Why this is a graph step and not config loading
Picking the wrong reference fails **silently**. The counts come out wrong, but
the audit log, the report, and the matrix all agree with each other and are all
wrong together — the one error shape nothing downstream can notice. Judge,
human gate, and audit log exist for exactly this, and a step only gets them by
being in the graph.

## Input
`run(payload)` where payload contains:

| key | meaning |
|---|---|
| `config.species` | `human`, `mouse`, `小鼠`, `Mus musculus`, … — see `src/species.py` |
| `config.transcriptome` | explicit path; always wins over the species lookup |
| `config.reference_root` | where references live, default `reference` |
| `artifacts.ingest_validate` | `needs_upstream_preprocessing` decides whether a transcriptome is *required* |

## Output

| key | meaning |
|---|---|
| `species` | canonical name, or `None` when unrecognised |
| `transcriptome` | resolved project-local path, or `None` |
| `reference_available` | the path exists and holds a readable `reference.json` |
| `species_verified` | the reference's own metadata confirmed the declared species |
| `reference_genomes` | the `genomes` mkref stamped in — what a counted matrix will carry |
| `mito_prefix` | `MT-` / `mt-` / … — how `run_qc_metrics` finds mitochondrial genes |
| `erythroid_genes`, `marker_db` | the rest of the species constants |
| `warnings`, `errors`, `recommended_next_tool`, `metrics` | standard |

## Behavior
- An explicit `config.transcriptome` always wins; the species lookup is only the default.
- Paths stay project-local. `reference/<dirname>` is what code and config say; the
  symlink made by `scripts/link_reference.sh` is the only thing that knows where
  the bytes are.
- A transcriptome is **required** only when `ingest_validate` said the input is
  FASTQ. A count-matrix run needs the species constants but never the 32 GB index.
- Species verification reads the reference's own `reference.json`, never the
  directory name — a directory can be called anything.

## Stays quiet on purpose
Two cases where the reference cannot be judged, and blocking would be worse than
the gap:

- **unrecognised fingerprint** — a custom build or a non-model organism. Blocking
  a legitimate reference is worse than missing a typo.
- **barnyard / PDX** (two species match) — both are correct; there is no wrong
  answer to point at.

Both emit a warning saying verification was skipped, rather than a false pass.

## Failure modes
Each becomes an `errors` entry, which `judge_reference` turns into `fail`:

- species unrecognised **and** no explicit `transcriptome` — nothing to resolve
- the resolved path does not exist (message carries how to get it)
- the path exists but has no readable `reference.json` — not a Cell Ranger reference
- **the reference is a different species than declared** — the silent-failure case

## Downstream routing
`sample_qc_triage` when enabled, otherwise `fastq_preflight` for FASTQ or
`count_matrix_classify` for matrices.

## Standalone

```bash
python skills/resolve_reference/resolve_reference.py --species human
python skills/resolve_reference/resolve_reference.py --transcriptome reference/my_custom_ref
```
