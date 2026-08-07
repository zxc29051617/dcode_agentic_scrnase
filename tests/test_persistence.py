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


def _run_dir(root: Path, *, config_hash: str = "abc123") -> Path:
    run_dir = root / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"run_id": "r", "source": {"config_sha256": config_hash}}), encoding="utf-8"
    )
    return run_dir


def _finished_step(run_dir: Path, step: str, *, with_artifact: bool = True) -> Path:
    artifact = run_dir / step / "adata.h5ad"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    if with_artifact:
        artifact.write_bytes(b"not really an h5ad, but it exists")
    persistence.write_step_output(run_dir, step, {"adata_path": str(artifact), "metrics": {"n": 1}})
    return artifact


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
        run_dir = _run_dir(Path(tmp))
        _finished_step(run_dir, "run_pca")
        assert "run_pca" in persistence.resumable_steps(run_dir, "abc123")


def test_a_step_whose_artifact_was_deleted_is_not_resumable():
    """The record is not the result — this is the whole reason for the check."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _run_dir(Path(tmp))
        artifact = _finished_step(run_dir, "run_pca")
        artifact.unlink()
        assert persistence.resumable_steps(run_dir, "abc123") == {}


def test_a_step_with_no_recorded_output_is_not_resumable():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _run_dir(Path(tmp))
        (run_dir / "run_pca").mkdir(parents=True)
        (run_dir / "run_pca" / "adata.h5ad").write_bytes(b"orphan")
        assert persistence.resumable_steps(run_dir, "abc123") == {}


def test_a_changed_config_disqualifies_the_whole_directory():
    """A threshold changed at one step changes what every later step should be."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _run_dir(Path(tmp), config_hash="abc123")
        _finished_step(run_dir, "run_pca")
        assert persistence.resumable_steps(run_dir, "abc123")
        assert persistence.resumable_steps(run_dir, "DIFFERENT") == {}


def test_a_missing_run_directory_is_empty_not_an_error():
    assert persistence.resumable_steps("/nope/not/here", "abc123") == {}


def test_every_recorded_path_has_to_exist_not_just_the_first():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _run_dir(Path(tmp))
        step = run_dir / "find_markers"
        step.mkdir(parents=True)
        (step / "adata.h5ad").write_bytes(b"here")
        persistence.write_step_output(run_dir, "find_markers", {
            "adata_path": str(step / "adata.h5ad"),
            "marker_table_path": str(step / "markers.csv"),  # never written
        })
        assert persistence.resumable_steps(run_dir, "abc123") == {}


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
