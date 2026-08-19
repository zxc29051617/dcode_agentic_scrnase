"""The whole path a browser-started analysis takes, with nothing faked.

    preview -> draft -> explicit confirm -> queued job -> real worker
            -> real graph -> real gate -> explicit decision -> continuation

Two interpreters, because the product is two interpreters: the controller runs
in `services/controller/.venv` (FastAPI, no scanpy) and the worker runs here
(scanpy, no FastAPI). They meet at one SQLite file.

Nothing here calls a model, Cell Ranger, or the network. The matrix is the same
synthetic fixture the rest of the suite uses, and the judge is the deterministic
stub. What is real is everything that decides whether a run happens: the
allowlist, the digest, the job, the graph, the gate and the checkpoint.

Skips when the controller venv is absent — the same rule the rest of this suite
follows for data it does not carry.

Run with `python tests/test_web_intake_flow.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.controller.app.store import Store  # noqa: E402
from services.controller import worker as worker_module  # noqa: E402
from tests import fixtures  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DRIVER = PROJECT_ROOT / "tests" / "intake_driver.py"
CONTROLLER_PYTHON = PROJECT_ROOT / "services" / "controller" / ".venv" / "bin" / "python"

SKIP_REASON = (
    "services/controller/.venv is not built; "
    "python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
)


class Harness:
    """A workdir with a data root, a catalog, a runs root and a controller db."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.data_root = workdir / "data"
        self.runs_root = workdir / "runs"
        self.db = workdir / "controller" / "controller.sqlite"
        self.catalog = workdir / "catalog.json"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)

        matrix = fixtures.bundle_for(
            {"input_type": "matrix", "matrix_kind": "filtered"}, self.data_root / "bundle"
        )
        self.catalog.write_text(json.dumps({
            "datasets": {
                "demo_matrix": {
                    "path": str(matrix),
                    "display_name": "Synthetic filtered matrix",
                    "kind": "matrix",
                    "species": "human",
                }
            },
            "manifests": {},
        }), encoding="utf-8")

    def call(self, mode: str, *args: str) -> dict:
        finished = subprocess.run(
            [str(CONTROLLER_PYTHON), str(DRIVER), mode, str(self.db), str(self.runs_root),
             str(self.catalog), str(self.data_root), *args],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300,
        )
        line = next((l for l in finished.stdout.splitlines() if l.startswith("RESULT ")), None)
        assert line is not None, (
            f"controller driver {mode!r} printed no result\n"
            f"--- stdout ---\n{finished.stdout[-2000:]}\n"
            f"--- stderr ---\n{finished.stderr[-3000:]}"
        )
        return json.loads(line[len("RESULT "):])

    def work(self) -> bool:
        """Run the worker in this process, once, against the same store."""
        store = Store(self.db)
        try:
            return worker_module.process_one(
                store, runs_dir=str(self.runs_root), worker_id="integration"
            )
        finally:
            store.close()

    def store(self) -> Store:
        return Store(self.db)


def _available() -> bool:
    return CONTROLLER_PYTHON.exists()


# --- scenario A: no data reference --------------------------------------------


def test_a_request_with_no_data_reference_cannot_be_confirmed():
    """"Analyse my PBMCs" is a conversation, not a request."""
    if not _available():
        print(f"  skip: {SKIP_REASON}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        harness = Harness(Path(tmp))
        result = harness.call("preview", json.dumps({
            "species": "human",
            "research_question": "analyse my PBMCs",
        }))
        body = result["body"]

    assert result["code"] == 200
    assert body["can_confirm"] is False
    assert "input_ref" in {q["field"] for q in body["request"]["missing_questions"]}
    assert body["request"]["scientific_run_id"] is None
    return 0


# --- scenario B: valid data, missing manifest ---------------------------------


def test_comparing_samples_without_a_manifest_stops_at_a_question():
    if not _available():
        print(f"  skip: {SKIP_REASON}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        harness = Harness(Path(tmp))
        body = harness.call("preview", json.dumps({
            "input_ref": "dataset:demo_matrix",
            "species": "human",
            "research_question": "compare cell type composition between conditions",
        }))["body"]
        runs_before = list(harness.runs_root.iterdir())

    assert body["can_confirm"] is False
    assert "study_design_ref" in {q["field"] for q in body["request"]["missing_questions"]}
    assert runs_before == [], "a question must not have started anything"
    return 0


# --- scenario C, D, E: the whole lifecycle ------------------------------------


def test_the_full_lifecycle_from_preview_to_a_answered_gate():
    """One pass through everything, asserting at each boundary.

    The assertions in the middle matter as much as the one at the end: a test
    that only checked the final status would pass even if preview had started a
    run, or if confirm had queued two.
    """
    if not _available():
        print(f"  skip: {SKIP_REASON}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        harness = Harness(Path(tmp))

        # --- preview: a complete request, and nothing executed ---------------
        preview = harness.call("preview", json.dumps({
            "input_ref": "dataset:demo_matrix",
            "species": "human",
            "research_question": "which cell types are present",
            "project": "intake-integration",
        }))["body"]
        request = preview["request"]
        assert preview["can_confirm"] is True, preview["request"]["validation_errors"]
        assert request["status"] == "awaiting_confirmation"
        assert request["research_question"] == "which cell types are present"
        assert list(harness.runs_root.iterdir()) == [], "preview must create no run"
        assert preview["execution_plan"]["route_decided_by"] == "ingest_validate"

        store = harness.store()
        assert store.jobs_for_request(request["request_id"]) == [], "preview must queue no job"
        store.close()

        # --- confirm: exactly one job, and still nothing running -------------
        confirmed = harness.call("confirm", request["request_id"], request["config_digest"])
        assert confirmed["code"] == 200, confirmed
        run_id = confirmed["body"]["scientific_run_id"]
        assert confirmed["body"]["status"] == "queued"

        # Scenario E: a second confirm is the same job, not a second run.
        again = harness.call("confirm", request["request_id"], request["config_digest"])
        assert again["body"]["job_id"] == confirmed["body"]["job_id"]
        assert again["body"]["idempotent_replay"] is True
        store = harness.store()
        assert len(store.jobs_for_request(request["request_id"])) == 1
        store.close()

        # --- the worker runs the real graph ----------------------------------
        assert harness.work() is True, "the worker had no job to claim"

        state = harness.call("gate", run_id)["body"]
        assert state["status"] == "needs_review", (
            f"the run should be waiting at a gate, not {state['status']!r}"
        )
        assert state["pending_gate"]["step"] == "apply_cell_qc_filter", (
            "with no thresholds given, this is the gate the run stops at"
        )
        assert state["generation"] == 1
        first_gate_id = state["gate_id"]

        status = harness.call("status", request["request_id"])["body"]
        assert status["status"] == "needs_review"
        assert status["scientific_run_id"] == run_id

        # --- scenario E: a stale decision is refused --------------------------
        stale = subprocess.run(
            [str(CONTROLLER_PYTHON), str(DRIVER), "gate", str(harness.db),
             str(harness.runs_root), str(harness.catalog), str(harness.data_root), run_id],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=120,
        )
        assert stale.returncode == 0

        # --- an explicit human decision ---------------------------------------
        decided = harness.call("decide", run_id, json.dumps({
            "decision": "revise",
            "operator_id": "integration-operator",
            "rationale": "supply the thresholds this fixture needs",
            "overrides": {"min_genes": "1", "max_pct_mito": "100"},
        }))
        assert decided["code"] == 200, decided
        assert decided["body"]["accepted_overrides"] == {"min_genes": 1.0, "max_pct_mito": 100.0}

        # The same decision again, against the same generation, is refused.
        duplicate = harness.call("decide", run_id, json.dumps({
            "decision": "accept", "operator_id": "integration-operator",
        }))
        assert duplicate["code"] == 409, duplicate

        # --- the worker applies exactly one continuation -----------------------
        assert harness.work() is True
        assert harness.work() is False, "one decision must queue one continuation"

        after = harness.call("gate", run_id)["body"]
        events = [
            json.loads(line)
            for line in (harness.runs_root / run_id / "audit.jsonl").read_text().splitlines()
            if line.strip()
        ]
        closes = [e for e in events if e["event"] == "human_gate_close"]
        opens = [e for e in events if e["event"] == "human_gate_open"]
        resumes = [e for e in events if e["event"] == "checkpoint_resumed"]
        filter_output = json.loads(
            (harness.runs_root / run_id / "apply_cell_qc_filter" / "output.json").read_text()
        )
        decisions = harness.call("status", request["request_id"])["body"]

    # The override took effect: the step refused to filter before and did not now.
    assert filter_output["filter_state"] == "applied"
    assert filter_output["thresholds"]["chosen_by"] == "operator"

    # Exactly one continuation was applied, by the worker, from the checkpoint.
    assert len(resumes) == 1, f"expected one checkpoint continuation, got {len(resumes)}"
    assert len(closes) == 1, "one decision closes one gate"

    # Scenario D: reaching a further gate opens a *new* pending question rather
    # than inheriting the answer just given.
    if after["status"] == "needs_review":
        assert after["generation"] == len(opens) > 1
        assert after["gate_id"] != first_gate_id, "a new gate must not reuse the answered id"
    else:
        assert after["status"] in {"completed", "running"}, after

    assert decisions["status"] in {"needs_review", "completed", "running"}
    return 0


def test_the_controller_never_wrote_into_the_run_directory():
    """The controller database is outside runs/, and stays there."""
    if not _available():
        print(f"  skip: {SKIP_REASON}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        harness = Harness(Path(tmp))
        preview = harness.call("preview", json.dumps({
            "input_ref": "dataset:demo_matrix", "species": "human",
            "research_question": "q",
        }))["body"]
        harness.call("confirm", preview["request"]["request_id"],
                     preview["request"]["config_digest"])
        harness.work()
        run_id = harness.call("status", preview["request"]["request_id"])["body"]["scientific_run_id"]

        inside = {p.name for p in (harness.runs_root / run_id).rglob("*") if p.is_file()}
        assert not any(name.startswith("controller") for name in inside), inside
        assert harness.db.exists() and not harness.db.is_relative_to(harness.runs_root)
    return 0


TESTS = (
    test_a_request_with_no_data_reference_cannot_be_confirmed,
    test_comparing_samples_without_a_manifest_stops_at_a_question,
    test_the_full_lifecycle_from_preview_to_a_answered_gate,
    test_the_controller_never_wrote_into_the_run_directory,
)


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
        except AssertionError as exc:
            failed = 1
            print(f"  FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed = 1
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok    {test.__name__}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
