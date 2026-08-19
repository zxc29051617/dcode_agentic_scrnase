"""The write-capable analysis controller. Separate service, separate database.

This is the only service in the repository that accepts a POST which can lead to
a scientific run existing. It is deliberately not `services/gateway`, which is
GET-only and stays that way: mixing the two would mean the read projection and
the mutation surface share an origin, a process and a set of dependencies, and
the read side's guarantee ("there is no code path here that writes") would stop
being a property of the code.

What this service does *not* do is as much of the contract as what it does:

- it never writes under `runs/<scientific_run_id>/` — the worker does
- it never builds a graph, imports `src.graph`, or calls a skill
- it never resumes a checkpoint — it queues a job that asks the worker to
- it never decides a gate answer — it validates one a person submitted

`src.registry` is imported, for `coerce_overrides` only. That module is pure
stdlib and executes nothing; using it here is what stops the controller growing
a second, drifting copy of which parameters a gate may set.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .catalog import Catalog
from .config import Settings, get_settings
from .domain import (
    TERMINAL_STATUSES,
    config_digest,
    digest_source,
    to_executor_config,
    utcnow,
)
from . import gates
from .plan import execution_plan
from .store import Store
from . import validation

app = FastAPI(
    title="scRNA-seq analysis controller",
    description=(
        "Validates analysis requests, records explicit human confirmation, and queues "
        "scientific jobs. Never writes under runs/ and never executes a workflow."
    ),
    version="0.1.0",
)


# --- wiring -------------------------------------------------------------------


def get_store(settings: Settings = Depends(get_settings)) -> Any:
    store = Store(settings.db_path)
    try:
        yield store
    finally:
        store.close()


def get_catalog(settings: Settings = Depends(get_settings)) -> Catalog:
    return Catalog(catalog_path=settings.catalog_path, data_roots=settings.data_roots)


# --- request bodies -----------------------------------------------------------


class AnalysisSettings(BaseModel):
    """The public analysis vocabulary. Extra keys are reported, not ignored.

    `extra="allow"` on purpose: an unknown key must reach `validate_analysis` so
    it can be named in `validation_errors`. Rejecting it at parse time would
    produce a 422 with a schema dump, which tells an intake conversation nothing
    it can act on.
    """

    model_config = {"extra": "allow"}


class PreviewBody(BaseModel):
    request_id: str | None = None
    conversation_id: str | None = None
    project: str | None = None
    species: str | None = None
    research_question: str | None = None
    input_ref: str | None = None
    input_path: str | None = Field(
        default=None,
        description=(
            "A location the user named in conversation. Untrusted: validated against the "
            "server's allowlist and replaced by an input_ref. Never forwarded to a worker."
        ),
    )
    input_kind_hint: str | None = Field(
        default=None,
        description=(
            "What the user said the data is. A hint for the conversation only — "
            "ingest_validate detects the real route from the filesystem and its answer wins."
        ),
    )
    study_design_ref: str | None = None
    analysis: dict[str, Any] = Field(default_factory=dict)


class ConfirmBody(BaseModel):
    config_digest: str
    operator_id: str
    rationale: str | None = None


class DecisionBody(BaseModel):
    decision: Literal["accept", "revise", "stop"]
    operator_id: str
    expected_generation: int
    rationale: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


# --- health -------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1/datasets")
def list_datasets(catalog: Catalog = Depends(get_catalog)) -> dict:
    """What may be analysed, as the browser and the model are allowed to see it.

    No absolute path appears in this response. A caller learns that
    `dataset:pbmc_1k_v3` exists and what it is, never where it is.

    `rejected` carries the entries the catalog offered and the allowlist
    refused, by name and reason — never by path. Without it a mistyped entry
    produces an empty list and no way to tell an empty catalog from a broken
    one, which is the single most likely thing to go wrong when setting this
    up. The reasons name no location, so this stays safe to render.
    """
    return {
        "datasets": catalog.list_datasets(),
        "study_designs": catalog.list_manifests(),
        "rejected": catalog.rejected,
    }


# --- preview ------------------------------------------------------------------


@app.post("/v1/analysis-requests/preview")
def preview(
    body: PreviewBody = Body(...),
    store: Store = Depends(get_store),
    catalog: Catalog = Depends(get_catalog),
) -> dict:
    """Validate a proposed request and persist it as a draft. Executes nothing.

    No run directory is created, no job is queued and no worker is signalled by
    this endpoint under any input. That is what makes it safe for a model to
    call: the worst a wrong draft can do is be wrong on a screen.
    """
    now = utcnow()
    request_id = body.request_id or f"ar_{uuid.uuid4().hex[:20]}"
    existing = store.get_request(request_id)
    if existing and existing.get("status") in {"queued", "running", "needs_review"} | TERMINAL_STATUSES:
        # A request that has already been confirmed describes a run that exists.
        # Re-previewing it would rewrite the record of what was confirmed.
        raise HTTPException(
            status_code=409,
            detail=(
                f"request {request_id} is {existing['status']} and can no longer be edited. "
                f"Start a new request to change the analysis."
            ),
        )

    errors: list[str] = []
    questions: list[dict[str, Any]] = []
    warnings: list[str] = []

    input_ref, input_path, admitted, input_errors, input_questions = validation.validate_input_ref(
        catalog, input_ref=body.input_ref, input_path=body.input_path,
        known_local=store.known_local(),
    )
    errors += input_errors
    questions += input_questions
    if admitted and input_ref:
        # Recorded only once the path has passed every check. The token is the
        # only thing that leaves this service.
        store.admit_path(input_ref, admitted)

    species, species_errors, species_questions = validation.validate_species(body.species)
    errors += species_errors
    questions += species_questions

    research_question, rq_questions = validation.validate_research_question(body.research_question)
    questions += rq_questions

    analysis, analysis_errors, analysis_warnings, unsupported = validation.validate_analysis(body.analysis)
    errors += analysis_errors
    warnings += analysis_warnings

    study_design_ref, manifest_path, manifest_errors = validation.validate_manifest_ref(
        catalog, body.study_design_ref
    )
    errors += manifest_errors

    _, integration_questions = validation.validate_integration(analysis, study_design_ref)
    questions += integration_questions
    questions += validation.comparison_needs_manifest(research_question, study_design_ref)

    if body.input_kind_hint:
        warnings.append(
            f"The described data type ({body.input_kind_hint!r}) is recorded as a hint only. "
            f"ingest_validate detects FASTQ, MTX, .h5 and .h5ad from the filesystem and its "
            f"answer decides the route."
        )

    document: dict[str, Any] = {
        "request_id": request_id,
        "conversation_id": body.conversation_id,
        "input_ref": input_ref,
        "project": (body.project or "").strip() or None,
        "species": species,
        "research_question": research_question,
        "study_design_ref": study_design_ref,
        "analysis": analysis,
        "status": "draft",
        "config_digest": None,
        "created_by": "conversation",
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "missing_questions": questions,
        "validation_errors": errors,
        "warnings": warnings,
        "unsupported": unsupported,
        "scientific_run_id": None,
    }
    document["config_digest"] = config_digest(digest_source(document))

    required_open = [q for q in questions if q.get("required")]
    if errors:
        document["status"] = "rejected" if not input_ref and not species else "draft"
    elif required_open:
        document["status"] = "draft"
    else:
        document["status"] = "awaiting_confirmation"

    store.put_request(document)

    return {
        "request": document,
        "can_confirm": document["status"] == "awaiting_confirmation",
        "execution_plan": execution_plan(
            input_path=input_path,
            analysis=analysis,
            study_design_ref=study_design_ref,
        ),
        "executor_config_preview": (
            to_executor_config(analysis, species=species) if species else {}
        ),
    }


@app.get("/v1/analysis-requests/{request_id}")
def get_request(request_id: str, store: Store = Depends(get_store),
                settings: Settings = Depends(get_settings)) -> dict:
    document = store.get_request(request_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no analysis request {request_id!r}")
    return _with_job_state(document, store, settings)


@app.get("/v1/analysis-requests/{request_id}/status")
def get_request_status(request_id: str, store: Store = Depends(get_store),
                       settings: Settings = Depends(get_settings)) -> dict:
    """The one shape a polling UI needs: where this request is, in one word."""
    document = store.get_request(request_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no analysis request {request_id!r}")
    enriched = _with_job_state(document, store, settings)
    return {
        "request_id": request_id,
        "status": enriched["request"]["status"],
        "scientific_run_id": enriched["request"].get("scientific_run_id"),
        "job": enriched.get("job"),
        "run": enriched.get("run"),
    }


def _with_job_state(document: dict[str, Any], store: Store, settings: Settings) -> dict[str, Any]:
    """Fold the job and the run's own recorded state into a request's status.

    The request row is not the authority on whether the analysis is running —
    the job is, and the run directory is. A request stuck at `queued` because
    the worker updated the job and crashed before updating the request would
    otherwise report the wrong thing forever.
    """
    job = store.start_job(document["request_id"])
    run_state = None
    run_id = document.get("scientific_run_id")
    if run_id:
        run_state = gates.gate_state(settings.runs_root, run_id)

    status = document["status"]
    if status not in TERMINAL_STATUSES:
        if run_state is not None:
            if run_state["status"] == "needs_review":
                status = "needs_review"
            elif run_state["status"] == "completed":
                status = "completed"
            elif job and job["status"] == "failed":
                status = "failed"
            elif job and job["status"] == "running":
                status = "running"
        elif job:
            status = {"queued": "queued", "running": "running",
                      "failed": "failed"}.get(job["status"], status)

    document = {**document, "status": status}
    return {
        "request": document,
        "job": (
            {k: job[k] for k in ("job_id", "kind", "status", "scientific_run_id", "error")}
            if job else None
        ),
        "run": run_state,
        "decisions": store.decisions_for_run(run_id) if run_id else [],
    }


# --- confirm ------------------------------------------------------------------


@app.post("/v1/analysis-requests/{request_id}/confirm")
def confirm(
    request_id: str,
    body: ConfirmBody = Body(...),
    store: Store = Depends(get_store),
    catalog: Catalog = Depends(get_catalog),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Record one human confirmation and queue exactly one scientific job.

    Everything about this endpoint is written to make a second run impossible:
    the state check, the digest check, and finally the unique index in the
    store, which is what actually holds when two requests arrive at once. The
    handler's checks are the readable version; the index is the guarantee.

    This endpoint is reachable from the web app's server-side route handler,
    which is called by a button. It is deliberately *not* exposed as a
    CopilotKit action — see `apps/web/lib/intakeActions.ts`. A model that can
    prepare a request and also confirm it is a model that starts analyses.
    """
    document = store.get_request(request_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no analysis request {request_id!r}")

    operator = (body.operator_id or "").strip()
    if not operator:
        raise HTTPException(status_code=400, detail="operator_id is required to confirm")

    existing_job = store.start_job(request_id)
    if existing_job is not None:
        # Idempotent: the same confirmation twice is the same job once. Returned
        # as 200 with the original job rather than a conflict, because a retried
        # POST after a timeout is the common case and it succeeded.
        return {
            "request_id": request_id,
            "job_id": existing_job["job_id"],
            "scientific_run_id": existing_job["scientific_run_id"],
            "status": store.get_request(request_id)["status"],
            "idempotent_replay": True,
        }

    if document["status"] != "awaiting_confirmation":
        raise HTTPException(
            status_code=409,
            detail=(
                f"request {request_id} is {document['status']!r}; only a request that is "
                f"'awaiting_confirmation' can be confirmed. Preview it again."
            ),
        )
    if body.config_digest != document.get("config_digest"):
        raise HTTPException(
            status_code=409,
            detail=(
                "this confirmation was made against a different version of the request. "
                "Preview it again and confirm the version you are looking at."
            ),
        )
    if document.get("validation_errors"):
        raise HTTPException(status_code=409, detail="the request has validation errors")
    if [q for q in document.get("missing_questions") or [] if q.get("required")]:
        raise HTTPException(status_code=409, detail="the request still has unanswered questions")

    # Re-resolved at confirm rather than trusted from the draft: the catalog or
    # the allowlist may have changed since the preview, and the path that is
    # about to be handed to a worker has to be admissible *now*.
    try:
        input_path = catalog.resolve_input_ref(document["input_ref"], known_local=store.known_local())
        manifest_path = (
            catalog.resolve_manifest_ref(document["study_design_ref"])
            if document.get("study_design_ref") else None
        )
    except Exception as exc:  # noqa: BLE001 - RefError and anything a filesystem raises
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    scientific_run_id = _allocate_run_id()
    payload = {
        "project": document.get("project") or request_id,
        "input_paths": [str(input_path)],
        "sample_manifest": str(manifest_path) if manifest_path else None,
        "config": to_executor_config(document["analysis"], species=document["species"]),
        "judge_backend": settings.judge_backend,
        "judge_model": settings.judge_model,
        "confirmed_by": operator,
        "confirmed_at": utcnow(),
        "confirmation_rationale": body.rationale or "",
        "config_digest": document["config_digest"],
    }
    job = store.enqueue_start(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        request_id=request_id,
        scientific_run_id=scientific_run_id,
        payload=payload,
    )
    store.set_request_status(request_id, "queued", scientific_run_id=job["scientific_run_id"])

    return {
        "request_id": request_id,
        "job_id": job["job_id"],
        "scientific_run_id": job["scientific_run_id"],
        "status": "queued",
        "idempotent_replay": False,
    }


def _allocate_run_id() -> str:
    """The scientific run id, minted here so the job record can name it.

    Same format the executor mints for itself. Assigned before the worker starts
    so that a crash between "job queued" and "graph running" leaves a directory
    the job already claims, rather than an orphan and a retry that starts a
    second analysis.
    """
    from datetime import datetime, timezone

    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


# --- human gate ---------------------------------------------------------------


@app.get("/v1/scientific-runs/{scientific_run_id}/gate")
def get_gate(scientific_run_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """What this run is waiting on, if anything, and what may be changed."""
    state = gates.gate_state(settings.runs_root, scientific_run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no scientific run {scientific_run_id!r}")
    return state


@app.post("/v1/scientific-runs/{scientific_run_id}/gates/{gate_id}/decision")
def gate_decision(
    scientific_run_id: str,
    gate_id: str,
    body: DecisionBody = Body(...),
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Validate one human answer to one pending gate and queue its continuation.

    Four ways a decision is refused, and they are different mistakes:

    - the run is not waiting (nothing to answer)
    - the gate id does not match the pending one (answering a different gate)
    - the generation is behind (answering a question already answered)
    - an override is not offered at this gate, or will not convert

    The last is `src.registry.coerce_overrides`, the same function the terminal
    goes through. Nothing here re-implements what a value means.
    """
    state = gates.gate_state(settings.runs_root, scientific_run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no scientific run {scientific_run_id!r}")
    if state["pending_gate"] is None:
        raise HTTPException(
            status_code=409,
            detail=f"run {scientific_run_id} is not waiting at a gate (status {state['status']!r})",
        )
    if gate_id != state["gate_id"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "that decision is for a different gate than the one this run is waiting at. "
                "Reload the run and answer the question shown."
            ),
        )
    if body.expected_generation != state["generation"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"this decision was made against gate generation {body.expected_generation} "
                f"and the run is now at {state['generation']}. Reload and answer again."
            ),
        )

    offered = list(state["pending_gate"].get("revisable") or [])
    accepted, rejected = _coerce(body.overrides, offered)
    if body.decision == "revise" and rejected:
        raise HTTPException(status_code=422, detail={"rejected_overrides": rejected})
    if body.decision != "revise" and body.overrides:
        raise HTTPException(
            status_code=422,
            detail="overrides are only meaningful with decision='revise'",
        )

    request = store.request_for_run(scientific_run_id)
    decision_id = f"gd_{uuid.uuid4().hex[:20]}"
    job = store.enqueue_continue(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        request_id=(request or {}).get("request_id") or scientific_run_id,
        scientific_run_id=scientific_run_id,
        generation=state["generation"],
        payload={
            "decision": {
                "decision": body.decision,
                "rationale": body.rationale or "",
                "operator": body.operator_id,
                "overrides": accepted,
            },
            "gate_id": gate_id,
            "generation": state["generation"],
        },
    )
    store.record_decision({
        "decision_id": decision_id,
        "scientific_run_id": scientific_run_id,
        "gate_id": gate_id,
        "generation": state["generation"],
        "decision": body.decision,
        "rationale": body.rationale or "",
        "overrides": accepted,
        "operator_id": body.operator_id,
        "outcome": "queued" if job else "duplicate",
    })
    if job is None:
        # The unique index refused a second continuation for this generation.
        # That is a duplicate submission, not a failure: the answer that got
        # there first is being applied.
        raise HTTPException(
            status_code=409,
            detail=(
                f"gate generation {state['generation']} of run {scientific_run_id} has already "
                f"been answered. One decision resumes one checkpoint."
            ),
        )

    if request:
        store.set_request_status(request["request_id"], "running")

    return {
        "decision_id": decision_id,
        "job_id": job["job_id"],
        "scientific_run_id": scientific_run_id,
        "gate_id": gate_id,
        "generation": state["generation"],
        "accepted_overrides": accepted,
        "status": "queued",
    }


def _coerce(raw: dict[str, Any], offered: list[str]) -> tuple[dict[str, Any], list[str]]:
    """`registry.coerce_overrides`, or a refusal when the package is not importable.

    Falling back to "accept everything" if the import fails would turn a
    deployment mistake into a hole in the one allowlist that decides what a
    stranger's POST can put into a scientific config. So the fallback is to
    accept nothing and say why.
    """
    try:
        from src.registry import coerce_overrides  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        if not raw:
            return {}, []
        return {}, [
            f"overrides cannot be validated here: {type(exc).__name__}. "
            f"The controller refuses to apply a value it cannot check."
        ]
    return coerce_overrides(raw, offered)
