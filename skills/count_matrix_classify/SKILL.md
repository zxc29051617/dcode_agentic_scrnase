---
name: count_matrix_classify
description: Classify count-matrix-like inputs into raw, filtered, or unknown so the workflow can route to the correct downstream branch.
version: 0.1.0
---

# count_matrix_classify

## Purpose
Classify matrix-like input before downstream analysis.

## Input
- `matrix_bundle`
  - directories or files containing 10x count matrices or h5ad files
- `source_hint`
  - optional context from upstream tools
- `config`
  - optional policy settings for routing

## Output
- `matrix_class`: `raw` / `filtered` / `unknown`
- `evidence`: supporting clues used to classify the matrix
- `needs_cell_calling`: boolean
- `recommended_next_tool`: one of `load_raw_counts`, `load_filtered_counts`, or `cell_calling_review`
- `warnings`
- `errors`

## Behavior
- Detect whether the input looks like raw matrix output or filtered matrix output
- Treat ambiguous matrix-like inputs as `unknown`
- Preserve the distinction between pre-cell-calling and post-cell-calling data

## Failure modes
- Missing matrix files
- Ambiguous or mixed outputs
- Unsupported h5ad state
- Evidence inconsistent with routing assumptions

## Downstream routing
- `raw` → `load_raw_counts` and possibly `cell_calling_review`
- `filtered` → `load_filtered_counts`
- `unknown` → human review or upstream clarification
