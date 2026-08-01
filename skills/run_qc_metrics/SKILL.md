---
name: run_qc_metrics
description: Compute deterministic QC metrics from AnnData as the first analytical step on the mainline.
version: 0.1.0
---

# run_qc_metrics

## Purpose
Compute deterministic QC metrics from AnnData as the first analytical step on the mainline.

## Input
- AnnData
- QC config

## Output
- qc_metrics
- qc_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Calculate standard cell- and gene-level QC summaries.
- Keep raw observations intact for later filtering decisions.
- Provide a compact summary for the judge node.

## Failure modes
- Invalid AnnData input
- Missing QC-relevant annotations
- Matrix dimensions cannot be interpreted

## Downstream routing
cell QC filter
