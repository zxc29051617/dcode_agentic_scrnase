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
- optional `embedding_max_cells` display limit; the full AnnData remains the scientific artifact

## Output
- html_report
- pdf_snapshot
- json_summary
- `embedding_data_paths` for the app-native 2D/3D viewer
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
