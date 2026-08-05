"""Tests for `src/provenance.py` and the run metadata written at run start.

Run with `python tests/test_provenance.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import provenance  # noqa: E402
from src.state import new_run_state, summarize  # noqa: E402


# --- the audit log ------------------------------------------------------------


def test_the_audit_log_is_append_only():
    with tempfile.TemporaryDirectory() as tmp:
        log = provenance.AuditLog(Path(tmp) / "audit.jsonl")
        log.append("first", step="a")
        log.append("second", step="b")
        records = log.read()
    assert [r["event"] for r in records] == ["first", "second"]


def test_an_unserializable_value_does_not_kill_a_run():
    with tempfile.TemporaryDirectory() as tmp:
        log = provenance.AuditLog(Path(tmp) / "audit.jsonl")
        log.append("odd", value=object())
        records = log.read()
    assert len(records) == 1, "the event must survive even if a field cannot be encoded"


# --- versions ------------------------------------------------------------------


def test_versions_are_recorded_for_the_packages_that_change_results():
    versions = provenance.package_versions()
    # These are hard dependencies; if any is missing the environment is broken.
    for name in ("scanpy", "anndata", "numpy"):
        assert versions[name], f"{name} version not recorded"


def test_an_absent_package_is_none_rather_than_an_error():
    versions = provenance.package_versions(("definitely-not-installed-xyz",))
    assert versions["definitely-not-installed-xyz"] is None


# --- git ------------------------------------------------------------------------


def test_git_state_reports_commit_and_whether_the_tree_was_dirty():
    """A commit alone is not what ran if there were uncommitted edits on top."""
    state = provenance.git_state()
    assert set(state) == {"commit", "branch", "dirty"}
    if state["commit"] is not None:
        assert isinstance(state["dirty"], bool)


# --- hashing ---------------------------------------------------------------------


def test_a_file_digest_changes_when_the_contents_do():
    """A reference or model named the same can still have been swapped."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.pkl"
        path.write_bytes(b"one")
        first = provenance.file_digest(path)
        path.write_bytes(b"two")
        second = provenance.file_digest(path)
    assert first and second and first != second


def test_a_missing_file_digests_to_none():
    assert provenance.file_digest("/nope/missing.pkl") is None
    assert provenance.file_digest(None) is None


def test_config_digest_ignores_key_order():
    assert provenance.config_digest({"a": 1, "b": 2}) == provenance.config_digest({"b": 2, "a": 1})


def test_config_digest_changes_with_a_changed_threshold():
    assert provenance.config_digest({"max_pct_mito": 15}) != provenance.config_digest(
        {"max_pct_mito": 20}
    )


# --- written at run start ----------------------------------------------------------


def test_run_metadata_is_written_when_the_run_starts():
    """Not gathered at report time: that is a different environment."""
    with tempfile.TemporaryDirectory() as tmp:
        state = new_run_state(project="t", config={"species": "human"}, runs_dir=tmp)
        path = Path(state["run_metadata_path"])
        assert path.exists(), "metadata must exist before any step has run"
        meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["run_id"] == state["run_id"]
    assert set(meta) == {"run_id", "runtime", "source", "packages", "seeds"}
    assert meta["runtime"]["python_version"]
    assert meta["source"]["config_sha256"]


def test_the_seed_actually_used_is_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        state = new_run_state(project="t", config={"random_state": 7}, runs_dir=tmp)
        meta = json.loads(Path(state["run_metadata_path"]).read_text(encoding="utf-8"))
    assert meta["seeds"]["random_state"] == 7


def test_the_default_seed_is_recorded_rather_than_left_implicit():
    with tempfile.TemporaryDirectory() as tmp:
        state = new_run_state(project="t", config={}, runs_dir=tmp)
        meta = json.loads(Path(state["run_metadata_path"]).read_text(encoding="utf-8"))
    assert meta["seeds"]["random_state"] == 0


def test_the_summary_points_at_the_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        state = new_run_state(project="t", config={}, runs_dir=tmp)
        report = summarize(state)
    assert report["run_metadata_path"] == state["run_metadata_path"]


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
