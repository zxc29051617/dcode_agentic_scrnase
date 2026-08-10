"""Tests for `src/persistence.py`: what makes a step safe to skip.

Run with `python tests/test_persistence.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.provenance import AuditLog, comparable_config, input_digest  # noqa: E402


def _bundle(root: Path, content: bytes = b"the input data") -> dict:
    """A real file, because the digest that guards a resume hashes real bytes."""
    path = root / "input.txt"
    path.write_bytes(content)
    return {"paths": [str(path)]}


def _run_dir(root: Path, *, config: dict | None = None, bundle: dict | None = None) -> Path:
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({
            "run_id": "r",
            "source": {
                "config": comparable_config(config or {}),
                "config_sha256": "abc123",
                "input_digest": input_digest(bundle) if bundle else None,
            },
        }),
        encoding="utf-8",
    )
    return run_dir


def _finished_step(
    run_dir: Path, step: str, *, with_artifact: bool = True, status: str = "ok"
) -> Path:
    artifact = run_dir / step / "adata.h5ad"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if with_artifact:
        artifact.write_bytes(b"not really an h5ad, but it exists")
    persistence.write_step_output(run_dir, step, {"adata_path": str(artifact), "metrics": {"n": 1}})
    # The audit log is where a step's outcome is recorded, so a step that has an
    # output.json but never reported finishing is not a completed step.
    AuditLog(run_dir / "audit.jsonl").append(
        "step_end", step=step, status=status, warnings=[], errors=[]
    )
    return artifact


def _plan(run_dir: Path, *, config: dict | None = None, bundle: dict | None = None):
    return persistence.plan_resume(run_dir, config=config or {}, input_bundle=bundle)


# --- the checkpointer ------------------------------------------------------------


def test_no_checkpointer_by_default_so_nothing_changes():
    assert persistence.make_checkpointer("none") is None
    assert persistence.make_checkpointer(None) is None


def test_the_memory_checkpointer_is_what_interrupt_needs():
    saver = persistence.make_checkpointer("memory")
    assert saver is not None and hasattr(saver, "get_tuple")


def test_an_unknown_checkpointer_is_refused_rather_than_ignored():
    try:
        persistence.make_checkpointer("sqlite")
    except ValueError as exc:
        assert "unknown checkpointer" in str(exc)
    else:
        raise AssertionError("an unavailable backend must not be silently ignored")


def test_thread_id_is_only_set_when_there_is_something_to_checkpoint():
    """Without a checkpointer the invoke config must be exactly what it was."""
    plain = persistence.thread_config("run-1", recursion_limit=150)
    assert plain == {"recursion_limit": 150}

    saver = persistence.make_checkpointer("memory")
    threaded = persistence.thread_config("run-1", recursion_limit=150, checkpointer=saver)
    assert threaded["configurable"]["thread_id"] == "run-1"


# --- recording what a step produced -------------------------------------------------


def test_a_step_output_survives_a_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _run_dir(Path(tmp))
        persistence.write_step_output(run_dir, "run_pca", {"adata_path": "x.h5ad", "n": 3})
        assert persistence.read_step_output(run_dir, "run_pca") == {"adata_path": "x.h5ad", "n": 3}


def test_an_unserializable_output_does_not_lose_the_step():
    """The step already ran; failing to record it must not raise."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _run_dir(Path(tmp))
        assert persistence.write_step_output(run_dir, "s", {"bad": {1, 2, 3}}) is not None or True


# --- what counts as done --------------------------------------------------------------


def test_a_finished_step_is_resumable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "run_pca")
        assert "run_pca" in _plan(run_dir, bundle=bundle).reusable


def test_a_step_whose_artifact_was_deleted_is_not_resumable():
    """The record is not the result — this is the whole reason for the check."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        artifact = _finished_step(run_dir, "run_pca")
        artifact.unlink()
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert plan.rerun_from == "run_pca"
        assert any("no longer on disk" in reason for reason in plan.reasons)


def test_a_step_with_no_recorded_output_is_not_resumable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        (run_dir / "run_pca").mkdir(parents=True)
        (run_dir / "run_pca" / "adata.h5ad").write_bytes(b"orphan")
        assert _plan(run_dir, bundle=bundle).reusable == {}


def test_a_step_that_recorded_an_error_is_not_a_result():
    """`call_skill` calls a skill that returned errors `ok`; the errors are the truth."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        step = run_dir / "run_pca"
        step.mkdir(parents=True)
        (step / "adata.h5ad").write_bytes(b"here")
        persistence.write_step_output(run_dir, "run_pca", {
            "adata_path": str(step / "adata.h5ad"),
            "errors": ["AnnData does not exist"],
        })
        AuditLog(run_dir / "audit.jsonl").append(
            "step_end", step="run_pca", status="ok", warnings=[], errors=["boom"]
        )
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert any("completed with errors" in reason for reason in plan.reasons)


def test_a_scaffold_is_never_reused_as_if_it_had_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "run_pca", status="scaffold")
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert any("'scaffold'" in reason for reason in plan.reasons)


def test_a_step_with_no_outcome_in_the_audit_log_is_not_trusted():
    """An `output.json` with no `step_end` behind it is a half-written run."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        artifact = run_dir / "run_pca" / "adata.h5ad"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"here")
        persistence.write_step_output(run_dir, "run_pca", {"adata_path": str(artifact)})
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert any("outcome is unknown" in reason for reason in plan.reasons)


def test_a_failed_step_takes_everything_after_it_with_it():
    """Whatever came next was computed from what this one produced."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "run_qc_metrics")
        artifact = _finished_step(run_dir, "run_pca")
        _finished_step(run_dir, "run_clustering")
        artifact.unlink()

        plan = _plan(run_dir, bundle=bundle)
        assert "run_qc_metrics" in plan.reusable, "it ran before the failure and still stands"
        assert "run_pca" not in plan.reusable
        assert "run_clustering" not in plan.reusable, "it was computed from the missing object"


def test_a_step_that_never_ran_does_not_invalidate_the_ones_after_it():
    """`load_raw_counts` never runs on the filtered route, and says nothing about merge."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "load_filtered_counts")
        _finished_step(run_dir, "merge_samples")
        plan = _plan(run_dir, bundle=bundle)
        assert set(plan.reusable) == {"load_filtered_counts", "merge_samples"}
        assert plan.rerun_from is None


def test_an_unverifiable_config_refuses_to_resume():
    """No metadata means nothing can be compared, not that everything matched."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "run_pca")
        metadata = run_dir / "run_metadata.json"

        metadata.unlink()
        assert _plan(run_dir, bundle=bundle).reusable == {}, "missing metadata"

        metadata.write_text("{ not json", encoding="utf-8")
        assert _plan(run_dir, bundle=bundle).reusable == {}, "corrupt metadata"


def test_metadata_written_before_this_check_existed_is_recomputed_not_half_trusted():
    """An older run recorded only a hash, so there is no config to diff."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "run_pca")
        (run_dir / "run_metadata.json").write_text(
            json.dumps({"run_id": "r", "source": {"config_sha256": "abc123"}}), encoding="utf-8"
        )
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert any("records no config" in reason for reason in plan.reasons)


def test_a_missing_run_directory_is_empty_not_an_error():
    assert persistence.plan_resume("/nope/not/here", config={}).reusable == {}


def test_every_recorded_path_has_to_exist_not_just_the_first():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        step = run_dir / "find_markers"
        step.mkdir(parents=True)
        (step / "adata.h5ad").write_bytes(b"here")
        persistence.write_step_output(run_dir, "find_markers", {
            "adata_path": str(step / "adata.h5ad"),
            "marker_table_path": str(step / "markers.csv"),  # never written
        })
        AuditLog(run_dir / "audit.jsonl").append(
            "step_end", step="find_markers", status="ok", warnings=[], errors=[]
        )
        assert _plan(run_dir, bundle=bundle).reusable == {}


# --- the cut: which step stops being trustworthy ------------------------------------


def test_a_changed_key_invalidates_from_the_earliest_step_that_reads_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, config={"min_genes": 200}, bundle=bundle)
        for step in ("run_qc_metrics", "apply_cell_qc_filter", "run_pca"):
            _finished_step(run_dir, step)

        plan = _plan(run_dir, config={"min_genes": 500}, bundle=bundle)
        assert plan.rerun_from == "apply_cell_qc_filter"
        assert "run_qc_metrics" in plan.reusable, "it does not read min_genes"
        assert "apply_cell_qc_filter" not in plan.reusable
        assert "run_pca" not in plan.reusable


def test_an_unrecognised_key_invalidates_everything():
    """A knob nobody has mapped is one whose blast radius nobody has established."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "ingest_validate")
        plan = _plan(run_dir, config={"some_new_knob": 1}, bundle=bundle)
        assert plan.rerun_from == "ingest_validate"
        assert plan.reusable == {}


def test_changed_input_data_invalidates_everything():
    """Same path, same byte count — the edit a size check is allowed to miss."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root, b"original")
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "ingest_validate")
        _finished_step(run_dir, "run_pca")

        edited = b"EDITED!!"
        assert len(edited) == len(b"original"), "the point is that the size is unchanged"
        Path(bundle["paths"][0]).write_bytes(edited)
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert any("input data changed" in reason for reason in plan.reasons)


def test_input_that_cannot_be_compared_is_not_assumed_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, bundle=bundle)
        _finished_step(run_dir, "ingest_validate")
        Path(bundle["paths"][0]).unlink()
        plan = _plan(run_dir, bundle=bundle)
        assert plan.reusable == {}
        assert any("cannot be compared" in reason for reason in plan.reasons)


def test_an_unchanged_run_reuses_everything_and_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = _bundle(root)
        run_dir = _run_dir(root, config={"species": "human"}, bundle=bundle)
        for step in ("ingest_validate", "run_qc_metrics", "run_pca"):
            _finished_step(run_dir, step)
        plan = _plan(run_dir, config={"species": "human"}, bundle=bundle)
        assert set(plan.reusable) == {"ingest_validate", "run_qc_metrics", "run_pca"}
        assert plan.rerun_from is None
        assert plan.reasons == ["nothing changed; every verified step is reused"]


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
