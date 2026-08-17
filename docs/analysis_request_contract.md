# Analysis request contract

This is the first write-capable product boundary to add around the existing
read-only CopilotKit observation UI. It is a design contract, not an
implementation claim.

## Responsibilities

```text
CopilotKit / Next.js
  understand the conversation and show a proposed request

Analysis controller
  validate an allowlisted input reference, persist a draft, and queue a job

Scientific worker
  run `src.run`, write the scientific run directory, audit, checkpoint and report

Read-only gateway
  project runs and artifacts back to the web app
```

The gateway remains GET-only. The browser, model and analysis controller never
write under `runs/<scientific_run_id>/`; only the scientific worker does.

## Three kinds of user change

### View change — no new run

These only change the browser rendering of an existing artifact:

- UMAP versus t-SNE
- 2D versus 3D when that embedding was already computed
- color by `leiden`, `cell_type`, `sample` or `conf_score`
- zoom, pan, rotate and display subset

### Analysis change — new revision or artifact resume

These change scientific output and must be validated and recorded:

- embedding dimensions or method when not already computed
- UMAP neighbors or display parameters
- t-SNE perplexity
- integration mode
- clustering resolution
- QC thresholds
- marker configuration

The browser never edits an existing result in place. A changed scientific
request produces a new revision with a new config digest and audit event.

### Human decision — explicit confirmation

A model may prepare and explain a request. It may not silently confirm it,
answer a gate or start a job. Confirmation is an authenticated human action
bound to the exact draft and its digest.

## Draft request shape

The first request is a validated, serializable object. It contains references,
not arbitrary filesystem paths supplied by a model:

```json
{
  "request_id": "ar_01J...",
  "conversation_id": "copilot-thread-id",
  "input_ref": "dataset:pbmc_1k_v3",
  "project": "PBMC demonstration",
  "species": "human",
  "study_design_ref": "manifest:pbmc_1k_v3",
  "analysis": {
    "embedding_method": "both",
    "embedding_dimensions": [2, 3],
    "embedding_max_cells": 50000,
    "integration_mode": "harmony",
    "resolution": 1.0
  },
  "status": "awaiting_confirmation",
  "config_digest": "sha256:...",
  "created_by": "conversation",
  "created_at": "..."
}
```

`input_ref` and `study_design_ref` resolve through a server-side allowlist. The
model never supplies a shell command, an unrestricted path or a Python snippet.

## Lifecycle

```text
draft
  → validated
  → awaiting_confirmation
  → queued
  → running
  → needs_review
  → completed | failed | cancelled
```

A request can be rejected at validation without creating a scientific run. Once
confirmed, the controller records the confirmation and queues one idempotent
job. Repeating the same confirmation cannot start a second job.

## Proposed API boundary

The write-capable controller is separate from the read-only gateway:

```text
POST /v1/analysis-requests/preview
  validate a proposed request; no scientific execution

GET  /v1/analysis-requests/{request_id}
  return draft or job status

POST /v1/analysis-requests/{request_id}/confirm
  authenticated human confirmation; enqueue exactly one job

GET  /v1/scientific-runs/{scientific_run_id}
  remains the gateway's read-only projection
```

The first implementation slice should only build and display the preview. The
confirm endpoint should be added after input allowlisting, authentication,
job ownership and idempotency are agreed.

## CopilotKit actions

The current five read-only actions remain unchanged. A future request flow may
expose a pure preview tool, but confirmation must not be an unconstrained model
tool:

```text
prepare_analysis_request → structured draft + missing questions
human reviews the draft → explicit Confirm button
confirm_analysis_request → controller endpoint, not gateway
```

The assistant can explain why a parameter is needed and report job progress;
it cannot claim that a run started until the controller returns a queued or
running record.

## Report and viewer identity

The following identifiers must remain separate:

- `conversation_id`: the CopilotKit conversation
- `request_id`: one proposed analysis request
- `scientific_run_id`: one scientific run and its provenance
- `revision`: one changed scientific configuration

The viewer and final report always point to a specific `scientific_run_id` and
revision. A conversation may discuss several runs without changing their
identity.
