---
name: cell_calling_review
description: Review raw-matrix evidence and decide whether cell calling is already resolved or needs human attention.
version: 0.1.0
---

# cell_calling_review

## Purpose
Review raw-matrix evidence and decide whether cell calling is already resolved or needs human attention.

## Input
- raw matrix summary
- source state
- review policy

## Output
- cell_calling_state
- evidence
- warnings
- errors
- recommended_next_tool

## Behavior
- Inspect barcodes, counts, and matrix structure for cell-calling clues.
- Emit a decision payload rather than silently assuming resolution.
- Escalate ambiguous cases to human review.

## Failure modes
- No usable raw-matrix summary
- Ambiguous evidence with no reliable decision
- Missing provenance needed for review

## Downstream routing
mainline or human gate
