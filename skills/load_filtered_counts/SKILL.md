---
name: load_filtered_counts
description: Load filtered count matrices or filtered-count h5ad inputs into AnnData for the downstream mainline.
version: 0.1.0
---

# load_filtered_counts

## Purpose
Load filtered count matrices or filtered-count h5ad inputs into AnnData for the downstream mainline.

## Input
- filtered matrix bundle
- optional source hint
- load config

## Output
- adata
- source_state
- warnings
- errors
- recommended_next_tool

## Behavior
- Import filtered counts and preserve source provenance.
- Treat the input as post-cell-calling unless evidence says otherwise.
- Pass a structured state forward for the core analysis line.

## Failure modes
- Missing filtered matrix files
- Unsupported h5ad state
- Evidence that conflicts with filtered-count assumptions

## Downstream routing
mainline QC
