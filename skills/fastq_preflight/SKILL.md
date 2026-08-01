---
name: fastq_preflight
description: Validate raw FASTQ bundles before Cell Ranger count. Checks file structure, sample sheet consistency, read role clues, and reference readiness.
version: 0.2.0
status: implemented
---

# fastq_preflight

## Purpose
Validate whether a FASTQ bundle is ready to enter `cellranger_count`. Goes deeper
than `ingest_validate`'s classification: opens the first read of each file to
check actual lengths against known 10x chemistries, cross-checks an optional
sample sheet, and verifies the reference path looks like a real transcriptome.

## Input
`run(payload)` where payload contains:

| key | meaning |
|---|---|
| `input_bundle` | `{"paths": [...]}` — FASTQ directories or files |
| `config.reference` | Cell Ranger transcriptome path |
| `config.samplesheet` | optional `[{"sample": ..., "chemistry": ...}, ...]` |
| `config.localcores` / `localmem` / `expected_cells` | optional run knobs, type-checked only |

## Output

| key | meaning |
|---|---|
| `ready_to_count` | `True` only when `blocking_errors` is empty |
| `detected_libraries` | one entry per sample: lanes, read counts, observed lengths, chemistry guess |
| `read_structure` | `{sample: {read_role: {n_files, lengths_observed, sampled_from}}}` |
| `blocking_errors` | would make `cellranger_count` fail outright; mirrored into `errors` for the judge |
| `warnings` | judged, but does not block |
| `recommended_next_tool` | `cellranger_count` if ready, else `human_review` |

## Behavior
- Peeks the first read of every file (`gzip` aware) to get real lengths — no assumptions.
- R1 length 28 → `SC3Pv3`. R1 length 26 → ambiguous, reported as `[SC3Pv2, SC5P-PE, SC5P-R2]`
  rather than a single guess, since that length is shared across kits.
- R2 shorter than 50bp is flagged as too short to be a usable cDNA read.
- A sample sheet entry naming a sample with no matching FASTQ is blocking (nothing to count).
  A FASTQ sample missing from the sheet is only a warning (extra data, still usable).
- A declared chemistry that conflicts with the observed read length is a warning, not blocking —
  Cell Ranger's own `--chemistry` override may be intentional.
- Reference readiness: no path → blocking. Path exists but missing `reference.json` → blocking
  (does not look like a Cell Ranger transcriptome).

## Failure modes
Each becomes a `blocking_errors` entry:

- input path missing, or no FASTQ files found under it
- a sample with no R1 (barcode+UMI) or no R2 (cDNA) read
- no reference provided, or the given path is not a valid transcriptome
- a sample sheet requesting a sample with no matching FASTQ

## Downstream routing
`cellranger_count` regardless of `ready_to_count` — the graph is wired linearly;
`judge_fastq_preflight` turns a non-empty `blocking_errors` into `fail`, which is
what actually stops the run at the human gate. See `src/graph.py`.

## Standalone

```bash
python skills/fastq_preflight/fastq_preflight.py <fastq_dir> --reference <path>
```

## Verified against
`pbmc_1k_v3` (10x official 3' v3 test set): R1=28bp, I1=8bp, chemistry correctly
identified as `SC3Pv3` (matches `SOURCE.txt`'s `10x_3prime_v3_GEX`), blocked only
on the missing reference since none was supplied.
