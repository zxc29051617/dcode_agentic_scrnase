---
name: apply_cell_qc_filter
description: Apply QC thresholds to AnnData and produce a filtered object for the downstream steps.
version: 0.1.0
---

# apply_cell_qc_filter

## Purpose
Apply QC thresholds to AnnData and produce a filtered object for the downstream steps.

## Input
- AnnData
- thresholds
- filter policy

## Output
- filtered_adata
- filter_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Apply explicit thresholds without hiding the removed-cell burden.
- Preserve counts of retained and removed cells.
- Return a filtered AnnData ready for doublet detection.

## Failure modes
- Threshold schema mismatch
- Invalid AnnData object
- Filtering would remove all cells

## Downstream routing
doublet detection
