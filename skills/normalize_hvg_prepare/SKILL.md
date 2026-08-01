---
name: normalize_hvg_prepare
description: Normalize counts, log-transform them, select HVGs, and prepare a PCA-ready AnnData object.
version: 0.1.0
---

# normalize_hvg_prepare

## Purpose
Normalize counts, log-transform them, select HVGs, and prepare a PCA-ready AnnData object.

## Input
- AnnData
- normalization config

## Output
- normalized_adata
- hvgs
- prep_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Normalize and transform the expression matrix in a reproducible way.
- Select HVGs according to the configured policy.
- Prepare a matrix suitable for PCA and later steps.

## Failure modes
- Missing normalized input
- No valid genes left after filtering
- Configuration cannot support HVG selection

## Downstream routing
PCA
