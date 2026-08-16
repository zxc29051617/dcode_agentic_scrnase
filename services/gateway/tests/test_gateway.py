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

def test_list_runs_returns_both_fixture_runs(client):
    r = client.get("/v1/scientific-runs")
    assert r.status_code == 200
    ids = {row["scientific_run_id"] for row in r.json()}
    assert ids == {"demo-2026-0001", "demo-2026-0002"}


def test_list_runs_reports_correct_status(client):
    rows = {row["scientific_run_id"]: row for row in client.get("/v1/scientific-runs").json()}
    assert rows["demo-2026-0001"]["status"] == "completed"
    assert rows["demo-2026-0002"]["status"] == "halted"


# --- run detail -----------------------------------------------------------------

def test_run_detail_completed_has_no_pending_gate(client):
    r = client.get("/v1/scientific-runs/demo-2026-0001")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["pending_gate"] is None
    assert body["has_report"] is True
    assert len(body["steps"]) > 0


def test_run_detail_halted_exposes_pending_gate(client):
    r = client.get("/v1/scientific-runs/demo-2026-0002")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "halted"
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
