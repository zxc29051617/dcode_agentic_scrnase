"""Controller tests: what preview accepts, what confirm refuses, and what
neither of them is able to reach.

The negative tests are the point. Every one of them corresponds to a way the
boundary could have been drawn wrongly — a path that escapes the allowlist, a
digest that is not checked, a second job for one confirmation, a gate answered
twice — and each would be silent if nothing asserted on it.

Run with:  pytest  (from services/controller, with the venv active)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import write_gate_close, write_gate_event


# --- the catalog and the allowlist -------------------------------------------


def test_datasets_are_listed_without_any_absolute_path(client):
    body = client.get("/v1/datasets").json()
    assert {d["input_ref"] for d in body["datasets"]} == {"dataset:pbmc_demo", "dataset:fastq_demo"}
    # The whole response, serialized, must not contain a path separator run that
    # would indicate a host path leaked into it.
    text = json.dumps(body)
    assert "/tmp" not in text and "/home" not in text
    assert body["study_designs"][0]["study_design_ref"] == "manifest:pbmc_demo"


def test_a_rejected_catalog_entry_says_why_instead_of_vanishing(client, tmp_path, data_root,
                                                                monkeypatch, runs_root):
    """An empty list must be distinguishable from a mistyped one.

    This is the most likely thing to go wrong when setting the service up, and
    it used to be entirely silent.
    """
    catalog = tmp_path / "broken.json"
    catalog.write_text(json.dumps({"datasets": {
        "typo": {"path": str(data_root / "does_not_exist"), "display_name": "Typo"},
        "outside": {"path": "/etc", "display_name": "Outside the allowlist"},
    }}), encoding="utf-8")
    monkeypatch.setenv("CONTROLLER_CATALOG", str(catalog))
    from app.config import get_settings
    get_settings.cache_clear()

    body = client.get("/v1/datasets").json()
    assert body["datasets"] == []
    reasons = {entry["name"]: entry["reason"] for entry in body["rejected"]}
    assert "nothing at that path" in reasons["typo"]
    assert "outside every directory" in reasons["outside"]
    # A reason names no location, so it stays safe to render in a browser.
    assert "/etc" not in json.dumps(body)
    get_settings.cache_clear()


def test_a_relative_catalog_path_is_relative_to_the_repository(tmp_path, monkeypatch):
    """Not to the working directory, which is services/controller when running.

    A person writing `data/counted/...` means it relative to the project. The
    old behaviour resolved it against the process's cwd, found nothing, and
    dropped the entry without a word.
    """
    from app.catalog import REPO_ROOT, _resolve_entry

    assert _resolve_entry("data/counted/x") == (REPO_ROOT / "data" / "counted" / "x").resolve()
    assert _resolve_entry("/tmp/absolute") == Path("/tmp/absolute").resolve()


def test_a_valid_dataset_reference_previews(client):
    response = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo",
        "species": "human",
        "research_question": "what cell types are present",
        "project": "demo",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["request"]["status"] == "awaiting_confirmation"
    assert body["can_confirm"] is True
    assert body["request"]["validation_errors"] == []
    assert body["request"]["config_digest"].startswith("sha256:")


def test_an_unknown_dataset_reference_is_refused(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:does_not_exist", "species": "human",
        "research_question": "q",
    }).json()
    assert body["can_confirm"] is False
    assert any("not a dataset" in e for e in body["request"]["validation_errors"])


def test_a_path_outside_the_allowlist_is_refused(client, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    body = client.post("/v1/analysis-requests/preview", json={
        "input_path": str(outside), "species": "human", "research_question": "q",
    }).json()
    assert body["can_confirm"] is False
    assert any("outside the directories" in e for e in body["request"]["validation_errors"])
    assert body["request"]["input_ref"] is None


def test_dot_dot_traversal_is_refused(client, data_root):
    escape = str(data_root / "pbmc_demo" / ".." / ".." / ".." / "etc")
    body = client.post("/v1/analysis-requests/preview", json={
        "input_path": escape, "species": "human", "research_question": "q",
    }).json()
    assert body["can_confirm"] is False
    assert body["request"]["input_ref"] is None
    assert body["request"]["validation_errors"]


def test_a_symlink_escaping_the_allowlist_is_refused(client, data_root, tmp_path):
    """The containment check runs *after* resolve(), which is what catches this.

    A prefix test on the unresolved string would pass this path: it starts
    inside the allowed root, and only resolution reveals that it does not end
    there.
    """
    secret = tmp_path / "outside_root"
    secret.mkdir()
    (secret / "matrix.mtx").write_bytes(b"")
    link = data_root / "escape"
    link.symlink_to(secret)

    body = client.post("/v1/analysis-requests/preview", json={
        "input_path": str(link), "species": "human", "research_question": "q",
    }).json()
    assert body["can_confirm"] is False
    assert any("outside the directories" in e for e in body["request"]["validation_errors"])


def test_an_admitted_path_becomes_an_opaque_reference(client, data_root):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_path": str(data_root / "pbmc_demo" / "outs"),
        "species": "human", "research_question": "q",
    }).json()
    # This one matches a catalog entry, so it resolves to the catalog's ref
    # rather than minting a second name for the same data.
    assert body["request"]["input_ref"] == "dataset:pbmc_demo"


def test_an_uncatalogued_but_allowed_path_gets_a_local_token(client, data_root):
    ad_hoc = data_root / "extra"
    ad_hoc.mkdir()
    (ad_hoc / "matrix.mtx").write_bytes(b"")
    body = client.post("/v1/analysis-requests/preview", json={
        "input_path": str(ad_hoc), "species": "human", "research_question": "q",
    }).json()
    ref = body["request"]["input_ref"]
    assert ref.startswith("local:")
    assert str(ad_hoc) not in json.dumps(body["request"])


def test_a_local_token_nobody_issued_is_refused(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "local:" + "0" * 16, "species": "human", "research_question": "q",
    }).json()
    assert any("not a reference this server issued" in e
               for e in body["request"]["validation_errors"])


def test_a_manifest_path_cannot_be_supplied_directly(client, data_root):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
        "study_design_ref": str(data_root / "designs" / "pbmc.csv"),
    }).json()
    assert any("not a study design reference" in e
               for e in body["request"]["validation_errors"])


def test_an_unknown_manifest_reference_is_refused(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
        "study_design_ref": "manifest:nope",
    }).json()
    assert any("not a study design this server offers" in e
               for e in body["request"]["validation_errors"])


# --- species, questions and unsupported analyses ------------------------------


def test_an_invalid_species_is_refused(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "tribble", "research_question": "q",
    }).json()
    assert body["can_confirm"] is False
    assert any("species" in e for e in body["request"]["validation_errors"])


def test_a_missing_data_reference_is_a_question_not_a_guess(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "species": "human", "research_question": "analyse my PBMCs",
    }).json()
    fields = {q["field"] for q in body["request"]["missing_questions"]}
    assert "input_ref" in fields
    assert body["can_confirm"] is False
    assert body["request"]["input_ref"] is None


def test_a_missing_research_question_blocks_confirmation(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human",
    }).json()
    assert {q["field"] for q in body["request"]["missing_questions"]} == {"research_question"}
    assert body["can_confirm"] is False


def test_comparing_samples_asks_for_a_manifest(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human",
        "research_question": "compare cell type composition across conditions",
    }).json()
    assert "study_design_ref" in {q["field"] for q in body["request"]["missing_questions"]}
    assert body["can_confirm"] is False


def test_harmony_without_a_manifest_is_a_question(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "cluster it",
        "analysis": {"integration_mode": "harmony"},
    }).json()
    assert "study_design_ref" in {q["field"] for q in body["request"]["missing_questions"]}


def test_trajectory_inference_is_reported_as_unsupported(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "pseudotime",
        "analysis": {"trajectory": True},
    }).json()
    assert any("trajectory" in u for u in body["request"]["unsupported"])
    # Unsupported is reported, not silently dropped, and it does not appear in
    # the config the executor would receive.
    assert "trajectory" not in body["executor_config_preview"]


def test_turning_off_a_mainline_step_is_unsupported(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
        "analysis": {"annotation": False},
    }).json()
    assert any("annotate_cells" in u for u in body["request"]["unsupported"])


def test_an_unknown_analysis_key_is_a_validation_error(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
        "analysis": {"n_comps": 200},
    }).json()
    assert any("n_comps" in e for e in body["request"]["validation_errors"])
    assert "n_comps" not in body["executor_config_preview"]


def test_public_names_map_to_executor_config_names(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
        "analysis": {"embedding_method": "both", "embedding_dimensions": [2, 3],
                     "resolution": 0.8},
    }).json()
    config = body["executor_config_preview"]
    assert config["method"] == "both"
    assert config["dimensions"] == [2, 3]
    assert config["resolution"] == 0.8
    assert "embedding_method" not in config


def test_the_described_input_kind_is_only_a_hint(client):
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
        "input_kind_hint": "fastq",
    }).json()
    assert any("hint only" in w for w in body["request"]["warnings"])
    assert body["execution_plan"]["route_decided_by"] == "ingest_validate"
    assert body["execution_plan"]["route_is_provisional"] is True
    # The hint does not become the route, and does not reach the config.
    assert "input_kind_hint" not in body["executor_config_preview"]


# --- preview executes nothing -------------------------------------------------


def test_preview_creates_no_run_directory_and_no_job(client, runs_root, store):
    before = list(runs_root.iterdir())
    body = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human", "research_question": "q",
    }).json()
    assert list(runs_root.iterdir()) == before
    assert store.jobs_for_request(body["request"]["request_id"]) == []
    assert body["request"]["scientific_run_id"] is None


def test_the_controller_database_is_not_inside_runs(monkeypatch, runs_root, data_root, catalog_file):
    """Layout is what keeps the controller out of scientific run storage."""
    from app.config import Settings

    with pytest.raises(RuntimeError, match="Only the scientific worker writes"):
        Settings(
            db_path=str(runs_root / "controller.sqlite"),
            runs_root=str(runs_root),
            data_roots=str(data_root),
            catalog_path=str(catalog_file),
        )


# --- confirmation -------------------------------------------------------------


def _valid_draft(client) -> dict:
    return client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo",
        "species": "human",
        "research_question": "what cell types are present",
        "project": "demo",
    }).json()["request"]


def test_confirm_queues_exactly_one_job(client, store, runs_root):
    draft = _valid_draft(client)
    response = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": draft["config_digest"], "operator_id": "alice",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["scientific_run_id"]
    jobs = store.jobs_for_request(draft["request_id"])
    assert len(jobs) == 1 and jobs[0]["kind"] == "start"
    # Confirming queues a job; it does not run one. Still no run directory.
    assert list(runs_root.iterdir()) == []


def test_a_second_confirm_is_idempotent(client, store):
    draft = _valid_draft(client)
    payload = {"config_digest": draft["config_digest"], "operator_id": "alice"}
    first = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json=payload).json()
    second = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json=payload).json()
    assert first["job_id"] == second["job_id"]
    assert first["scientific_run_id"] == second["scientific_run_id"]
    assert second["idempotent_replay"] is True
    assert len(store.jobs_for_request(draft["request_id"])) == 1


def test_a_stale_digest_is_refused(client):
    draft = _valid_draft(client)
    response = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": "sha256:" + "0" * 64, "operator_id": "alice",
    })
    assert response.status_code == 409
    assert "different version" in response.json()["detail"]


def test_confirm_needs_an_operator(client):
    draft = _valid_draft(client)
    response = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": draft["config_digest"], "operator_id": "   ",
    })
    assert response.status_code == 400


def test_a_request_with_open_questions_cannot_be_confirmed(client):
    draft = client.post("/v1/analysis-requests/preview", json={
        "input_ref": "dataset:pbmc_demo", "species": "human",
    }).json()["request"]
    response = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": draft["config_digest"], "operator_id": "alice",
    })
    assert response.status_code == 409


def test_a_confirmed_request_cannot_be_re_previewed(client):
    draft = _valid_draft(client)
    client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": draft["config_digest"], "operator_id": "alice",
    })
    response = client.post("/v1/analysis-requests/preview", json={
        "request_id": draft["request_id"], "input_ref": "dataset:fastq_demo",
        "species": "mouse", "research_question": "something else",
    })
    assert response.status_code == 409


def test_a_request_survives_a_controller_restart(client, settings_env):
    """The store is durable, so a new process answers for an old request."""
    draft = _valid_draft(client)
    client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": draft["config_digest"], "operator_id": "alice",
    })
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as fresh:
        body = fresh.get(f"/v1/analysis-requests/{draft['request_id']}").json()
    assert body["request"]["status"] in {"queued", "running"}
    assert body["job"]["status"] == "queued"


def test_no_secret_appears_in_a_request_response(client):
    draft = _valid_draft(client)
    text = json.dumps(client.get(f"/v1/analysis-requests/{draft['request_id']}").json())
    for forbidden in ("api_key", "apiKey", "SCRNA_JUDGE_API_KEY", "not-needed", "/home/", "/tmp/"):
        assert forbidden not in text


# --- the human gate -----------------------------------------------------------


def _confirmed_run(client, store, runs_root) -> tuple[str, str]:
    draft = _valid_draft(client)
    body = client.post(f"/v1/analysis-requests/{draft['request_id']}/confirm", json={
        "config_digest": draft["config_digest"], "operator_id": "alice",
    }).json()
    return draft["request_id"], body["scientific_run_id"]


def test_a_run_with_no_pending_gate_cannot_be_answered(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    (runs_root / run_id).mkdir(parents=True, exist_ok=True)
    (runs_root / run_id / "run_metadata.json").write_text("{}", encoding="utf-8")
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/gate_0000000000000000/decision",
        json={"decision": "accept", "operator_id": "alice", "expected_generation": 1},
    )
    assert response.status_code == 409
    assert "not waiting at a gate" in response.json()["detail"]


def test_the_pending_gate_is_projected_with_its_generation(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id, revisable=["min_genes", "max_pct_mito"],
                     step="apply_cell_qc_filter")
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    assert state["status"] == "needs_review"
    assert state["generation"] == 1
    assert state["gate_id"].startswith("gate_")
    assert state["pending_gate"]["revisable"] == ["min_genes", "max_pct_mito"]


def test_an_accept_queues_one_continuation(client, store, runs_root):
    request_id, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id)
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "accept", "operator_id": "alice", "expected_generation": 1,
              "rationale": "the warning is expected for this library"},
    )
    assert response.status_code == 200
    jobs = [j for j in store.jobs_for_request(request_id) if j["kind"] == "continue"]
    assert len(jobs) == 1
    assert jobs[0]["payload"]["decision"]["operator"] == "alice"


def test_the_same_gate_cannot_be_answered_twice(client, store, runs_root):
    request_id, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id)
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    payload = {"decision": "accept", "operator_id": "alice", "expected_generation": 1}
    assert client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision", json=payload
    ).status_code == 200
    second = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision", json=payload
    )
    assert second.status_code == 409
    assert "already been answered" in second.json()["detail"]
    assert len([j for j in store.jobs_for_request(request_id) if j["kind"] == "continue"]) == 1


def test_a_stale_generation_is_refused(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id)
    stale = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    write_gate_close(runs_root, run_id)
    write_gate_event(runs_root, run_id, step="annotate_cells")
    current = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    assert current["generation"] == 2

    # The page the operator was looking at carried generation 1.
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{stale['gate_id']}/decision",
        json={"decision": "accept", "operator_id": "alice", "expected_generation": 1},
    )
    assert response.status_code == 409


def test_a_decision_for_another_gate_is_refused(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id)
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/gate_deadbeefdeadbeef/decision",
        json={"decision": "accept", "operator_id": "alice", "expected_generation": 1},
    )
    assert response.status_code == 409
    assert "different gate" in response.json()["detail"]


def test_an_override_the_gate_did_not_offer_is_refused(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id, step="apply_cell_qc_filter",
                     revisable=["min_genes", "min_counts", "max_pct_mito"])
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "revise", "operator_id": "alice", "expected_generation": 1,
              "overrides": {"celltypist_model": "Immune_All_Low.pkl"}},
    )
    assert response.status_code == 422
    assert "not offered at this gate" in json.dumps(response.json())


def test_an_override_that_will_not_convert_is_refused(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id, step="apply_cell_qc_filter", revisable=["min_genes"])
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "revise", "operator_id": "alice", "expected_generation": 1,
              "overrides": {"min_genes": "not a number"}},
    )
    assert response.status_code == 422


def test_an_offered_override_is_converted_by_the_registry(client, store, runs_root):
    """The value reaching the worker is the executor's own conversion of it."""
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id, step="apply_cell_qc_filter", revisable=["min_genes"])
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    body = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "revise", "operator_id": "alice", "expected_generation": 1,
              "overrides": {"min_genes": "250"}},
    ).json()
    assert body["accepted_overrides"] == {"min_genes": 250.0}


def test_overrides_without_revise_are_refused(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id, step="apply_cell_qc_filter", revisable=["min_genes"])
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "accept", "operator_id": "alice", "expected_generation": 1,
              "overrides": {"min_genes": 250}},
    )
    assert response.status_code == 422


def test_a_decision_is_only_accept_revise_or_stop(client, store, runs_root):
    _, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id)
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    response = client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "rerun_everything", "operator_id": "alice", "expected_generation": 1},
    )
    assert response.status_code == 422


def test_a_gate_on_an_unknown_run_is_a_404(client):
    assert client.get("/v1/scientific-runs/../etc/gate").status_code in (404, 307, 404)
    assert client.get("/v1/scientific-runs/nope/gate").status_code == 404


def test_the_controller_writes_nothing_into_the_run_directory(client, store, runs_root):
    request_id, run_id = _confirmed_run(client, store, runs_root)
    write_gate_event(runs_root, run_id)
    before = {p: p.stat().st_mtime_ns for p in (runs_root / run_id).rglob("*")}
    state = client.get(f"/v1/scientific-runs/{run_id}/gate").json()
    client.post(
        f"/v1/scientific-runs/{run_id}/gates/{state['gate_id']}/decision",
        json={"decision": "accept", "operator_id": "alice", "expected_generation": 1},
    )
    client.get(f"/v1/analysis-requests/{request_id}")
    after = {p: p.stat().st_mtime_ns for p in (runs_root / run_id).rglob("*")}
    assert before == after


# --- the species notice ---------------------------------------------------------
#
# The intake has to tell somebody what a species costs *before* they commit to
# it, and it has to be the truth the executor uses rather than a second list
# that drifts. These assert the projection stays tied to `src/species.py`, and
# that "supported" is never allowed to read as "installed".


def test_the_species_list_comes_from_the_scientific_package(client):
    body = client.get("/v1/species").json()
    assert body["available"] is True, "the scientific package was not importable"
    from src.species import SPECIES_PROFILES

    assert {row["species"] for row in body["profiled"]} == set(SPECIES_PROFILES)


def test_supported_and_installed_are_different_questions(client):
    """A species with a profile still needs its reference on this machine.

    Conflating the two promises a run that cannot start, and the person finds
    out at `resolve_reference` instead of at the intake form.
    """
    body = client.get("/v1/species").json()
    for row in body["profiled"]:
        assert "reference_present" in row
        assert isinstance(row["reference_present"], bool)


def test_a_recognised_species_is_not_reported_as_profiled(client):
    """`rat` is understood and has no vetted gene lists. Those are different
    states, and collapsing them is what would make the run invent constants."""
    body = client.get("/v1/species").json()
    profiled = {row["species"] for row in body["profiled"]}
    assert "rat" in body["recognised"]
    assert not profiled & set(body["recognised"])


def test_the_gtf_requirements_are_stated_with_their_consequence(client):
    """Each one fails silently, so a requirement without its consequence is
    just a checklist somebody skims."""
    body = client.get("/v1/species").json()
    assert len(body["gtf_requirements"]) >= 7
    for item in body["gtf_requirements"]:
        assert item["requirement"] and item["why"]


def test_the_species_notice_names_no_path_on_this_machine(client):
    """It is rendered in a browser, like every other projection here."""
    import json as _json

    text = _json.dumps(client.get("/v1/species").json())
    assert "/home/" not in text and "/tmp/" not in text
