---
name: fastq_qc
description: Assess sequencing quality with FastQC and aggregate it with MultiQC, reading 10x read roles so structural failures are not mistaken for bad data.
version: 0.1.0
status: implemented
---

# fastq_qc

## Purpose
Answer "is this sequencing any good?" — a different question from
`fastq_preflight`'s "can this run through Cell Ranger?".

| | `fastq_preflight` | `fastq_qc` |
|---|---|---|
| reads | the first record of each file | every read |
| asks | is the bundle structurally usable | is the sequencing quality acceptable |
| costs | milliseconds | minutes |

It runs **after** preflight on purpose: a bundle with a missing R2 should be
rejected in milliseconds, not after twenty minutes of FastQC.

## The 10x problem this step exists to handle
Run FastQC on a 10x library and it will report failures that are **not quality
problems**:

| read | what it is | FastQC will fail it on |
|---|---|---|
| R1 | 28bp barcode + UMI | per-base sequence content, duplication, overrepresented sequences |
| I1 | 8bp sample index | the same, plus length |
| R2 | the actual cDNA | nothing structural — this is the read that matters |

R1 is a barcode, so of course its base composition is skewed and its sequences
repeat. Reporting that as "QC failed" would make every 10x run look broken and
train people to ignore the step.

So: quality is judged on **R2**, and R1/I1 module failures of the structural
kind are downgraded to notes saying they are expected. A genuine R1 problem —
low quality scores, not composition — is still reported.

### Duplication is expected on R2 as well
The read-role rule alone is not enough. scRNA-seq amplifies by PCR and
deliberately over-sequences a small set of transcripts; UMIs collapse the copies
afterwards, and FastQC cannot see UMIs. So it fails **Sequence Duplication
Levels** on the cDNA read of every healthy run.

That one is treated as expected on all reads, not just barcodes. The number is
still reported as a note, together with the metric that actually answers the
question — Cell Ranger's **Sequencing Saturation**, measured after
deduplication, where 50-80% is the usual target.

Found by running the real `pbmc_1k_v3` set: R2 duplication 51%, which the
read-role rule flagged as a failure, while Cell Ranger put sequencing saturation
at a perfectly healthy 70.8%.

## Input

| key | meaning |
|---|---|
| `input_bundle.paths` | FASTQ directories or files |
| `run_dir` | where the reports go |
| `artifacts.ingest_validate` | `fastq_layout`, used to know each file's read role |
| `config.fastqc_binary` / `multiqc_binary` | default: found on PATH |
| `config.fastqc_threads` | default 8 |
| `config.skip_fastq_qc` | skip the step and say so, rather than failing |
| `config.min_q30` | `warn` below this fraction on R2, default 0.75 |

## Output

| key | meaning |
|---|---|
| `reports` | per-file FastQC status, one entry per FASTQ |
| `multiqc_report` | the aggregated HTML — the thing to actually open |
| `per_read_role` | R1 / R2 / I1 summaries, so R2 can be read on its own |
| `metrics` | `q30_r2`, `pct_duplicate_r2`, `max_adapter_pct`, `total_sequences`, `pct_gc` |
| `module_failures` | FastQC modules that failed, minus the expected 10x ones |
| `warnings`, `errors`, `recommended_next_tool` |

## Behavior
- Runs `fastqc` over every FASTQ, then `multiqc` over the results.
- Parses `fastqc_data.txt` out of each `_fastqc.zip` rather than scraping HTML.
- Q30 is computed from the "Per sequence quality scores" histogram: the fraction
  of reads whose mean quality is at least 30. FastQC does not report it directly.
- Duplication comes from `#Total Deduplicated Percentage`, inverted.
- Adapter content is the maximum across all positions and all adapter types.

## Missing tools are a warning, not a failure
If `fastqc` is not installed the step reports that and continues. Sequencing QC
is advisory here — Cell Ranger's own `web_summary.html` still reports Q30 and
mapping rate — so a missing optional tool should not block a count that would
otherwise succeed. `config.skip_fastq_qc` does the same thing deliberately.

## Failure modes
Each becomes an `errors` entry:

- no FASTQ found under the given paths
- `fastqc` exited non-zero
- FastQC produced no parseable output

## Downstream routing
`cellranger_count`.

## Verified against
`pbmc_1k_v3`, 6 files / 200M reads, 12 threads:

| | |
|---|---|
| Q30 on R2 (cDNA) | 94.1% |
| Q30 on R1 / I1 | 98.0% / 94.6% |
| R2 duplication | 51% — expected, noted, not a failure |
| worst adapter content | 2.7% |
| genuine module failures | none |

Flags correctly downgraded as expected: per base sequence content, per sequence
GC content, overrepresented sequences (barcode/index reads), and sequence
duplication (all reads).

## Standalone

```bash
python skills/fastq_qc/fastq_qc.py --fastqs <dir> --run-dir runs/manual --threads 8
```
