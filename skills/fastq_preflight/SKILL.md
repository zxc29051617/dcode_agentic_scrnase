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
- Peeks the first read of every file (`gzip` aware) to get real lengths.
- **Chemistry comes from the barcode whitelist, not the read length.** See below.
- R2 shorter than 50bp is flagged as too short to be a usable cDNA read.
- A sample sheet entry naming a sample with no matching FASTQ is blocking (nothing to count).
  A FASTQ sample missing from the sheet is only a warning (extra data, still usable).
- A declared chemistry that conflicts with the observed read length is a warning, not blocking —
  Cell Ranger's own `--chemistry` override may be intentional.
- Reference readiness: no path → blocking. Path exists but missing `reference.json` → blocking
  (does not look like a Cell Ranger transcriptome).

## Chemistry: why read length does not work
This step used to read the chemistry off the R1 length — 26bp meant v2, 28bp
meant v3. Running 10x's own `pbmc_1k_v2` proved that wrong: **its R1 is 28bp**,
because read length is a sequencing parameter, not a property of the kit. You
can run more cycles than the chemistry needs, and 10x did.

The barcode whitelist is what actually identifies it. Each kit ships a different
list, and membership is decisive:

| dataset | R1 | 737K-august-2016 | 3M-february-2018 | called |
|---|---|---|---|---|
| `pbmc_1k_v2` | 28bp | **65%** | 9% | SC3Pv2 / SC5P |
| `pbmc_1k_v3` | 28bp | 7% | **69%** | SC3Pv3 |
| `neuron_1k_v3` | 28bp | 6% | **78%** | SC3Pv3 |

3' v2 and 5' share the 737K list, so a hit there narrows to those kits and stops.
Separating them needs to know where the cDNA begins, which means alignment —
not something a preflight check can or should do.

Read length is kept only as a validity check: shorter than 26bp is too short for
any 10x kit and blocks. Longer than a kit needs is a sequencing choice, not a
defect.

The guess is still never used to set `--chemistry`; Cell Ranger sees the reads
and auto-detects better than any preflight can.

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
Three real bundles, all with a 28bp R1 — the case the old heuristic got wrong:

| | chemistry called | correct |
|---|---|---|
| `pbmc_1k_v3` (human, 3' v3) | `SC3Pv3` | ✓ |
| `pbmc_1k_v2` (human, 3' v2) | `SC3Pv2 / SC5P-PE / SC5P-R2` | ✓ (v2 is in the set; 5' is genuinely indistinguishable here) |
| `neuron_1k_v3` (mouse, 3' v3) | `SC3Pv3` | ✓ |
