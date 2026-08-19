"""Worker tests: the job state machine, and the things the worker must not do.

The executor is faked here on purpose. What these tests are about is the
worker's own contract — one job claimed once, a suspended run recorded as
waiting rather than finished, a restart that never starts a second analysis —
and none of that is about scanpy. `tests/test_web_intake_flow.py` at the
repository root drives the real graph through the real seam.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import write_gate_event

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.controller import worker as worker_module  # noqa: E402


@pytest.fixture
def queued_start(store, runs_root):
    """One queued start job, as confirm would have left it."""
    store.put_request({
        "request_id": "ar_test", "status": "queued", "analysis": {},
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "missing_questions": [], "validation_errors": [],
        "scientific_run_id": "20260101T000000Z-abcdef01",
    })
    return store.enqueue_start(
        job_id="job_1", request_id="ar_test",
        scientific_run_id="20260101T000000Z-abcdef01",
        payload={"project": "demo", "input_paths": ["/allowed/path"],
                 "sample_manifest": None, "config": {"species": "human"}},
    )


def _fake_executor(monkeypatch, *, start_result=None, continue_result=None, record=None):
    def fake():
        def start(**kwargs):
            if record is not None:
                record.append(("start", kwargs))
            return start_result or {"status": "completed", "run_id": kwargs["run_id"]}

        def cont(**kwargs):
            if record is not None:
                record.append(("continue", kwargs))
            return continue_result or {"status": "completed", "run_id": kwargs["run_id"]}

        return start, cont

    monkeypatch.setattr(worker_module, "_executor", fake)


def test_a_completed_run_marks_the_request_completed(store, runs_root, queued_start, monkeypatch):
    _fake_executor(monkeypatch, start_result={"status": "completed"})
    assert worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1") is True
    assert store.get_job("job_1")["status"] == "completed"
    assert store.get_request("ar_test")["status"] == "completed"


def test_a_run_that_stops_at_a_gate_is_waiting_not_finished(store, runs_root, queued_start, monkeypatch):
    _fake_executor(monkeypatch, start_result={
        "status": "needs_review",
        "pending_review": {"gate": "human_gate", "step": "run_qc_metrics"},
    })
    worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1")
    assert store.get_job("job_1")["status"] == "waiting"
    assert store.get_request("ar_test")["status"] == "needs_review"


def test_a_crashing_executor_records_the_failure(store, runs_root, queued_start, monkeypatch):
    def fake():
        def start(**_kwargs):
            raise RuntimeError("scanpy exploded")

        return start, None

    monkeypatch.setattr(worker_module, "_executor", fake)
    worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1")
    job = store.get_job("job_1")
    assert job["status"] == "failed"
    assert "scanpy exploded" in job["error"]
    assert store.get_request("ar_test")["status"] == "failed"


def test_the_worker_starts_the_run_id_the_controller_allocated(store, runs_root, queued_start, monkeypatch):
    """The run id is decided before the run, so the job always claims a directory."""
    record: list = []
    _fake_executor(monkeypatch, record=record)
    worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1")
    kind, kwargs = record[0]
    assert kind == "start"
    assert kwargs["run_id"] == "20260101T000000Z-abcdef01"
    assert kwargs["input_bundle"] == {"paths": ["/allowed/path"]}


def test_a_job_is_claimed_by_exactly_one_worker(store, runs_root, queued_start):
    first = store.claim_next_job(worker_id="w1")
    second = store.claim_next_job(worker_id="w2")
    assert first is not None and first["job_id"] == "job_1"
    assert second is None


def test_an_empty_queue_is_not_an_error(store, runs_root):
    assert worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1") is False


def test_a_stop_decision_leaves_the_request_cancelled(store, runs_root, monkeypatch):
    store.put_request({
        "request_id": "ar_stop", "status": "needs_review", "analysis": {},
        "created_at": "x", "updated_at": "x", "missing_questions": [],
        "validation_errors": [], "scientific_run_id": "run_stop",
    })
    store.enqueue_continue(
        job_id="job_stop", request_id="ar_stop", scientific_run_id="run_stop",
        generation=1, payload={"decision": {"decision": "stop", "operator": "alice"}},
    )
    _fake_executor(monkeypatch, continue_result={
        "status": "halted", "halted": True,
        "halt_reason": "human stopped the run at run_qc_metrics",
    })
    worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1")
    assert store.get_request("ar_stop")["status"] == "cancelled"


def test_the_decision_reaches_the_executor_unchanged(store, runs_root, monkeypatch):
    store.put_request({
        "request_id": "ar_d", "status": "needs_review", "analysis": {}, "created_at": "x",
        "updated_at": "x", "missing_questions": [], "validation_errors": [],
        "scientific_run_id": "run_d",
    })
    decision = {"decision": "revise", "rationale": "too strict",
                "operator": "alice", "overrides": {"min_genes": 250.0}}
    store.enqueue_continue(job_id="job_d", request_id="ar_d", scientific_run_id="run_d",
                           generation=1, payload={"decision": decision})
    record: list = []
    _fake_executor(monkeypatch, record=record)
    worker_module.process_one(store, runs_dir=str(runs_root), worker_id="w1")
    kind, kwargs = record[0]
    assert kind == "continue"
    assert kwargs["decision"] == decision


# --- restarting ---------------------------------------------------------------


def test_a_restart_never_requeues_a_running_job(store, runs_root, queued_start):
    """The failure this prevents is two analyses under one run id."""
    store.claim_next_job(worker_id="dead-worker")
    assert store.get_job("job_1")["status"] == "running"

    write_gate_event(runs_root, "20260101T000000Z-abcdef01")
    notes = worker_module.reconcile(store, runs_root=runs_root)

    assert store.get_job("job_1")["status"] == "waiting"
    assert store.get_request("ar_test")["status"] == "needs_review"
    assert any("waiting at a gate" in n for n in notes)
    # Nothing went back on the queue.
    assert store.claim_next_job(worker_id="w2") is None


def test_a_restart_finds_a_completed_run_completed(store, runs_root, queued_start):
    store.claim_next_job(worker_id="dead-worker")
    run_dir = runs_root / "20260101T000000Z-abcdef01" / "build_report"
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# report", encoding="utf-8")
    (runs_root / "20260101T000000Z-abcdef01" / "run_metadata.json").write_text("{}", encoding="utf-8")

    worker_module.reconcile(store, runs_root=runs_root)
    assert store.get_job("job_1")["status"] == "completed"
    assert store.get_request("ar_test")["status"] == "completed"


def test_a_restart_with_no_run_directory_fails_the_job(store, runs_root, queued_start):
    store.claim_next_job(worker_id="dead-worker")
    worker_module.reconcile(store, runs_root=runs_root)
    assert store.get_job("job_1")["status"] == "failed"
    assert store.get_request("ar_test")["status"] == "failed"
    assert store.claim_next_job(worker_id="w2") is None


def test_a_restart_mid_step_does_not_start_over(store, runs_root, queued_start):
    """A run that was mid-step is failed with a reason, not silently restarted."""
    store.claim_next_job(worker_id="dead-worker")
    run_dir = runs_root / "20260101T000000Z-abcdef01"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text("{}", encoding="utf-8")
    (run_dir / "audit.jsonl").write_text(
        json.dumps({"event": "step_start", "step": "run_pca"}) + "\n", encoding="utf-8"
    )
    worker_module.reconcile(store, runs_root=runs_root)
    job = store.get_job("job_1")
    assert job["status"] == "failed"
    assert "--resume-from" in job["error"]


# --- what the worker never does -----------------------------------------------


WORKER_SOURCE = REPO_ROOT / "services" / "controller" / "worker.py"
CONTROLLER_SOURCE = REPO_ROOT / "services" / "controller" / "app" / "main.py"


def _called_names(path: Path) -> set[str]:
    """Every name this module actually calls, from its AST.

    Parsed rather than grepped because the source *discusses* `input()` — a
    substring search would match the docstring explaining why it is not used,
    which is a test that fails when a comment is written well.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _imported_names(path: Path) -> set[str]:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_the_worker_never_reads_stdin():
    """`input()` is what a terminal gate uses, and no path here reaches one."""
    called = _called_names(WORKER_SOURCE)
    assert "input" not in called
    assert "ask_on_terminal" not in called
    assert "ask_for_overrides" not in called


def test_the_worker_never_uses_a_shell():
    import ast

    tree = ast.parse(WORKER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert keyword.arg != "shell", "the worker must never run a shell"
    assert "system" not in _called_names(WORKER_SOURCE)


def test_the_worker_calls_the_seam_and_not_a_skill():
    """Routing and dispatch stay in the executor: no skill dispatch here."""
    called = _called_names(WORKER_SOURCE)
    assert "call_skill" not in called
    assert "build_graph" not in called
    imported = _imported_names(WORKER_SOURCE)
    assert {"continue_checkpoint_once", "start_detached_run"} <= imported


def test_the_controller_api_never_executes_a_workflow():
    called = _called_names(CONTROLLER_SOURCE)
    for forbidden in ("build_graph", "run_workflow", "continue_workflow",
                      "call_skill", "start_detached_run", "continue_checkpoint_once"):
        assert forbidden not in called, f"the controller must not call {forbidden}"
    # The one thing it may borrow from the scientific package is the allowlist.
    assert _imported_names(CONTROLLER_SOURCE) & {"coerce_overrides"}
