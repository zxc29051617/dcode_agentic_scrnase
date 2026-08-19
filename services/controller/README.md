# services/controller

The write-capable half of the browser product: it validates analysis requests,
records an explicit human confirmation, and queues scientific jobs. A separate
worker — in the scientific environment, not this one — is what actually runs
them.

This service exists because `services/gateway` must not. That one is GET-only
and its guarantee is a property of its code ("there is no route here that
writes a file, starts `src.run`, opens a checkpoint, or answers a gate"). A
mutation endpoint added there would end that sentence. So mutation lives here,
in a different process, with a different database and a different dependency
set.

See `docs/analysis_request_contract.md` for the contract, and
`docs/copilotkit_product_architecture.md` §1 for the invariants both services
inherit.

## What it does not do

- It never writes under `runs/<scientific_run_id>/`. Only the worker does.
- It never builds a graph, imports `src.graph`, or calls a skill.
- It never resumes a checkpoint. It queues a job asking the worker to.
- It never decides a gate answer. It validates one a person submitted.

It does import one thing from the scientific package: `coerce_overrides` from
`src/registry.py`, which is pure standard library and executes nothing. That is
deliberate — it is what stops this service growing a second, drifting copy of
which parameters a gate may set. `services/controller/tests/test_worker.py`
asserts by AST walk that nothing else is imported or called.

## Isolated on purpose

Its own `requirements.txt` and its own virtualenv, never installed into
`dcode-scrna`, and nothing here edits `environment.yml` or `conda-lock.yml` —
the same rule `services/gateway` follows.

```bash
cd services/controller
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The **worker does not use that venv**. It imports the executor, so it runs in
`dcode-scrna`. The two share exactly one thing: a SQLite file.

## Run it

```bash
# the API — in the controller venv
CONTROLLER_DB=var/controller/controller.sqlite \
CONTROLLER_RUNS_ROOT=runs \
CONTROLLER_DATA_ROOTS=/abs/path/to/data \
CONTROLLER_CATALOG=config/dataset_catalog.json \
.venv/bin/uvicorn app.main:app --port 8020
```

```bash
# the worker — in the scientific environment, from the repository root
conda activate dcode-scrna
CONTROLLER_DB=var/controller/controller.sqlite \
CONTROLLER_RUNS_ROOT=runs \
python -m services.controller.worker
```

`--once` processes the queue until it is empty and exits, which is what the
tests use. Without it the worker polls.

`apps/web/scripts/dev-stack.sh` starts the gateway, the controller and the web
app together, and prints the worker command rather than starting it — a scanpy
import does not belong in the front end's process tree.

## Environment

| variable | required | local default | notes |
|---|---|---|---|
| `CONTROLLER_DB` | yes | none | the controller's own SQLite file. **Refuses to start if it is inside `CONTROLLER_RUNS_ROOT`** — layout, not permissions, is what keeps this service out of scientific run storage |
| `CONTROLLER_RUNS_ROOT` | yes | none | read-only to this service; it reads `audit.jsonl` to find the pending gate |
| `CONTROLLER_DATA_ROOTS` | no | none (nothing is reachable) | `os.pathsep`-separated. The allowlist. Empty means a path can never be accepted, only a catalog entry |
| `CONTROLLER_CATALOG` | no | none | JSON naming the datasets and manifests on offer. See `config/dataset_catalog.example.json` |
| `CONTROLLER_JUDGE_BACKEND` | no | `stub` (the executor's own default) | passed to the worker unchanged |
| `CONTROLLER_JUDGE_MODEL` | no | unset | passed to the worker unchanged |

None of these reaches a browser. The API returns dataset *references* and
display names; no response contains an absolute path, and
`test_datasets_are_listed_without_any_absolute_path` asserts it.

## The dataset catalog, and why a path is never forwarded

Everything a caller supplies is untrusted text — it may come from a model that
hallucinated it, from a browser field, or from someone who typed `../../etc`.
None is distinguishable from the others by the time it arrives.

So **the worker never receives a path a caller supplied.** It receives an
`input_ref`, and only `app/catalog.py` knows what one resolves to:

    dataset:pbmc_1k_v3      a named entry in the server-side catalog
    manifest:pbmc_study     a named study design
    local:<16 hex>          a path that passed the allowlist; the token is the handle

`local:` exists because "the data is in data/counted/pbmc_1k_v3/outs" is a
sentence people say to an intake assistant, and refusing it outright pushes
them to a worse workaround. What it is not is a path the request then carries:
it is checked once and replaced with a token that means nothing outside this
service's store, and re-checked on every use rather than treated as a standing
permission.

Validation is `Path.resolve()` — which collapses `..` and follows symlinks —
followed by a containment check against the roots. That ordering is the whole
guarantee: a symlink inside an allowed root pointing outside it resolves to its
target and is then rejected, so an escape has to be a real path under a real
root to survive. `Path.is_relative_to` rather than string prefixes, which would
say `/data/private` is inside `/data/priv`.

What the data *is* stays `ingest_validate`'s answer. A request may carry an
`input_kind_hint`; it is recorded as a hint, reported as one, and never reaches
the executor's config.

## API

| method | path | what it does |
|---|---|---|
| `GET` | `/healthz` | liveness |
| `GET` | `/v1/datasets` | the catalog, as references and display names |
| `POST` | `/v1/analysis-requests/preview` | validate and persist a draft. **Creates no run directory and queues no job, under any input** |
| `GET` | `/v1/analysis-requests/{request_id}` | the draft, its job and its run state |
| `GET` | `/v1/analysis-requests/{request_id}/status` | the one shape a polling UI needs |
| `POST` | `/v1/analysis-requests/{request_id}/confirm` | record a human confirmation and queue exactly one job |
| `GET` | `/v1/scientific-runs/{scientific_run_id}/gate` | what this run is waiting on, with its `gate_id` and `generation` |
| `POST` | `/v1/scientific-runs/{scientific_run_id}/gates/{gate_id}/decision` | validate one human answer and queue its continuation |

Request and response shapes: `schemas/analysis_request.schema.json`,
`schemas/analysis_request_preview.schema.json`,
`schemas/analysis_gate_decision.schema.json`.

## Two things confirm has to get right

**A confirmation names the version it confirms.** `config_digest` is taken over
exactly what decides the execution, and a mismatch is a 409. A draft previewed,
edited in another tab, and confirmed under the digest somebody read cannot
happen.

**One confirmation is one analysis.** The store has a unique index —
`jobs_one_start_per_request` — so two clicks, two tabs and a retried POST after
a timeout all converge on one job. The handler's checks are the readable
version; the index is the guarantee. A repeat returns 200 with the original job
and `idempotent_replay: true`, because a retry after a timeout succeeded.

## Two things the gate has to get right

**A decision names the question it answers.** A run can open several gates, and
can open the *same* gate twice — `revise` routes back to the step, which runs
again and can stop again. So the pending gate is identified by `generation`:
how many gates this run has opened, derived from its own audit log. A decision
carrying a stale generation is refused, because the alternative is applying an
answer to a question that was never shown.

**One decision resumes one checkpoint.** `jobs_one_continue_per_generation`
refuses a second continuation for a generation already answered. The worker
then applies it through `src.service.continue_checkpoint_once`, which answers
once and leaves a run that stops again suspended with a *new* pending question
rather than inheriting the answer just given.

Overrides go through `src/registry.py::coerce_overrides` — the same function
the terminal uses. A value typed in a browser and one typed at a prompt take
the same path through the same code.

## Operator identity

Recorded server-side, never taken from a request body, and never reachable by a
model. `apps/web/lib/operator.ts` resolves it; the confirm and decision route
handlers ignore whatever the client sent.

**This is local-development only.** There is no authentication in this slice.
`ANALYSIS_OPERATOR_ID` names the one person running a local stack; unset in
development it becomes `local-operator`, which identifies whoever is running
the server rather than whoever is at the browser. Unset in production it is a
refusal rather than a fallback — see the note in `apps/web/.env.local.example`.

## Local-development limitations

Stated rather than left to be discovered:

- **SQLite, not Postgres.** One writer at a time under WAL. Fine for one worker
  and one browser; not a multi-tenant queue.
- **Polling, not events.** The worker polls the job table; the intake page
  polls the request. No SSE and no AG-UI streaming is implemented, and nothing
  in this repository claims otherwise.
- **No authentication and no authorization.** Anyone who can reach the
  controller can confirm a request. Do not expose this port.
- **One worker assumed.** `claim_next_job` is atomic so two workers cannot take
  the same job, but nothing bounds how many run concurrently or schedules
  across machines.
- **A run interrupted mid-step is failed, not retried.** `reconcile()` never
  re-queues: re-running would start a second analysis under a run id the first
  is still using. Recovery is `--resume-from`, by hand, deliberately.

## Tests

```bash
cd services/controller
.venv/bin/python -m pytest tests -q
```

60 tests, no network, no model, no Cell Ranger, no real dataset. The negative
ones are the point — a path that escapes the allowlist, a digest that is not
checked, a second job for one confirmation, a gate answered twice — because
each would be silent if nothing asserted on it.

The end-to-end path, across both environments and against the real graph, is
`tests/test_web_intake_flow.py` at the repository root.
