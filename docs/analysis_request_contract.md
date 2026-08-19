# Analysis request contract

The first write-capable product boundary around the read-only CopilotKit
observation UI.

**This document was a design contract and is now partly an implementation
record.** Each section below is marked:

| status | meaning |
|---|---|
| **Current** | code on this branch, with tests naming it |
| **Local MVP** | implemented, and deliberately not production-grade; the gap is stated |
| **Near-term** | not implemented; the next bounded slice |
| **Production target** | not implemented; needs decisions this slice did not make |

The executor is the authority for every Current claim. `src/run.py`,
`src/service.py`, `src/persistence.py`, `src/graph.py`, `src/nodes.py`,
`src/registry.py` and `docs/report_contract.md` take precedence over this
document.

## Responsibilities — Current

```text
CopilotKit / Next.js            apps/web
  understand the conversation and show a proposed request

Analysis controller             services/controller/app
  validate an allowlisted input reference, persist a draft, and queue a job

Scientific worker               services/controller/worker.py
  run src.run through src/service.py, write the run directory, audit,
  checkpoint and report

Read-only gateway               services/gateway
  project runs and artifacts back to the web app
```

The gateway remains GET-only and unchanged by this slice. The browser, the
model and the controller never write under `runs/<scientific_run_id>/`; only
the scientific worker does. `services/controller/tests/test_worker.py` asserts
by AST walk that the controller calls no executor function, and
`test_the_controller_writes_nothing_into_the_run_directory` asserts the
filesystem consequence.

The worker is a separate *environment*, not only a separate module: it runs in
`dcode-scrna` because it imports the executor, while the controller runs in its
own venv with FastAPI and no scanpy. They share one SQLite file.

## Three kinds of user change

### View change — no new run — Current

Rendering changes over an existing artifact, handled entirely in the browser:
UMAP versus t-SNE, 2D versus 3D when both were computed, colour by `leiden`,
`cell_type`, `sample` or `conf_score`, zoom, pan, rotate, display subset.

### Analysis change — new revision — Current at a gate, Near-term as a re-request

At a human gate, an operator may `revise` the parameters that gate offers, and
the web control for that exists (`components/GateDecisionCard.tsx`). The
allowlist is `src/registry.py::StepSpec.revisable` and the conversion is
`coerce_overrides` — one semantic validator shared with the terminal.

What is **Near-term** is re-requesting a *finished* run with changed settings
from the browser. The executor supports it (`--resume-from` recomputes from the
first invalidated step) and no endpoint exposes it yet.

The browser never edits a result in place. A changed scientific request
produces a new config digest and its own audit events.

### Human decision — explicit confirmation — Current

A model may prepare and explain a request. It may not confirm it, answer a
gate, or start a job. This is enforced by what exists rather than by
instructions: `apps/web/lib/intakeActions.ts` defines four actions, none of
which can confirm anything, and there is deliberately no
`confirm_analysis_request` action —
`apps/web/tests/intake.test.ts::"no action can confirm a request"` fails if one
is added. Confirmation is a POST from a route handler reached by a button, and
the operator identity is resolved server-side and ignored from the body.

## Draft request shape — Current

Validated and serializable, holding references rather than paths.
Authoritative schema: `schemas/analysis_request.schema.json`.

```json
{
  "request_id": "ar_01J...",
  "conversation_id": "copilot-thread-id",
  "input_ref": "dataset:pbmc_1k_v3",
  "project": "PBMC demonstration",
  "species": "human",
  "research_question": "compare cell-type composition across samples",
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
  "created_at": "...",
  "updated_at": "...",
  "missing_questions": [],
  "validation_errors": [],
  "warnings": [],
  "unsupported": [],
  "scientific_run_id": null
}
```

`input_ref` and `study_design_ref` resolve through a server-side allowlist. The
model never supplies a shell command, an unrestricted path or a Python snippet.

### `research_question` — Current, and why it is a field

Nothing in the executor recorded what an analysis was *for*. A config says
`resolution: 1.0`; it does not say whether 1.0 was chosen because someone
wanted rare subpopulations separated. Recording the question is what makes the
settings reviewable, and it is the thing a conversation is actually good at
capturing.

It is deliberately **never converted into a config value**. The only effect it
has is to make the intake *ask a question*:
`validation.comparison_needs_manifest` reads it for words about comparing
samples and, finding them, asks which manifest describes the libraries —
because without one the report cannot say which cells came from which sample,
and the request would silently be for something narrower than what was asked
for. It sets nothing. A sentence in a chat window must not become a threshold.

### Three lists, not one — Current

Collapsing these was the first thing that made the intake unusable:

| field | means |
|---|---|
| `validation_errors` | this request is wrong; nothing can start |
| `missing_questions` | this request is incomplete; ask, do not guess |
| `warnings` | this will run, and here is what it will do that you may not have meant |
| `unsupported` | this was asked for and this workflow has no step for it |

An absent required value is a question rather than a default, because a
filled-in species, manifest or CellTypist model produces a request that looks
complete and describes an analysis nobody asked for.

### `analysis` is a vocabulary, not a config dict — Current

`domain.ANALYSIS_TO_CONFIG` maps public names to executor keys —
`embedding_method` → `method`, `embedding_dimensions` → `dimensions` — and
`to_executor_config` can emit only keys it has an explicit rule for. The point
is not naming: a request passed straight through as config would make every
documented CLI flag, and every config key any skill happens to read, settable
by whoever can post a request.

`annotation` and `doublet_detection` are accepted as `true` and reported as
**unsupported** when `false`: both steps are on `registry.MAINLINE` and every
route that produces a report runs them. Trajectory inference, RNA velocity,
differential expression, cell-cell communication and copy-number inference are
reported as unsupported outright. None of them is quietly dropped.

## Lifecycle — Current

```text
draft
  → validated
  → awaiting_confirmation
  → queued
  → running
  → needs_review
  → completed | failed | cancelled
```

`rejected` is the tenth status: a request that failed validation badly enough
that re-previewing is the way forward.

A request can be rejected at validation without creating a scientific run. Once
confirmed, the controller records the confirmation and queues one idempotent
job; repeating the confirmation cannot start a second. The uniqueness is a
database index (`jobs_one_start_per_request`), not a handler check, because two
clicks race and an index does not.

`needs_review` is reached by the worker, not claimed by it: the run's own
`status` says so, and `gates.gate_state` derives it from the audit log
independently.

## API boundary — Current

The write-capable controller is a separate service from the read-only gateway.

```text
POST /v1/analysis-requests/preview                 validate; execute nothing
GET  /v1/analysis-requests/{request_id}            draft, job and run state
GET  /v1/analysis-requests/{request_id}/status     the polling shape
POST /v1/analysis-requests/{request_id}/confirm    human confirmation; one job
GET  /v1/scientific-runs/{id}/gate                 the pending question
POST /v1/scientific-runs/{id}/gates/{gate_id}/decision   one human answer

GET  /v1/scientific-runs/{scientific_run_id}       remains the gateway's, read-only
```

The original plan said the confirm endpoint should come "after input
allowlisting, authentication, job ownership and idempotency are agreed". Three
of the four are implemented — allowlisting in `app/catalog.py`, ownership in
the job table, idempotency in the unique index. **Authentication is not**, and
that is the main reason this slice is labelled local-development. See
"Operator identity" in `services/controller/README.md`.

## CopilotKit actions — Current

The five read-only actions are unchanged and still read-only.

```text
list_available_datasets       references and display names; never a path
list_available_study_designs  manifest references
prepare_analysis_request      pure preview: creates no run, queues no job
get_analysis_request          request, job and run status
```

There is no `confirm_analysis_request` action and there must never be. The two
sets are never merged into one runtime: `?mode=intake` selects the intake four,
anything else selects the read-only five, and the default is the read-only set
so a missing parameter is the safe case.

## Human gate from a browser — Current

The executor already supported everything but one thing. `run_workflow` with a
durable checkpointer and `decide=None` suspends at a gate and returns;
`continue_workflow` picks it up from another process. What was missing is that
`continue_workflow` answers *every* gate it reaches, because at a terminal
there is still a person sitting there.

`src/service.py::continue_checkpoint_once` is the seam: a one-shot answerer
that raises `EOFError` on the second question, which `_answer_until_done`
already treats as "there is nobody to ask" and leaves the run suspended. So a
web decision applies to exactly the question it was shown, and a run that stops
again stops with a *new* pending question rather than inheriting the answer.

`src/service.py::start_detached_run` is the other half, and adds one optional
argument to `run_workflow`: `run_id`, which names a fresh run without drawing
up a resume plan. A worker needs the id *before* it starts, so that a crash
between "job queued" and "graph running" leaves a directory the job already
claims rather than an orphan and a retry that starts a second analysis. Passing
it together with `resume_run_id` is refused.

Neither function knows anything scientific. Routing stays in `graph.py`, step
order and revisable parameters stay in `registry.py`, and what a decision means
stays in `nodes.make_human_gate_node`.

### Gate identity

A run can open the same gate twice: `revise` routes back to the step, which
runs again and can stop again. So a pending gate is identified by
`generation` — how many gates the run has opened, read from its own audit log —
and `gate_id`, derived from `(run_id, generation, gate, step)` so the
controller, the worker and the browser all compute the same handle without
being told it. A decision carries the generation back, and a stale one is
refused.

## Identifiers — Current

| identifier | scope |
|---|---|
| `conversation_id` | one CopilotKit conversation; may prepare many requests |
| `request_id` | one proposed analysis request |
| `job_id` | one queued worker job |
| `scientific_run_id` | one scientific run and its provenance |
| `generation` | one pending gate of one run |
| `revision` | one changed scientific configuration |

`conversation_id` is never used as a `scientific_run_id`, and `request_id` is
never a checkpoint thread key — LangGraph's `configurable.thread_id` remains
exactly `scientific_run_id`, as `persistence.thread_config` has always set it.
The viewer and the report always name a specific run and revision.

## Not implemented

**Near-term**

- Re-requesting a finished run with changed settings from the browser
  (`--resume-from` exists; no endpoint exposes it).
- Cancelling a queued or running job from the browser.
- SSE or AG-UI streaming. The intake page and the worker both poll, and nothing
  in this repository claims otherwise.

**Production target**

- Authentication and authorization. Anyone who can reach the controller can
  confirm a request.
- Postgres and a real queue instead of SQLite and a polling worker.
- Multi-worker scheduling across machines.
- Object storage for artifacts, and retention.
- Automatic recovery of a run interrupted mid-step. `reconcile()` marks it
  failed and says to use `--resume-from`, deliberately: re-running would start
  a second analysis under a run id the first is still using.
