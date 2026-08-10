"""Picking a paused run up in a process that did not start it.

Every test here spawns real interpreters. The feature is that a gate survives
the process holding it, and a test that stayed in one process would be testing
`InMemorySaver` with extra steps — the boundary is the thing.

Process A runs until `apply_cell_qc_filter` stops for thresholds and exits with
the graph suspended. Process B, a fresh interpreter, opens the SQLite checkpoint
in the run directory and answers.

Run with `python tests/test_durable_resume.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.provenance import AuditLog  # noqa: E402

DRIVER = Path(__file__).resolve().parent / "durable_driver.py"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _drive(*args: str) -> tuple[int, dict]:
    """Run one driver mode as its own process and read the object it printed."""
    finished = subprocess.run(
        [sys.executable, str(DRIVER), *args],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=1800,
    )
    line = next(
        (l for l in finished.stdout.splitlines() if l.startswith("RESULT ")), None
    )
    assert line is not None, (
        f"driver {args[0]!r} printed no result\n"
        f"--- stdout ---\n{finished.stdout[-2000:]}\n--- stderr ---\n{finished.stderr[-2000:]}"
    )
    return finished.returncode, json.loads(line[len("RESULT "):])


def _pause(workdir: Path) -> dict:
    code, paused = _drive("pause", str(workdir))
    assert code == 0, paused
    assert paused["status"] == "needs_review", "process A has to end suspended, not finished"
    assert paused["checkpoint_exists"], "the checkpoint has to outlive the process"
    return paused


def _audit_events(workdir: Path, run_id: str) -> list[dict]:
    return AuditLog(workdir / "runs" / run_id / "audit.jsonl").read()


def _steps_started_after_resume(events: list[dict]) -> list[str]:
    """Which steps the *continuing* process ran.

    Not answerable from `step_results`: that is reduced state and comes back
    with the checkpoint, so it carries what the first process did as well. The
    audit log is a record of events in order, and `checkpoint_resumed` marks
    where the second process took over.
    """
    marker = next(i for i, e in enumerate(events) if e["event"] == "checkpoint_resumed")
    return [e["step"] for e in events[marker:] if e["event"] == "step_start"]


# --- the process boundary -------------------------------------------------------------


def test_a_run_paused_in_one_process_is_visible_to_another():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        # Process A is gone. Nothing but the run directory survives it.
        code, seen = _drive("describe", str(workdir), paused["run_id"])

    assert code == 0, seen
    assert paused["waiting_at"] == "apply_cell_qc_filter"
    assert paused["checkpoint"].endswith(persistence.CHECKPOINT_NAME)
    assert f"/runs/{paused['run_id']}/" in paused["checkpoint"], \
        "the database belongs to the run, not to a shared store"


def test_answering_accept_in_a_new_process_carries_the_run_forward():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        code, done = _drive("answer", str(workdir), paused["run_id"], "accept")

    assert code == 0, done
    assert done["first_question_step"] == "apply_cell_qc_filter", \
        "process B has to be handed the question process A stopped on"
    assert done["run_id"] == paused["run_id"], "same run, not a new one"
    assert done["status"] in {"completed", "halted"}
    assert done["errors"] == []


def test_answering_revise_in_a_new_process_applies_the_override_and_reruns():
    """The gate's whole point, across a process boundary.

    `apply_cell_qc_filter` stopped because it had no thresholds. Process B
    supplies them, and the step has to run again and actually filter — not
    report the same refusal from the checkpointed state.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        code, done = _drive(
            "answer", str(workdir), paused["run_id"], "revise",
            json.dumps({"min_genes": "1", "max_pct_mito": "100"}),
        )
        events = _audit_events(workdir, paused["run_id"])

    assert code == 0, done
    assert done["first_question_offered"] == ["min_genes", "min_counts", "max_pct_mito"]
    assert done["config_min_genes"] == 1.0, "the override reached the run's config"
    assert done["filter_state"] == "applied", "it refused before; it must not now"
    assert done["thresholds"]["chosen_by"] == "operator"
    assert "apply_cell_qc_filter" in _steps_started_after_resume(events), \
        "the step has to actually run again, in process B"
    revised = [d for d in done["decisions"] if d[1] == "revise"]
    assert revised and revised[0][2] == {"min_genes": 1.0, "max_pct_mito": 100.0}


def test_the_steps_process_a_finished_are_not_repeated_in_process_b():
    """A resume answers a question; it does not re-run the analysis behind it."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        code, done = _drive("answer", str(workdir), paused["run_id"], "accept")
        events = _audit_events(workdir, paused["run_id"])

    assert code == 0, done
    started = [e["step"] for e in events if e["event"] == "step_start"]
    repeated = sorted({s for s in started if started.count(s) > 1})
    assert repeated == [], f"process B re-ran {repeated}"

    after = _steps_started_after_resume(events)
    for step in paused["steps_done"]:
        assert step not in after, f"{step} already ran in process A and ran again in B"

    # And process B does still carry the whole run, not just its own part.
    for step in paused["steps_done"]:
        assert step in done["steps_in_state"], "the checkpoint restores what came before"


def test_the_gate_is_opened_once_even_though_two_processes_touched_it():
    """`interrupt()` re-executes its node on resume, so this is a real risk.

    It does not bite because asking and answering are separate nodes: the node
    that writes `human_gate_open` completed in process A and is not re-entered,
    and the node that re-runs has no side effect before its `interrupt()`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        _drive("answer", str(workdir), paused["run_id"], "accept")
        events = _audit_events(workdir, paused["run_id"])

    opened = [e for e in events if e["event"] == "human_gate_open"
              and e.get("step") == "apply_cell_qc_filter"]
    closed = [e for e in events if e["event"] == "human_gate_close"
              and e.get("step") == "apply_cell_qc_filter"]
    assert len(opened) == 1, f"the question was recorded {len(opened)} times"
    assert len(closed) == 1, f"the answer was recorded {len(closed)} times"

    resumed = [e for e in events if e["event"] == "checkpoint_resumed"]
    assert len(resumed) == 1, "and the pick-up itself is recorded, once"
    assert resumed[0]["waiting_at"] == "apply_cell_qc_filter"


# --- refusing to start over ------------------------------------------------------------


def test_an_unknown_thread_id_fails_instead_of_running_from_the_start():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        before = sorted(p.name for p in (workdir / "runs").iterdir())

        code, failed = _drive("answer", str(workdir), "not-a-real-run-id", "accept")
        after = sorted(p.name for p in (workdir / "runs").iterdir())

    assert code == 2, failed
    assert failed["error"] == "ResumeError"
    assert "no run directory" in failed["message"]
    assert before == after, "a failed continue must not create a run directory"
    assert "unknown" not in after, "and certainly not runs/unknown"
    assert paused["run_id"] in after


def test_a_thread_id_with_no_checkpoint_in_the_database_fails_loudly():
    """The directory exists and the database exists; this thread is not in it."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        # A second run directory carrying the first one's database, so the file
        # is readable but knows nothing about this thread id.
        impostor = workdir / "runs" / "borrowed-id"
        impostor.mkdir(parents=True)
        (impostor / persistence.CHECKPOINT_NAME).write_bytes(
            persistence.checkpoint_path(workdir / "runs" / paused["run_id"]).read_bytes()
        )
        code, failed = _drive("answer", str(workdir), "borrowed-id", "accept")

    assert code == 2, failed
    assert failed["error"] == "ResumeError"
    assert "no checkpoint for thread 'borrowed-id'" in failed["message"]


def test_a_missing_checkpoint_database_fails_instead_of_creating_one():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        database = persistence.checkpoint_path(workdir / "runs" / paused["run_id"])
        database.unlink()

        code, failed = _drive("answer", str(workdir), paused["run_id"], "accept")
        recreated = database.exists()

    assert code == 2, failed
    assert "no checkpoint at" in failed["message"]
    assert not recreated, "a missing database must not be conjured into an empty one"


def test_a_corrupt_checkpoint_database_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        persistence.checkpoint_path(workdir / "runs" / paused["run_id"]).write_bytes(
            b"this is not a sqlite file, not even slightly"
        )
        code, failed = _drive("answer", str(workdir), paused["run_id"], "accept")

    assert code == 2, failed
    assert failed["error"] == "ResumeError"
    assert "readable checkpoint database" in failed["message"]


def test_a_run_that_is_not_waiting_cannot_be_continued():
    """Answering twice is the mistake this catches."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        code, done = _drive("answer", str(workdir), paused["run_id"], "accept")
        assert code == 0, done

        again, failed = _drive("answer", str(workdir), paused["run_id"], "accept")

    assert again == 2, failed
    assert failed["error"] == "ResumeError"
    assert "not waiting at a gate" in failed["message"]


# --- the two resumes stay separate -------------------------------------------------------


def test_the_checkpoint_lives_in_the_run_directory_not_a_shared_store():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        paused = _pause(workdir)
        run_dir = workdir / "runs" / paused["run_id"]
        databases = sorted(p.relative_to(workdir) for p in workdir.rglob("*.sqlite"))

    assert databases == [Path("runs") / paused["run_id"] / persistence.CHECKPOINT_NAME]
    assert persistence.checkpoint_path(run_dir).name == "checkpoint.sqlite"


def test_a_non_interactive_run_writes_no_checkpoint_at_all():
    """Nothing that cannot pause should be paying for a database."""
    from src.policy import GatePolicy
    from src.run import run_workflow
    from tests import fixtures

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        matrix = fixtures.bundle_for(
            {"input_type": "matrix", "matrix_kind": "filtered"}, workdir / "bundle"
        )
        reference = fixtures.make_reference(workdir, "ref", genomes=["GRCh38"])
        final = run_workflow(
            project="plain", input_bundle={"paths": [str(matrix)]},
            config={"species": "human", "transcriptome": str(reference),
                    "min_genes": 1, "max_pct_mito": 100},
            runs_dir=str(workdir / "runs"),
            policy=GatePolicy(headless_decision="accept"),
            checkpointer_kind="none",
        )
        databases = list((workdir / "runs" / final["run_id"]).glob("*.sqlite"))

    assert databases == []


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
