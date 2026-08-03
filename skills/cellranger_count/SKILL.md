---
name: cellranger_count
description: Run Cell Ranger count on validated FASTQ bundles and produce BAM plus raw and filtered count outputs.
version: 0.2.0
status: implemented
---

# cellranger_count

## Purpose
Turn FASTQ into count matrices, once `fastq_preflight` says the bundle is ready
and `resolve_reference` has proven which reference to use.

## Input
`run(payload)` where payload contains:

| key | meaning |
|---|---|
| `input_bundle.paths` | FASTQ directories, joined into one `--fastqs` |
| `run_dir` | where output goes; the graph passes the run's own directory |
| `artifacts.resolve_reference` | supplies `transcriptome`, already species-checked |
| `artifacts.fastq_preflight` | supplies `detected_libraries` and `ready_to_count` |
| `config.binary` | path to `cellranger`; found automatically if omitted |
| `config.localcores` / `localmem` | defaults 16 / 64 |
| `config.create_bam` / `include_introns` | defaults true / true, always explicit in the command |
| `config.chemistry` | default `auto` — Cell Ranger detects it |
| `config.expected_cells` | optional `--expect-cells` |

## Output

| key | meaning |
|---|---|
| `libraries` | one record per sample: outs, matrices, BAM, metrics, disposition |
| `count_manifest` | JSON of `library_id -> outs`, for later steps |
| `raw_feature_bc_matrix` / `filtered_feature_bc_matrix` | the two matrices Cell Ranger writes |
| `matrix_path` / `matrix_kind` | what `count_matrix_classify` routes on (filtered) |
| `metrics.per_library` | estimated cells, mean reads/cell, fraction reads in cells — what the judge scores |

`disposition` is `counted` for new work or `reused: <genome>` for an existing
matrix whose own recorded genome was checked against this reference first.

## The reference guard
Counting costs ~20 minutes a library, so an existing
`filtered_feature_bc_matrix.h5` is reused — **but only after the genome recorded
inside that matrix is compared with the reference this run was told to use.**

Without that check, changing the reference and re-running into the same run
directory skips every library and finishes in seconds with the old counts, while
the audit log and the report both name the new reference. Log, report and matrix
then agree with each other and are all wrong together — the one error shape
nothing downstream can notice.

Being unable to verify is treated as a mismatch. A stop is recoverable in one
command; a wrong reuse is not detectable at all.

## Behavior
- Cell Ranger is located automatically (PATH, then the usual tarball install
  locations) so nobody has to remember the version number in the path. An
  explicit `config.binary` is honoured even when wrong — reporting the bad path
  beats silently running a different install.
- `--chemistry` is left at `auto` unless config declares one. `fastq_preflight`'s
  read-length guess is deliberately not used: when that guess is ambiguous
  (26bp covers three kits) forcing one is worse than Cell Ranger's own detection.
- `--create-bam` and `--include-introns` are always written into the command
  explicitly, so two runs are comparable from the log alone.
- One library's refusal stops the whole step. A partial set of counts feeding the
  mainline silently is worse than stopping.

## Failure modes
Each becomes an `errors` entry, which `judge_cellranger_count` turns into `fail`:

- no transcriptome (`resolve_reference` did not run)
- the reference has no `reference.json`
- `fastq_preflight` reported `ready_to_count: false` — its reasons are carried through
- the cellranger executable cannot be found
- an existing matrix was counted against a different reference, or cannot be read
- a run directory exists with no filtered matrix in it (partial or aborted run)
- cellranger exits non-zero, or exits cleanly without producing the matrix

## Downstream routing
`count_matrix_classify`, on the filtered matrix. The raw matrix stays in the
record for `cell_calling_review`.

## Standalone

```bash
python skills/cellranger_count/cellranger_count.py \
  --fastqs ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs \
  --sample pbmc_1k_v3 \
  --transcriptome reference/T2T_CHM13v2_RefSeqLiftoff_v5_3 \
  --run-dir runs/manual --localcores 32 --localmem 128
```

## Verified against
`pbmc_1k_v3` counted against the T2T-CHM13v2.0 / RefSeq-Liftoff-v5.3 reference,
32 cores / 128 GB, 21.3 minutes:

| | |
|---|---|
| estimated cells | 1,218 (10x's published figure for this set is 1,222, on GRCh38) |
| mean reads/cell | 54,681 |
| median genes/cell | 3,201 |
| reads confidently mapped to transcriptome | 79.6% |
| fraction reads in cells | 95.6% |

Both branches of the reuse decision were exercised on that real output:

- same reference → reused in 0.25s instead of recounting for 21 minutes
- a reference named `GRCh38-2024-A` → refused, matrix left untouched, message
  naming both genomes and how to recover

