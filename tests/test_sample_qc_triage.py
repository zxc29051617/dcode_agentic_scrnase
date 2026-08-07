"""Tests for `sample_qc_triage`: which libraries enter the run.

The point of pinning this one is that it must never quietly shrink a study.
This pipeline already shipped a bug where a two-sample run analysed one library
and reported it as the whole thing; a triage step is where that mistake would
be easiest to make on purpose.

Run with `python tests/test_sample_qc_triage.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402

triage = load_skill("sample_qc_triage")


def _metrics(root: Path, rows: str, name: str = "metrics.csv") -> Path:
    path = root / name
    path.write_text(rows, encoding="utf-8")
    return path


TABLE = (
    "sample,mean_reads_per_cell,saturation\n"
    "libA,45000,0.82\n"
    "libB,38000,0.75\n"
    "libC,1200,0.10\n"
)


def _run(root: Path, *, table: str = TABLE, known=("libA", "libB", "libC"), **config):
    path = _metrics(root, table)
    return triage.run({
        "config": {"qc_metrics_csv": str(path), **config},
        "artifacts": {"ingest_validate": {"matrix_paths": {n: f"/data/{n}.h5" for n in known}}},
        "sample_metadata": {},
    })


# --- it reports, it does not prune ------------------------------------------------


def test_nothing_is_excluded_without_being_asked():
    """A flagged sample is a warning, not a removal."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), sample_thresholds={"mean_reads_per_cell": {"min": 10000}})
    assert result["errors"] == []
    assert result["triage_state"] == "needs_review"
    assert result["excluded_samples"] == []
    assert set(result["included_samples"]) == {"libA", "libB", "libC"}
    assert result["per_sample"]["libC"]["flagged"] is True
    assert any("nothing was excluded" in w for w in result["warnings"])


def test_a_clean_table_needs_no_decision():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), sample_thresholds={"mean_reads_per_cell": {"min": 500}})
    assert result["triage_state"] == "no_action"
    assert result["warnings"] == []


def test_the_evidence_prices_each_candidate_bound():
    """The operator picks the number; this shows what each one would cost."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp))
    preview = result["evidence"]["preview"]["mean_reads_per_cell"]
    assert preview and all({"min", "at_percentile", "would_exclude"} <= set(r) for r in preview)
    assert result["evidence"]["distributions"]["saturation"]["percentiles"]["50"]


# --- excluding, when told ------------------------------------------------------------


def test_an_explicit_exclusion_is_applied_and_named():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), exclude_samples=["libC"])
    assert result["triage_state"] == "applied"
    assert result["excluded_samples"] == ["libC"]
    assert set(result["included_samples"]) == {"libA", "libB"}
    assert set(result["matrix_paths"]) == {"libA", "libB"}


def test_excluding_an_unknown_sample_is_an_error():
    """Silently ignoring it would let a typo look like a successful exclusion."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), exclude_samples=["libZ"])
    assert result["errors"] and "not a library" in result["errors"][0]


def test_excluding_everything_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), exclude_samples=["libA", "libB", "libC"])
    assert result["errors"] and "nothing to analyse" in result["errors"][0]


# --- the table has to describe this run ------------------------------------------------


def test_a_duplicated_sample_name_is_an_error():
    """Two rows under one name is how two libraries become one."""
    table = TABLE + "libA,50000,0.9\n"
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), table=table)
    assert result["errors"] and "more than once" in result["errors"][0]


def test_a_library_missing_from_the_table_is_flagged_not_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), known=("libA", "libB", "libC", "libD"))
    assert any("absent from the metrics table" in w and "libD" in w for w in result["warnings"])


def test_a_table_row_that_is_not_in_the_run_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), known=("libA", "libB"))
    assert any("not libraries in this run" in w and "libC" in w for w in result["warnings"])


def test_an_unrecognisable_sample_column_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(Path(tmp), table="x,y\n1,2\n")
    assert result["errors"] and "which column names the sample" in result["errors"][0]


def test_asking_for_triage_with_no_table_says_so():
    """Believing triage happened when it did not is the failure to avoid."""
    result = triage.run({"config": {}, "artifacts": {}, "sample_metadata": {}})
    assert result["triage_state"] == "no_action"
    assert any("no metrics table was supplied" in w for w in result["warnings"])


def test_a_missing_csv_is_an_error():
    result = triage.run({"config": {"qc_metrics_csv": "/nope.csv"}, "artifacts": {}})
    assert result["errors"] and "does not exist" in result["errors"][0]


# --- the exclusion actually reaches the steps that would do the work ------------------


def test_the_matrix_route_honours_the_exclusion():
    """Recording an exclusion and then counting the sample anyway is the bug."""
    preflight = load_skill("matrix_preflight")
    resolved = preflight._resolve_matrices({
        "artifacts": {
            "ingest_validate": {"matrix_paths": {"libA": "/a.h5", "libB": "/b.h5"}},
            "sample_qc_triage": {"matrix_paths": {"libA": "/a.h5"}},
        }
    })
    assert set(resolved) == {"libA"}


def test_the_matrix_route_is_untouched_when_nothing_was_excluded():
    preflight = load_skill("matrix_preflight")
    resolved = preflight._resolve_matrices({
        "artifacts": {
            "ingest_validate": {"matrix_paths": {"libA": "/a.h5", "libB": "/b.h5"}},
            "sample_qc_triage": {"matrix_paths": {}},
        }
    })
    assert set(resolved) == {"libA", "libB"}


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
