---
name: annotate_cells
description: Assign provisional cell labels from marker evidence and any approved reference evidence.
version: 0.1.0
---

# annotate_cells

## Purpose
Assign provisional cell labels from marker evidence and any approved reference evidence.

## Input
- marker table
- reference evidence
- annotation policy

## Output
- labels
- confidence
- evidence
- warnings
- errors
- recommended_next_tool

## Behavior
- Map marker evidence to provisional biological labels.
- Report confidence and keep unknown or mixed states explicit.
- Do not force a label when evidence is weak.

## Failure modes
- Marker table is incomplete
- Reference evidence conflicts with marker evidence
- No defensible label can be assigned

## Downstream routing
human review
