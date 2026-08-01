---
name: build_report
description: Build the final HTML, PDF, and JSON summary from the verified workflow state and artifacts.
version: 0.1.0
---

# build_report

## Purpose
Build the final HTML, PDF, and JSON summary from the verified workflow state and artifacts.

## Input
- final state
- artifacts
- report config

## Output
- html_report
- pdf_snapshot
- json_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Render the canonical interactive HTML report.
- Produce a frozen PDF snapshot from the same verified content.
- Package a machine-readable JSON summary for provenance.

## Failure modes
- Missing final workflow state
- Required artifacts are absent
- Report generation cannot complete cleanly

## Downstream routing
done
