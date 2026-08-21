"""Gateway tests: correct projections over the fixture, and that the four
forbidden things (arbitrary path, artifact write, graph.invoke, resume/gate)
have no route to reach.

Run with:  pytest  (from services/gateway, with the venv active)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_runs"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNS_ROOT", str(FIXTURE_ROOT))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def traversal_client(tmp_path, monkeypatch):
    """A fresh runs_root with an outside-pointing symlink, isolated from the
    checked-in fixture so this test cannot be affected by what that directory
    happens to contain."""
    root = tmp_path / "runs"
    root.mkdir()
    secret = tmp_path / "outside_root.json"
    secret.write_text('{"leak": "should never be served"}', encoding="utf-8")
    (root / "escape").symlink_to(secret.parent)  # a run-id-shaped symlink out of root

    real_run = root / "real-run-0001"
    real_run.mkdir()
    (real_run / "run_metadata.json").write_text(
        json.dumps({"runtime": {"started_at": "2026-01-01T00:00:00Z"}, "source": {}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("GATEWAY_RUNS_ROOT", str(root))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c, root, secret
    get_settings.cache_clear()


# --- healthz -----------------------------------------------------------------

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- list runs -----------------------------------------------------------------

def test_list_runs_returns_every_fixture_run(client):
    r = client.get("/v1/scientific-runs")
    assert r.status_code == 200
    ids = {row["scientific_run_id"] for row in r.json()}
    assert ids == {"demo-2026-0001", "demo-2026-0002", "demo-2026-0003"}


def test_list_runs_reports_correct_status(client):
    rows = {row["scientific_run_id"]: row for row in client.get("/v1/scientific-runs").json()}
    assert rows["demo-2026-0001"]["status"] == "completed"
    # `needs_review`, not `halted`. The executor's vocabulary reserves `halted`
    # for a run a person stopped; a run waiting at a gate has not been stopped,
    # it is waiting. They were the same word here, so the app's "needs
    # attention" counter — which looks for `needs_review` — could never find one.
    assert rows["demo-2026-0002"]["status"] == "needs_review"


# --- run detail -----------------------------------------------------------------

def test_run_detail_completed_has_no_pending_gate(client):
    r = client.get("/v1/scientific-runs/demo-2026-0001")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["pending_gate"] is None
    assert body["has_report"] is True
    assert len(body["steps"]) > 0


def test_run_detail_waiting_exposes_pending_gate(client):
    r = client.get("/v1/scientific-runs/demo-2026-0002")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_review"
    assert body["pending_gate"] is not None
    assert body["pending_gate"]["step"] == "apply_cell_qc_filter"
    assert body["has_report"] is False


def test_unknown_run_id_is_404(client):
    r = client.get("/v1/scientific-runs/does-not-exist")
    assert r.status_code == 404


# --- steps -----------------------------------------------------------------

def test_steps_lists_recorded_steps_with_verdicts(client):
    r = client.get("/v1/scientific-runs/demo-2026-0001/steps")
    assert r.status_code == 200
    steps = r.json()
    names = [s["step"] for s in steps]
    assert "ingest_validate" in names
    assert all(s["verdict"]["verdict"] == "pass" for s in steps)


# --- report -----------------------------------------------------------------

def test_report_available_for_completed_run(client):
    r = client.get("/v1/scientific-runs/demo-2026-0001/report")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert "Synthetic fixture" in body["content"]


# --- upstream QC detail (FastQC / Cell Ranger) --------------------------------

def _upstream(client, step):
    steps = client.get("/v1/scientific-runs/demo-2026-0003/steps").json()
    return next(s for s in steps if s["step"] == step)


def test_fastq_qc_detail_carries_the_per_read_role_numbers(client):
    detail = _upstream(client, "fastq_qc")["upstream_detail"]
    assert detail["per_read_role"]["R2"]["q30_fraction"] == 0.9218
    assert detail["files_total"] == 2
    assert detail["files_shown"] == 2
    assert detail["has_multiqc_report"] is True


def test_fastq_qc_detail_reports_module_failures(client):
    detail = _upstream(client, "fastq_qc")["upstream_detail"]
    assert detail["module_failures"] == {
        "SAMPLE_S1_L001_R2_001.fastq.gz": ["Overrepresented sequences"]
    }


def test_cellranger_detail_passes_the_metrics_summary_through_unrenamed(client):
    # Cell Ranger's own column names are what a person compares against its
    # web summary, so they are passed through as recorded.
    libraries = _upstream(client, "cellranger_count")["upstream_detail"]["libraries"]
    assert libraries[0]["library_id"] == "SAMPLE"
    assert libraries[0]["metrics_summary"]["Estimated Number of Cells"] == "1,206"
    assert libraries[0]["metrics_summary"]["Q30 Bases in RNA Read"] == "92.2%"
    assert libraries[0]["has_web_summary"] is True


def test_upstream_detail_never_leaks_a_host_path(client):
    # The fixture deliberately records absolute paths (`outs`, `bam`,
    # `web_summary`, `report_dir`, `multiqc_report`). None may reach a browser
    # — see docs/copilotkit_product_architecture.md §3.2 on opaque ids.
    body = json.dumps(client.get("/v1/scientific-runs/demo-2026-0003/steps").json())
    for leaked in ("/synthetic/", "possorted_genome_bam", "web_summary.html",
                   "multiqc_report.html", "raw_feature_bc_matrix.h5"):
        assert leaked not in body, f"{leaked} reached the API response"


def test_steps_without_upstream_detail_omit_the_key_entirely(client):
    # Absent, not empty: "this step has no such detail" and "this step
    # recorded none" are different facts.
    steps = client.get("/v1/scientific-runs/demo-2026-0001/steps").json()
    assert all("upstream_detail" not in s for s in steps)


@pytest.fixture
def build_report_client(tmp_path, monkeypatch):
    """A run laid out the way the executor actually writes one.

    `build_report` writes `report.md` into its own step directory, not into
    the run root — so a real kept run has `<run>/build_report/report.md`. The
    checked-in fixture uses the run root, so without this the executor's own
    layout would be untested.
    """
    root = tmp_path / "runs"
    run = root / "real-layout-0001"
    (run / "build_report").mkdir(parents=True)
    (run / "run_metadata.json").write_text(
        json.dumps({"runtime": {"started_at": "2026-01-01T00:00:00Z"}, "source": {}}),
        encoding="utf-8",
    )
    (run / "audit.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00Z", "event": "step_start",
                    "step": "build_report", "run_id": "real-layout-0001"}) + "\n",
        encoding="utf-8",
    )
    (run / "build_report" / "report.md").write_text("# Real layout report\n", encoding="utf-8")

    monkeypatch.setenv("GATEWAY_RUNS_ROOT", str(root))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_report_is_found_in_the_build_report_step_directory(build_report_client):
    r = build_report_client.get("/v1/scientific-runs/real-layout-0001/report")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert "Real layout report" in body["content"]
    assert body["source_path"] == "build_report/report.md"


def test_a_run_whose_report_is_in_build_report_is_not_reported_as_still_running(
    build_report_client,
):
    # The status is derived from whether a report exists, so missing the
    # executor's own layout made a finished run read as `running`.
    detail = build_report_client.get("/v1/scientific-runs/real-layout-0001").json()
    assert detail["has_report"] is True
    assert detail["status"] == "completed"

    listed = build_report_client.get("/v1/scientific-runs").json()
    assert listed[0]["status"] == "completed"


def test_report_unavailable_for_halted_run_states_a_reason(client):
    r = client.get("/v1/scientific-runs/demo-2026-0002/report")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["reason"]
    assert body["content"] is None


# --- provenance / redaction -----------------------------------------------------------------

def test_provenance_never_includes_the_raw_command(client):
    r = client.get("/v1/scientific-runs/demo-2026-0001/provenance")
    assert r.status_code == 200
    body = r.json()
    assert "command" not in body["source"]
    dumped = json.dumps(body)
    assert "SYNTHETIC" not in dumped  # the fixture's fake --input value, from `command`
    assert body["source"]["commit"] == "0" * 40


# --- security boundary: no control routes exist -----------------------------------------------------------------

@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_mutating_method_is_accepted_on_any_route(client, method):
    for path in ("/v1/scientific-runs", "/v1/scientific-runs/demo-2026-0001",
                 "/v1/scientific-runs/demo-2026-0001/steps"):
        r = getattr(client, method)(path)
        assert r.status_code in (404, 405), f"{method.upper()} {path} returned {r.status_code}"


def test_no_start_resume_or_gate_route_exists(client):
    for path in ("/v1/scientific-runs/demo-2026-0001/start",
                 "/v1/scientific-runs/demo-2026-0001/resume",
                 "/v1/scientific-runs/demo-2026-0001/continue",
                 "/v1/scientific-runs/demo-2026-0001/gate",
                 "/v1/scientific-runs/demo-2026-0001/gate/answer"):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code in (404, 405)


# --- path traversal -----------------------------------------------------------------

@pytest.mark.parametrize("bad_id", [
    "..%2F..%2Foutside_root.json",
    "....//outside_root.json",
    "escape/outside_root.json",   # via the symlinked run-id itself
])
def test_traversal_style_run_ids_are_rejected(traversal_client, bad_id):
    client, root, secret = traversal_client
    r = client.get(f"/v1/scientific-runs/{bad_id}")
    assert r.status_code == 404
    assert "leak" not in r.text


def test_symlinked_run_id_does_not_resolve_outside_root(traversal_client):
    client, root, secret = traversal_client
    # "escape" itself is a run-id-shaped symlink pointing outside root; it must
    # not be treated as a valid run (no run_metadata.json survives resolution
    # inside it as this service sees it, and resolve_run_dir refuses anything
    # that resolves outside runs_root).
    r = client.get("/v1/scientific-runs/escape")
    assert r.status_code == 404


def test_real_run_still_works_alongside_the_escape_attempt(traversal_client):
    client, root, secret = traversal_client
    r = client.get("/v1/scientific-runs/real-run-0001")
    assert r.status_code == 200


def test_absolute_path_run_id_is_rejected_by_the_router(traversal_client):
    client, root, secret = traversal_client
    r = client.get(f"/v1/scientific-runs/{secret}")
    assert r.status_code == 404
    assert "leak" not in r.text


# --- liveness: telling a working run from a dead one -----------------------------
#
# Four runs sat in this project's `runs/` reporting `running` for hours after
# the processes writing them had been killed, and nothing distinguished them
# from a run that was genuinely working. The cost was not cosmetic: the wrong
# status produced a wrong diagnosis — a real investigation went looking for a
# bug in `fastq_qc`, which had never failed. It had been killed.


import json
import time

import pytest


def _write_run(root, run_id, events, *, age_seconds=0.0, report=False):
    """A synthetic run directory whose audit log is `age_seconds` old."""
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "run_metadata.json").write_text(
        json.dumps({"runtime": {"started_at": "2026-01-01T00:00:00Z"}, "source": {}}),
        encoding="utf-8",
    )
    (run / "audit.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    if report:
        (run / "report.md").write_text("# report", encoding="utf-8")
    stamp = time.time() - age_seconds
    for path in (run / "audit.jsonl", run):
        os.utime(path, (stamp, stamp))
    return run


import os  # noqa: E402  (used by _write_run above)


@pytest.fixture
def liveness_client(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setenv("GATEWAY_RUNS_ROOT", str(root))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c, root
    get_settings.cache_clear()


MID_STEP = [
    {"event": "step_start", "step": "ingest_validate"},
    {"event": "step_end", "step": "ingest_validate", "status": "ok"},
    {"event": "step_start", "step": "fastq_qc"},
]


def test_a_run_writing_right_now_is_running(liveness_client):
    client, root = liveness_client
    _write_run(root, "fresh-run", MID_STEP, age_seconds=5)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["fresh-run"]["status"] == "running"


def test_a_run_killed_mid_step_is_interrupted_not_running(liveness_client, monkeypatch):
    """The bug this whole section exists for."""
    client, root = liveness_client
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SECONDS", "60")
    _write_run(root, "killed-run", MID_STEP, age_seconds=3600)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["killed-run"]["status"] == "interrupted"


def test_an_interrupted_run_names_the_step_it_died_in(liveness_client, monkeypatch):
    client, root = liveness_client
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SECONDS", "60")
    _write_run(root, "killed-run", MID_STEP, age_seconds=3600)
    body = client.get("/v1/scientific-runs/killed-run").json()
    assert body["status"] == "interrupted"
    assert body["unfinished_step"] == "fastq_qc"


def test_the_evidence_for_the_verdict_travels_with_it(liveness_client):
    """A threshold is a judgement, so what it was applied to is reported too."""
    client, root = liveness_client
    _write_run(root, "some-run", MID_STEP, age_seconds=120)
    row = client.get("/v1/scientific-runs").json()[0]
    assert row["last_activity_at"] is not None
    assert row["last_activity_at"].endswith("+00:00")


def test_a_long_silent_step_is_not_libelled_as_interrupted(liveness_client):
    """`cellranger_count` runs for tens of minutes and writes no audit events.

    Calling a working run interrupted is the worse error of the two: it invites
    somebody to start a second analysis on top of a live one. So the default
    threshold is generous, and this asserts it stays that way.
    """
    client, root = liveness_client
    _write_run(root, "slow-run", MID_STEP, age_seconds=45 * 60)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["slow-run"]["status"] == "running"


def test_a_run_between_steps_is_never_interrupted(liveness_client, monkeypatch):
    """No step is open, so there is nothing to have been interrupted in.

    A run that finished a step and stopped is idle, not broken mid-work, and
    the two want different words.
    """
    client, root = liveness_client
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SECONDS", "60")
    _write_run(root, "between", MID_STEP[:2], age_seconds=3600)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["between"]["status"] == "running"


def test_a_finished_run_is_completed_however_old(liveness_client, monkeypatch):
    client, root = liveness_client
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SECONDS", "60")
    _write_run(root, "old-done", MID_STEP, age_seconds=10 * 86400, report=True)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["old-done"]["status"] == "completed"


def test_a_pending_gate_beats_staleness(liveness_client, monkeypatch):
    """A run waiting for a person has not been interrupted — it is waiting.

    This is the case the old code got wrong twice over: it called it `halted`,
    and staleness would now be tempted to call it `interrupted`. Neither is
    true, and a gate waiting a week is still a gate waiting.
    """
    client, root = liveness_client
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SECONDS", "60")
    _write_run(root, "waiting", MID_STEP + [
        {"event": "human_gate_open", "gate": "human_gate", "step": "fastq_qc",
         "verdict": "warn", "reasons": [], "revisable": []},
    ], age_seconds=7 * 86400)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["waiting"]["status"] == "needs_review"


def test_an_unreadable_threshold_falls_back_to_the_default(liveness_client, monkeypatch):
    """A typo in configuration must not silently disable the check."""
    client, root = liveness_client
    monkeypatch.setenv("GATEWAY_STALE_AFTER_SECONDS", "not-a-number")
    _write_run(root, "typo-env", MID_STEP, age_seconds=10 * 86400)
    rows = {r["scientific_run_id"]: r for r in client.get("/v1/scientific-runs").json()}
    assert rows["typo-env"]["status"] == "interrupted"


# --- step traceability: how a step ran, not only that it did ---------------------
#
# `docs/report_contract.md` calls this tier — "who decided what, and can it be
# rerun" — the reason the pipeline exists. Every field of it was already being
# written to disk and none of it was projected, so the app could show that a
# step passed and not what it passed with.


def test_a_step_reports_the_settings_it_ran_with(client):
    steps = {s["step"]: s for s in client.get("/v1/scientific-runs/demo-2026-0003/steps").json()}
    assert steps, "the fixture should record some steps"
    assert all("settings" in s for s in steps.values())


def test_a_step_reports_its_own_reservations(client):
    """A judge can return `pass` while the step itself recorded a doubt.

    `run_clustering` writes "the smallest cluster has only 8 cells; may be
    noise rather than a population" and is judged `pass`, because the judge is
    asked whether the step ran soundly and by that measure it did. Both facts
    have to reach the screen; only one of them used to.
    """
    steps = {s["step"]: s for s in client.get("/v1/scientific-runs/demo-2026-0003/steps").json()}
    assert all(isinstance(s["notes"], list) for s in steps.values())


def test_settings_never_carry_a_host_path(client):
    """The same rule `get_provenance` follows for `source.command`."""
    body = client.get("/v1/scientific-runs/demo-2026-0003/steps").json()
    text = json.dumps([s["settings"] for s in body])
    assert "/home/" not in text and "/tmp/" not in text
    for step in body:
        for key in step["settings"]:
            assert not key.endswith("_path"), f"{step['step']}.{key} is a path"
            assert not key.endswith("_paths"), f"{step['step']}.{key} is a path"


def test_figures_are_named_by_artifact_id_not_by_filename(client):
    """An id the content endpoint accepts, not a path the browser assembles.

    `list_artifacts` is the whole access-control surface for run files: an id
    absent from it cannot be fetched. Building figure references any other way
    would be a second way to name a file.
    """
    body = client.get("/v1/scientific-runs/demo-2026-0003/steps").json()
    served = {
        a["artifact_id"]
        for a in client.get("/v1/scientific-runs/demo-2026-0003/artifacts").json()
    }
    for step in body:
        for figure in step["figures"]:
            assert figure["artifact_id"] in served, (
                f"{step['step']} names a figure the artifact endpoint will not serve"
            )


def test_a_step_with_no_figure_of_its_own_reports_none(client):
    """Absent, not guessed. Several steps legitimately produce no figure, and a
    prefix this projection has no entry for is shown against nothing rather
    than matched by a name that happens to look close."""
    steps = {s["step"]: s for s in client.get("/v1/scientific-runs/demo-2026-0003/steps").json()}
    for step in steps.values():
        assert isinstance(step["figures"], list)


def test_a_large_nested_setting_says_what_it_cut(client):
    """A `per_cluster` table on a real run is long. It is bounded, and the
    projection declares the cut rather than truncating in silence — the rule
    `src/nodes.py` already applies when it abridges output for the judge."""
    from app.read_model import MAX_SETTING_ENTRIES, _project_settings

    wide = {"per_cluster": {str(i): i for i in range(MAX_SETTING_ENTRIES + 5)}}
    projected = _project_settings(wide)["per_cluster"]
    assert len(projected) == MAX_SETTING_ENTRIES + 1
    assert "5 more not shown" in projected["…"]


def test_the_run_list_names_the_step_a_waiting_run_is_sitting_in(liveness_client, monkeypatch):
    """A row that says only "waiting" tells somebody to go and find out what.

    The detail already carried this; the list is where a person decides which
    run to open, so it is where the fact has to be.
    """
    client, root = liveness_client
    _write_run(root, "at-a-gate", MID_STEP + [
        {"event": "human_gate_open", "gate": "human_gate", "step": "fastq_qc",
         "verdict": "warn", "reasons": [], "revisable": []},
    ], age_seconds=30)
    row = client.get("/v1/scientific-runs").json()[0]
    assert row["status"] == "needs_review"
    assert row["unfinished_step"] == "fastq_qc"


def test_a_finished_run_names_no_unfinished_step_in_the_list(liveness_client):
    client, root = liveness_client
    _write_run(root, "all-done", [
        {"event": "step_start", "step": "ingest_validate"},
        {"event": "step_end", "step": "ingest_validate", "status": "ok"},
    ], age_seconds=30, report=True)
    row = client.get("/v1/scientific-runs").json()[0]
    assert row["unfinished_step"] is None


def test_the_list_names_the_step_a_gate_is_asking_about(liveness_client):
    """Not the step the run is inside — a gate opens after its step has ended.

    `unfinished_step` is null for a run at a gate, which is exactly the run
    somebody scanning the list most needs to identify, so the two facts are
    reported separately rather than one standing in for the other.
    """
    client, root = liveness_client
    _write_run(root, "gated", [
        {"event": "step_start", "step": "apply_cell_qc_filter"},
        {"event": "step_end", "step": "apply_cell_qc_filter", "status": "ok"},
        {"event": "human_gate_open", "gate": "human_gate", "step": "apply_cell_qc_filter",
         "verdict": "warn", "reasons": [], "revisable": []},
    ], age_seconds=30)
    row = client.get("/v1/scientific-runs").json()[0]
    assert row["status"] == "needs_review"
    assert row["unfinished_step"] is None, "the step finished before the gate opened"
    assert row["pending_gate_step"] == "apply_cell_qc_filter"


def test_a_run_with_no_open_gate_names_no_gate_step(liveness_client):
    client, root = liveness_client
    _write_run(root, "ungated", MID_STEP, age_seconds=30)
    row = client.get("/v1/scientific-runs").json()[0]
    assert row["pending_gate_step"] is None


# --- how long things take -------------------------------------------------------
#
# Every duration a person is shown comes from this machine's own finished runs.
# The alternative was a table of expected durations written by hand, which is a
# claim rather than a measurement — and one that `cellranger_count` breaks
# immediately, since a 1k library and a 10k library differ by more than any
# single written number survives.


def _timed_run(root, run_id, pairs, *, report=True):
    """A run whose steps have real start/end timestamps `pairs` seconds apart."""
    from datetime import datetime, timedelta, timezone

    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = []
    for step, seconds in pairs:
        events.append({"ts": t.isoformat(), "event": "step_start", "step": step})
        t += timedelta(seconds=seconds)
        events.append({"ts": t.isoformat(), "event": "step_end", "step": step, "status": "ok"})
    return _write_run(root, run_id, events, age_seconds=60, report=report)


def test_a_step_reports_how_long_it_took(liveness_client):
    client, root = liveness_client
    _timed_run(root, "timed", [("ingest_validate", 3), ("cellranger_count", 1800)])
    steps = {s["step"]: s for s in client.get("/v1/scientific-runs/timed/steps").json()}
    assert steps["ingest_validate"]["duration_seconds"] == 3
    assert steps["cellranger_count"]["duration_seconds"] == 1800


#: `MID_STEP` predates timing and carries no `ts`, which the executor always
#: writes. Timing tests use this instead, so they exercise the shape a real
#: audit log has rather than one only the older fixtures do.
TIMED_MID_STEP = [
    {"ts": "2026-01-01T00:00:00+00:00", "event": "step_start", "step": "ingest_validate"},
    {"ts": "2026-01-01T00:00:05+00:00", "event": "step_end", "step": "ingest_validate", "status": "ok"},
    {"ts": "2026-01-01T00:00:06+00:00", "event": "step_start", "step": "fastq_qc"},
]


def test_a_step_still_running_has_no_duration(liveness_client):
    """Absent, not zero. It has not taken no time; it has not finished."""
    client, root = liveness_client
    _write_run(root, "midway", TIMED_MID_STEP, age_seconds=30)
    steps = {s["step"]: s for s in client.get("/v1/scientific-runs/midway/steps").json()}
    assert steps["fastq_qc"]["duration_seconds"] is None
    assert steps["ingest_validate"]["duration_seconds"] is not None


def test_a_running_run_says_how_long_it_has_been_in_this_step(liveness_client):
    """"In cellranger_count" and "in cellranger_count for 40 minutes" are
    different facts, and only the second tells a working run from a stuck one."""
    client, root = liveness_client
    _write_run(root, "working", TIMED_MID_STEP, age_seconds=30)
    body = client.get("/v1/scientific-runs/working").json()
    assert body["unfinished_step"] == "fastq_qc"
    assert body["current_step_started_at"] is not None
    assert body["current_step_elapsed_seconds"] > 0


def test_a_finished_run_is_not_in_any_step(liveness_client):
    client, root = liveness_client
    _timed_run(root, "done", [("ingest_validate", 3)])
    body = client.get("/v1/scientific-runs/done").json()
    assert body["current_step_started_at"] is None
    assert body["current_step_elapsed_seconds"] is None


def test_one_run_is_not_enough_to_call_a_duration_typical(liveness_client):
    """A single sample presented as "usually takes" is a claim this projection
    has not earned. The same step varies by minutes between runs."""
    client, root = liveness_client
    _timed_run(root, "only-one", [("run_pca", 10)])
    body = client.get("/v1/step-timings").json()
    assert body["steps"] == {}
    assert body["runs_measured"] == 1
    assert body["total_median_seconds"] is None


def test_timings_are_reported_with_their_sample_size_and_range(liveness_client):
    """"About 30 minutes" drawn from runs of 12 and 48 is a different thing to
    know than one drawn from runs of 29 and 31."""
    client, root = liveness_client
    _timed_run(root, "run-a", [("run_pca", 10)])
    _timed_run(root, "run-b", [("run_pca", 30)])
    entry = client.get("/v1/step-timings").json()["steps"]["run_pca"]
    assert entry == {"n": 2, "median_seconds": 20.0, "min_seconds": 10.0, "max_seconds": 30.0}


def test_a_run_that_stopped_at_a_gate_does_not_set_the_expectation(liveness_client):
    """It spent an unbounded amount of that step's wall-clock waiting for a
    person. Counting it would make every future estimate wrong the same way."""
    client, root = liveness_client
    _timed_run(root, "fine-a", [("run_pca", 10)])
    _timed_run(root, "fine-b", [("run_pca", 30)])
    _write_run(root, "at-gate", [
        {"ts": "2026-01-01T00:00:00+00:00", "event": "step_start", "step": "run_pca"},
        {"ts": "2026-01-02T00:00:00+00:00", "event": "step_end", "step": "run_pca", "status": "ok"},
        {"event": "human_gate_open", "gate": "human_gate", "step": "run_pca",
         "verdict": "warn", "reasons": [], "revisable": []},
    ], age_seconds=60)
    entry = client.get("/v1/step-timings").json()["steps"]["run_pca"]
    assert entry["n"] == 2, "the gated run was counted"
    assert entry["max_seconds"] == 30.0


def test_an_unparseable_timestamp_does_not_take_the_step_record_down(liveness_client):
    """Timing is a convenience; the step record it hangs off is not."""
    client, root = liveness_client
    _write_run(root, "bad-ts", [
        {"ts": "not-a-time", "event": "step_start", "step": "run_pca"},
        {"ts": "also-not", "event": "step_end", "step": "run_pca", "status": "ok"},
    ], age_seconds=60, report=True)
    steps = client.get("/v1/scientific-runs/bad-ts/steps").json()
    assert steps[0]["step"] == "run_pca"
    assert steps[0]["duration_seconds"] is None


def test_a_step_with_no_timestamp_at_all_is_absent_rather_than_guessed(liveness_client):
    """Older runs, written before `ts` was recorded, still list their steps.

    The one thing this must not do is invent a duration for them — a made-up
    number here would flow straight into `/v1/step-timings` and become the
    expectation shown to somebody deciding whether to keep waiting.
    """
    client, root = liveness_client
    _write_run(root, "no-ts", MID_STEP, age_seconds=30)
    body = client.get("/v1/scientific-runs/no-ts").json()
    assert body["current_step_elapsed_seconds"] is None
    steps = {s["step"]: s for s in client.get("/v1/scientific-runs/no-ts/steps").json()}
    assert steps["ingest_validate"]["duration_seconds"] is None


# --- the read model derives each fact once -------------------------------------
#
# `RunAudit` was introduced to stop the projections re-deriving the same things.
# `get_run_snapshot` called `_current_step_started` three times in three
# adjacent entries; `_derive_status` re-found the pending gate and the
# unfinished step that its caller then found again. None of that was visible in
# a response, which is exactly why it needs a test rather than a reading.
#
# The load-bearing assertion is the first one: the projections must agree
# exactly with what the free functions produce. The rest is about not doing the
# work twice.


def test_the_run_audit_agrees_with_the_free_functions(client):
    """The refactor's contract: same answers, derived once instead of twice."""
    from app import read_model as rm

    for name in ("demo-2026-0001", "demo-2026-0002", "demo-2026-0003"):
        run_dir = FIXTURE_ROOT / name
        audit = rm.RunAudit(run_dir)
        events = rm._read_audit(run_dir)

        assert audit.events == events
        assert audit.step_order == rm._step_order(events)
        assert audit.step_status == rm._step_status(events)
        assert audit.verdicts == rm._judge_verdicts(events)
        assert audit.durations == rm._step_durations(events)
        assert audit.pending_gate == rm._pending_gate(events)
        assert audit.unfinished_step == rm._unfinished_step(events)
        assert audit.current_step_started == rm._current_step_started(events)
        assert audit.has_report == rm._has_report(run_dir)
        assert audit.status() == rm._derive_status(
            events,
            has_report=rm._has_report(run_dir),
            last_activity=rm._last_activity(run_dir),
        )


def test_the_audit_log_is_read_once_per_run(client, monkeypatch):
    """Not a micro-optimisation: it is per run, for every run, every request."""
    from app import read_model as rm

    reads: list[str] = []
    original = rm._read_audit

    def counted(run_dir):
        reads.append(run_dir.name)
        return original(run_dir)

    monkeypatch.setattr(rm, "_read_audit", counted)

    rm.get_run_snapshot(FIXTURE_ROOT, "demo-2026-0002")
    assert reads == ["demo-2026-0002"], f"audit.jsonl read {len(reads)} times"

    reads.clear()
    rm.list_runs(FIXTURE_ROOT)
    assert len(reads) == len(set(reads)), f"a run's audit log was read twice: {reads}"


def test_a_snapshot_derives_the_current_step_once(client, monkeypatch):
    """The three adjacent calls this refactor removed."""
    from app import read_model as rm

    calls = 0
    original = rm._current_step_started

    def counted(events):
        nonlocal calls
        calls += 1
        return original(events)

    monkeypatch.setattr(rm, "_current_step_started", counted)
    rm.get_run_snapshot(FIXTURE_ROOT, "demo-2026-0002")
    assert calls == 1, f"_current_step_started called {calls} times, expected 1"


def test_status_does_not_re_derive_what_it_was_given(client, monkeypatch):
    """`_derive_status` used to find the gate its caller had already found."""
    from app import read_model as rm

    calls = 0
    original = rm._pending_gate

    def counted(events):
        nonlocal calls
        calls += 1
        return original(events)

    monkeypatch.setattr(rm, "_pending_gate", counted)
    rm.get_run_snapshot(FIXTURE_ROOT, "demo-2026-0002")
    assert calls == 1, f"_pending_gate called {calls} times, expected 1"


def test_none_is_still_an_answer_and_not_a_missing_argument(client):
    """`pending_gate=None` means "no gate", not "work it out yourself".

    A `None` default would have made those indistinguishable, and a run whose
    caller knew there was no gate would have been re-scanned for one.
    """
    from app import read_model as rm

    gated = rm._read_audit(FIXTURE_ROOT / "demo-2026-0002")
    assert rm._pending_gate(gated) is not None, "fixture no longer has an open gate"

    # Told there is no gate, it believes that rather than looking.
    assert rm._derive_status(
        gated, has_report=False, last_activity=None, pending_gate=None,
    ) != "needs_review"
    # Told nothing, it looks, and finds one.
    assert rm._derive_status(gated, has_report=False, last_activity=None) == "needs_review"


def test_nothing_is_cached_between_calls(client):
    """Two RunAudit objects over one directory are independent.

    The gateway's guarantee is that it rebuilds from disk every request. A
    cache keyed on the run directory would break that; per-object memoisation
    does not, and this is what says so.
    """
    from app import read_model as rm

    run_dir = FIXTURE_ROOT / "demo-2026-0002"
    first, second = rm.RunAudit(run_dir), rm.RunAudit(run_dir)
    assert first.events == second.events
    assert first.events is not second.events, "state leaked between two reads"
