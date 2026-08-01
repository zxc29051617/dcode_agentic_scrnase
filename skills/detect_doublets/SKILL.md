---
name: detect_doublets
description: Detect and annotate likely doublets before normalization and dimensionality reduction.
version: 0.1.0
---

# detect_doublets

## Purpose
Detect and annotate likely doublets before normalization and dimensionality reduction.

## Input
- AnnData
- doublet config

## Output
- doublet_calls
- filtered_adata
- warnings
- errors
- recommended_next_tool

## Behavior
- Run a deterministic doublet-calling stage or pass through a documented placeholder.
- Preserve the filtered object after doublet handling.
- Record the doublet burden for the judge.

## Failure modes
- Invalid input AnnData
- Missing doublet configuration
- Incompatible per-cell annotations

## Downstream routing
preprocess
