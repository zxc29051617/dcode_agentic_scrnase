---
name: run_integration
description: Perform batch correction or latent integration when multiple batches are present.
version: 0.1.0
---

# run_integration

## Purpose
Perform batch correction or latent integration when multiple batches are present.

## Input
- AnnData
- integration config

## Output
- integrated_embedding
- integration_summary
- warnings
- errors
- recommended_next_tool

## Behavior
- Use the configured integration strategy only when it is scientifically justified.
- Retain provenance for the integrated representation.
- Report whether the data actually warranted integration.

## Failure modes
- No usable batches or covariates
- Integration config is incompatible with the input
- Latent representation cannot be constructed

## Downstream routing
clustering
