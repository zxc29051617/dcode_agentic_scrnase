---
name: run_clustering
description: Assign cluster labels to the integrated or PCA-based representation.
version: 0.1.0
---

# run_clustering

## Purpose
Assign cluster labels to the integrated or PCA-based representation.

## Input
- AnnData
- clustering config

## Output
- cluster_labels
- clustering_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Run a documented clustering strategy such as Leiden.
- Record cluster assignments with provenance.
- Expose cluster size and quality summaries to the judge.

## Failure modes
- Missing graph or embedding
- Invalid clustering config
- No clusters can be formed

## Downstream routing
UMAP
