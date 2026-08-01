---
name: judge_fastq_preflight
description: Judge the result of fastq_preflight with the shared local pass/warn/fail contract.
version: 0.1.0
---

# judge_fastq_preflight

## Purpose
Evaluate the output of `fastq_preflight` and return a structured judge result.

## Input
- step name
- analysis result payload
- key metrics and artifacts
- policy context

## Output
- `step`
- `verdict`
- `score`
- `reasons`
- `evidence`
- `suggested_action`
- `needs_human_review`

## Behavior
- Return JSON only and do not change any analysis outputs.
- Apply the shared judge contract from `schemas/judge_result.schema.json`.
- Distinguish acceptable results from warning and failure states.

## Failure modes
- Missing step context
- Unsupported or incomplete evidence
- Output that cannot be assessed against the judge schema

## Downstream routing
pass -> next workflow step; warn/fail -> human review or reroute
