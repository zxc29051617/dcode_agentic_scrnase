---
name: load_raw_counts
description: Load raw count matrices or raw-count h5ad inputs into AnnData while preserving the pre-cell-calling state.
version: 0.1.0
---

# load_raw_counts

## Purpose
Load raw count matrices or raw-count h5ad inputs into AnnData while preserving the pre-cell-calling state.

## Input
- raw matrix bundle
- optional source hint
- load config

## Output
- adata
- source_state
- warnings
- errors
- recommended_next_tool

## Behavior
- Import raw counts without collapsing the pre-cell-calling evidence.
- Record source provenance and matrix shape metadata.
- Force a cell-calling review if the state is still unresolved.

## Failure modes
- Missing raw matrix files
- Unsupported h5ad state
- Evidence inconsistent with raw-count assumptions

## Downstream routing
cell_calling_review or mainline QC
