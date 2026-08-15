"""Read-only FastAPI gateway over scientific run directories.

Six endpoints, all GET. There is no route here, and no function this module
calls, that writes a file, starts `src.run`, opens a checkpoint, or answers a
gate — see `docs/copilotkit_product_architecture.md` §3.2 for the contract this
implements and §1.2 for why only the Scientific Worker may write under
`runs/<id>/`. This service never imports `src/`.

Run it:

    GATEWAY_RUNS_ROOT=/path/to/runs uvicorn app.main:app --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import read_model
from .config import get_settings

app = FastAPI(
    title="scRNA-seq scientific run gateway",
    description="Read-only projections over runs/<id>/. No control endpoints exist in this service.",
    version="0.1.0",
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/v1/scientific-runs")
def list_runs() -> list[dict]:
    settings = get_settings()
    return read_model.list_runs(settings.runs_root)


def _not_found(run_id: str) -> HTTPException:
    # Identical response whether `run_id` names nothing or was a path-traversal
    # attempt — see `read_model.resolve_run_dir`. Distinguishing them here
    # would hand an attacker exactly the signal this design refuses to give.
    return HTTPException(status_code=404, detail=f"no scientific run {run_id!r}")


@app.get("/v1/scientific-runs/{run_id}")
def get_run(run_id: str) -> dict:
    settings = get_settings()
    snapshot = read_model.get_run_snapshot(settings.runs_root, run_id)
    if snapshot is None:
        raise _not_found(run_id)
    return snapshot


@app.get("/v1/scientific-runs/{run_id}/steps")
def get_steps(run_id: str) -> list[dict]:
    settings = get_settings()
    steps = read_model.get_steps(settings.runs_root, run_id)
    if steps is None:
        raise _not_found(run_id)
    return steps


@app.get("/v1/scientific-runs/{run_id}/report")
def get_report(run_id: str) -> dict:
    settings = get_settings()
    report = read_model.get_report(settings.runs_root, run_id)
    if report is None:
        raise _not_found(run_id)
    return report


@app.get("/v1/scientific-runs/{run_id}/provenance")
def get_provenance(run_id: str) -> dict:
    settings = get_settings()
    provenance = read_model.get_provenance(settings.runs_root, run_id)
    if provenance is None:
        raise _not_found(run_id)
    return provenance


@app.exception_handler(404)
async def not_found_handler(_request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.detail})
