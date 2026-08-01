---
name: human_review_decision
description: Convert a human decision into a structured accept, revise, or stop action.
version: 0.1.0
---

# human_review_decision

## Purpose
Convert a human decision into a structured accept, revise, or stop action.

## Input
- judge payload
- candidate labels
- decision context

## Output
- decision
- rationale
- warnings
- errors
- recommended_next_tool

## Behavior
- Record explicit human review choices.
- Allow revise or stop without hiding the decision.
- Feed the chosen action back into the workflow state.

## Failure modes
- Missing decision context
- Conflicting review instructions
- Decision payload cannot be serialized

## Downstream routing
report or reroute
