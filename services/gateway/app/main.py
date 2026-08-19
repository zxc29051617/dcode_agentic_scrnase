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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import artifacts as artifact_store
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


@app.get("/v1/step-timings")
def step_timings() -> dict:
    """How long each step has actually taken on this machine.

    Registered above the `/{run_id}` route so `step-timings` is never read as
    a run id. Drawn only from completed runs, and reported with the sample
    size and range so a caller can decline to show an estimate it does not
    trust — which is why this exists rather than a table of expected durations
    written by hand.
    """
    settings = get_settings()
    return read_model.step_timings(settings.runs_root)


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


@app.get("/v1/scientific-runs/{run_id}/artifacts")
def list_artifacts(run_id: str) -> list[dict]:
    """Every file in this run the gateway will serve, and nothing else.

    The list is the whole access-control surface: an `artifact_id` that does
    not appear here cannot be fetched, because the content endpoint finds a
    file by rebuilding this list and matching the id against it.
    """
    settings = get_settings()
    run_dir = read_model.resolve_run_dir(settings.runs_root, run_id)
    if run_dir is None:
        raise _not_found(run_id)
    return artifact_store.list_artifacts(run_dir)


@app.get("/v1/scientific-runs/{run_id}/artifacts/{artifact_id}")
def get_artifact(
    run_id: str,
    artifact_id: str,
    download: bool = Query(False, description="send as an attachment instead of inline"),
) -> FileResponse:
    """One artifact's bytes.

    `artifact_id` is an opaque token, never a path — see `app/artifacts.py`.
    HTML artifacts are third-party documents (FastQC, MultiQC, Cell Ranger),
    so they leave here with a sandboxing CSP and `nosniff`: the app renders
    them in a `sandbox="allow-scripts"` iframe, and these headers mean a
    report opened directly in a tab is isolated too rather than running with
    the gateway's origin.
    """
    settings = get_settings()
    run_dir = read_model.resolve_run_dir(settings.runs_root, run_id)
    if run_dir is None:
        raise _not_found(run_id)

    resolved = artifact_store.resolve_artifact(run_dir, artifact_id)
    if resolved is None:
        # Unknown id, rejected symlink and vanished file are one answer on
        # purpose: telling them apart would let a caller probe the filesystem.
        raise HTTPException(status_code=404, detail=f"no artifact {artifact_id!r} in run {run_id!r}")

    if resolved.entry["size_bytes"] > artifact_store.MAX_ARTIFACT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{resolved.entry['name']} is {resolved.entry['size_bytes']} bytes, over the "
                f"{artifact_store.MAX_ARTIFACT_BYTES} byte limit this gateway will serve"
            ),
        )

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": (
            f'{"attachment" if download else "inline"}; filename="{resolved.entry["name"]}"'
        ),
        # Not cached by shared caches: a run directory is not public data.
        "Cache-Control": "private, max-age=60",
    }
    if resolved.entry["is_html"]:
        headers["Content-Security-Policy"] = "sandbox allow-scripts; frame-ancestors 'self'"

    return FileResponse(
        resolved.path,
        media_type=resolved.entry["media_type"],
        headers=headers,
    )


@app.exception_handler(404)
async def not_found_handler(_request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.detail})
