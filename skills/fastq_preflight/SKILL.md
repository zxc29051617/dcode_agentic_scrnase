---
name: fastq_preflight
description: Validate raw FASTQ bundles before Cell Ranger count. Checks file structure, sample sheet consistency, read role clues, and reference readiness.
version: 0.1.0
---

# fastq_preflight

## Purpose
Validate whether a FASTQ bundle is ready to enter `cellranger_count`.

## Input
- `fastq_bundle`
  - one sample directory or a list of sample directories
- `samplesheet`
  - library/sample name, FASTQ directory, sample prefix, chemistry
- `reference`
  - Cell Ranger transcriptome path
- `config`
  - `localcores`, `localmem`, `expected_cells` if provided, and related run knobs

## Output
- `ready_to_count`: boolean
- `detected_libraries`: array of libraries
- `read_structure`: per-file / per-library read length and role evidence
- `warnings`: array of non-blocking issues
- `blocking_errors`: array of fatal issues
- `recommended_next_tool`: usually `cellranger_count`

## Behavior
- Validate FASTQ extensions and layout
- Validate sample sheet shape and sample naming consistency
- Check whether the FASTQ bundle looks like 10x GEX input
- Detect whether chemistry should be explicit or can remain auto
- Refuse to call downstream counting if the bundle is malformed

## Failure modes
- Missing or unreadable FASTQ
- Sample sheet mismatch
- Unsupported read structure
- Missing reference for the intended counting route
- Non-10x or ambiguous input that needs human review

## Downstream routing
If `ready_to_count` is true, the workflow should call `cellranger_count` next.
