"""The scientific worker: the only process in the product that runs a workflow.

It runs in the scientific environment (`dcode-scrna`), not the controller's
venv, because it imports `src.service` and through it the whole executor. The
controller never imports this module and this module never imports FastAPI —
they share exactly one thing, the SQLite store, which is stdlib on both sides.

    conda activate dcode-scrna
    CONTROLLER_DB=... CONTROLLER_RUNS_ROOT=runs python -m services.controller.worker

## What it does not do

It does not decide anything. Every job it claims already carries a validated
config and, for a gate, a decision a person made and the controller checked.
The worker's whole contribution is to be the process that is allowed to call
`src.service.start_detached_run` and `src.service.continue_checkpoint_once`.

It never reads stdin. `start_detached_run` passes `decide=None`, so a gate
suspends and the call returns rather than blocking on `input()`; there is no
code path from here to `ask_on_terminal`. That is the difference between a run
started from a browser and one started from a terminal, and it is one argument.

## Restarting

A job left `running` by a killed worker is not re-run. On startup, each is
reconciled against what is actually on disk: a run directory that reached a gate
is `needs_review`, one that produced a report is `completed`, and one that shows
neither is marked failed with the reason. Re-queuing it instead would start a
second analysis under a run id the first one is still using.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.controller.app import gates  # noqa: E402
from services.controller.app.store import Store  # noqa: E402


def _executor():
    """The scientific seam, imported late so `--help` works without the env."""
    from src.service import continue_checkpoint_once, start_detached_run

    return start_detached_run, continue_checkpoint_once


def _manifest_design(path: str | None) -> tuple[dict[str, Any], str | None, list[str]]:
    """Load a study manifest the same way the CLI does, or say why not.

    `src.manifest` is the one parser; a second one here would be a second
    opinion about what a valid design is.
    """
    if not path:
        return {}, None, []
    from src import manifest as manifest_module

    parsed, problems = manifest_module.load_manifest(path)
    if problems or parsed is None:
        return {}, None, list(problems)
    return manifest_module.design_state(parsed), parsed.sha256, []


def run_start_job(store: Store, job: dict[str, Any], *, runs_dir: str) -> None:
    """Start one confirmed analysis, and stop when it asks a question."""
    start_detached_run, _ = _executor()
    payload = job["payload"]
    config = dict(payload.get("config") or {})

    study_design, manifest_sha, problems = _manifest_design(payload.get("sample_manifest"))
    if problems:
        store.finish_job(job["job_id"], "failed", error="; ".join(problems))
        store.set_request_status(job["request_id"], "failed")
        return
    if manifest_sha:
        # Travels in config so a changed design invalidates the steps that read
        # it, exactly as `src/run.py` does it for the CLI.
        config["manifest_sha256"] = manifest_sha

    try:
        final = start_detached_run(
            run_id=job["scientific_run_id"],
            project=payload.get("project") or job["request_id"],
            config=config,
            input_bundle={"paths": list(payload.get("input_paths") or [])},
            study_design=study_design,
            runs_dir=runs_dir,
            judge_backend=payload.get("judge_backend"),
            judge_model=payload.get("judge_model"),
        )
    except Exception as exc:  # noqa: BLE001 - a crashed run must be recorded, not lost
        store.finish_job(job["job_id"], "failed", error=f"{type(exc).__name__}: {exc}")
        store.set_request_status(job["request_id"], "failed")
        return

    _settle(store, job, final)


def run_continue_job(store: Store, job: dict[str, Any], *, runs_dir: str) -> None:
    """Apply one operator decision to one suspended run, and stop asking."""
    _, continue_checkpoint_once = _executor()
    payload = job["payload"]
    try:
        final = continue_checkpoint_once(
            run_id=job["scientific_run_id"],
            decision=payload["decision"],
            runs_dir=runs_dir,
        )
    except Exception as exc:  # noqa: BLE001
        store.finish_job(job["job_id"], "failed", error=f"{type(exc).__name__}: {exc}")
        return

    _settle(store, job, final)


def _settle(store: Store, job: dict[str, Any], final: dict[str, Any]) -> None:
    """Record where the run got to, in the job and in the request.

    The run's own state is the authority. `needs_review` is not a failure and
    not a completion — it is a run waiting for a person, which is the state this
    whole product exists to make answerable from a browser.
    """
    status = str(final.get("status") or "running")
    if final.get("halted"):
        status = "halted"
    job_status = {
        "needs_review": "waiting",
        "completed": "completed",
        "failed": "failed",
        "halted": "completed",
    }.get(status, "completed")
    store.finish_job(job["job_id"], job_status, error=final.get("halt_reason"))

    request_status = {
        "needs_review": "needs_review",
        "completed": "completed",
        "failed": "failed",
        "halted": "cancelled" if final.get("halt_reason", "").startswith("human stopped")
        else "failed",
    }.get(status, "running")
    store.set_request_status(
        job["request_id"], request_status, scientific_run_id=job["scientific_run_id"]
    )


def reconcile(store: Store, *, runs_root: Path) -> list[str]:
    """Decide what happened to jobs a previous worker was running when it died.

    Never re-queues. A `running` job whose run reached a gate is waiting; one
    whose run produced a report finished; one that shows neither is failed and
    says so. Re-queuing would run a second analysis under a run id the first is
    still using, which is the failure this function exists to prevent.
    """
    notes: list[str] = []
    for job in store.running_jobs():
        run_id = job.get("scientific_run_id")
        state = gates.gate_state(runs_root, run_id) if run_id else None
        if state is None:
            store.finish_job(
                job["job_id"], "failed",
                error="the worker restarted before this job produced a run directory",
            )
            store.set_request_status(job["request_id"], "failed")
            notes.append(f"{job['job_id']}: no run directory, marked failed")
            continue
        if state["status"] == "needs_review":
            store.finish_job(job["job_id"], "waiting")
            store.set_request_status(job["request_id"], "needs_review", scientific_run_id=run_id)
            notes.append(f"{job['job_id']}: run {run_id} is waiting at a gate")
        elif state["status"] == "completed":
            store.finish_job(job["job_id"], "completed")
            store.set_request_status(job["request_id"], "completed", scientific_run_id=run_id)
            notes.append(f"{job['job_id']}: run {run_id} completed")
        else:
            store.finish_job(
                job["job_id"], "failed",
                error="the worker restarted while this run was mid-step; "
                      "resume it with --resume-from rather than starting again",
            )
            store.set_request_status(job["request_id"], "failed", scientific_run_id=run_id)
            notes.append(f"{job['job_id']}: run {run_id} was interrupted, marked failed")
    return notes


def process_one(store: Store, *, runs_dir: str, worker_id: str) -> bool:
    """Claim and run at most one job. Returns whether there was one."""
    job = store.claim_next_job(worker_id=worker_id)
    if job is None:
        return False
    if job["kind"] == "start":
        run_start_job(store, job, runs_dir=runs_dir)
    elif job["kind"] == "continue":
        run_continue_job(store, job, runs_dir=runs_dir)
    else:
        store.finish_job(job["job_id"], "failed", error=f"unknown job kind {job['kind']!r}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.environ.get("CONTROLLER_DB"))
    parser.add_argument("--runs-dir", default=os.environ.get("CONTROLLER_RUNS_ROOT", "runs"))
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true",
                        help="process the queue until it is empty, then exit")
    args = parser.parse_args(argv)

    if not args.db:
        parser.error("--db (or CONTROLLER_DB) is required")

    runs_root = Path(args.runs_dir).expanduser().resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    store = Store(args.db)
    try:
        for note in reconcile(store, runs_root=runs_root):
            print(f"reconciled {note}", file=sys.stderr)
        print(f"worker {worker_id} watching {args.db}", file=sys.stderr)
        while True:
            worked = process_one(store, runs_dir=str(runs_root), worker_id=worker_id)
            if not worked:
                if args.once:
                    return 0
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
