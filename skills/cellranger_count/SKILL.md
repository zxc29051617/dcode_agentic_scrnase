---
name: cellranger_count
description: Run Cell Ranger count on validated FASTQ bundles and produce BAM plus raw and filtered count outputs.
version: 0.1.0
---

# cellranger_count

## Purpose
Run Cell Ranger count after FASTQ preflight passes.

## Input
- `fastq_bundle`
- `samplesheet`
- `reference`
  - Cell Ranger transcriptome / mkref output
- `cellranger_config`
  - `binary`, `localcores`, `localmem`, `create_bam`, `include_introns`, `expected_cells`, chemistry settings

## Output
- `run_dir`
- `bam`
- `raw_feature_bc_matrix`
- `filtered_feature_bc_matrix`
- `web_summary`
- `metrics_summary`
- `run_manifest`
- `preferred_h5ad` if a downstream conversion step is available
- `warnings`
- `errors`

## Behavior
- Launch Cell Ranger count with validated arguments
- Keep the command and runtime metadata for provenance
- Produce the raw matrix and filtered matrix outputs expected by downstream matrix classification
- Record whether BAM creation was enabled

## Failure modes
- Missing or invalid reference
- FASTQ mismatch with samplesheet
- Cell Ranger binary missing
- Reference / FASTQ / chemistry inconsistency
- Cell Ranger exits without producing the expected outputs

## Downstream routing
After success, the workflow should call `count_matrix_classify` on the produced matrix outputs.
