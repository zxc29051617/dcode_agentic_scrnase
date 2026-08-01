---
name: run_pca
description: Compute PCA embeddings and loadings from the prepared AnnData object.
version: 0.1.0
---

# run_pca

## Purpose
Compute PCA embeddings and loadings from the prepared AnnData object.

## Input
- AnnData
- PCA config

## Output
- pca_embedding
- loadings
- variance_explained
- warnings
- errors
- recommended_next_tool

## Behavior
- Fit PCA using the configured number of components.
- Store the embedding and loadings in a structured form.
- Summarize variance explained for judge review.

## Failure modes
- Invalid PCA input
- Too few variable features
- Component count exceeds matrix rank

## Downstream routing
integration or clustering
