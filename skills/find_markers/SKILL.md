---
name: find_markers
description: Compute cluster marker tables from the clustered AnnData object.
version: 0.1.0
---

# find_markers

## Purpose
Compute cluster marker tables from the clustered AnnData object.

## Input
- AnnData
- cluster labels
- marker config

## Output
- marker_table
- marker_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Run marker discovery on the defined cluster labels.
- Summarize the strongest marker evidence per cluster.
- Pass a table suitable for the annotation step.

## Failure modes
- Missing cluster labels
- AnnData lacks marker-relevant layers
- No meaningful differential signal

## Downstream routing
annotation
