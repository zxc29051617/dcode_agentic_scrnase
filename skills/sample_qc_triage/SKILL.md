---
name: sample_qc_triage
description: Perform deterministic sample-level QC triage from a summary metrics table before the main workflow branches.
version: 0.1.0
---

# sample_qc_triage

## Purpose
Perform deterministic sample-level QC triage from a summary metrics table before the main workflow branches.

## Input
- QC metrics CSV
- optional identity checks
- triage policy

## Output
- sample_flags
- summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Validate the sample metrics table shape and required columns.
- Flag sample-level outliers and identity inconsistencies.
- Keep the triage operational rather than clinical.

## Failure modes
- Missing required QC columns
- No sample rows present
- Conflicting or malformed identity fields

## Downstream routing
FASTQ or matrix route
