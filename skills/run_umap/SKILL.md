---
name: run_umap
description: Compute UMAP coordinates for visual inspection after clustering.
version: 0.1.0
---

# run_umap

## Purpose
Compute UMAP coordinates for visual inspection after clustering.

## Input
- AnnData
- UMAP config

## Output
- umap_coordinates
- warnings
- errors
- recommended_next_tool

## Behavior
- Create 2D coordinates from the selected embedding.
- Preserve the mapping between cells and coordinates.
- Keep the step deterministic and reproducible.

## Failure modes
- Missing embedding or neighbor graph
- UMAP parameters are invalid
- Coordinates cannot be constructed

## Downstream routing
markers
