"""Unit tests for `count_matrix_classify`.

Run with `python tests/test_count_matrix_classify.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402
from tests import paths  # noqa: E402
from tests import fixtures  # noqa: E402

classify = load_skill("count_matrix_classify")

REAL_OUTS = paths.COUNT_OUTS / "pbmc_1k_v3" / "outs"


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


# --- the decision rule, on evidence alone ----------------------------------


def test_any_empty_barcode_means_raw():
    """A cell caller removes empty droplets, so their presence settles it."""
    verdict, reasons = classify.classify({"n_barcodes": 5_000, "n_empty_barcodes": 12})
    assert verdict == "raw"
    assert "no detected genes" in reasons[0]


def test_a_huge_barcode_list_is_raw_even_with_no_empties():
    verdict, reasons = classify.classify({"n_barcodes": 300_000, "n_empty_barcodes": 0})
    assert verdict == "raw"
    assert "far more than any cell caller returns" in reasons[0]


def test_a_small_barcode_list_with_no_empties_is_filtered():
    verdict, _ = classify.classify({"n_barcodes": 1_218, "n_empty_barcodes": 0})
    assert verdict == "filtered"


def test_the_middle_range_is_left_unknown():
    """Between the ceiling and the threshold, the count alone cannot decide."""
    verdict, reasons = classify.classify({"n_barcodes": 75_000, "n_empty_barcodes": 0})
    assert verdict == "unknown"
    assert "cannot decide" in reasons[0]


def test_no_barcode_count_is_unknown_not_a_guess():
    verdict, _ = classify.classify({"format": "h5ad"})
    assert verdict == "unknown"


def test_unknown_leaves_cell_calling_undecided_rather_than_false():
    """`None` must not be read downstream as 'cell calling is already done'."""
    result = classify._result(matrix_class="unknown")
    assert result["needs_cell_calling"] is None
    assert classify._result(matrix_class="raw")["needs_cell_calling"] is True
    assert classify._result(matrix_class="filtered")["needs_cell_calling"] is False


# --- reading each format ----------------------------------------------------


def test_mtx_header_gives_dimensions_without_reading_the_body():
    with tempfile.TemporaryDirectory() as tmp:
        directory = fixtures.make_mtx_dir(Path(tmp), "filtered_feature_bc_matrix")
        (directory / "barcodes.tsv.gz").unlink()
        (directory / "matrix.mtx.gz").unlink()
        import gzip

        with gzip.open(directory / "barcodes.tsv.gz", "wt") as handle:
            handle.write("".join(f"BC{i}\n" for i in range(10)))
        with gzip.open(directory / "matrix.mtx.gz", "wt") as handle:
            handle.write("%%MatrixMarket matrix coordinate integer general\n%\n100 10 250\n")
        evidence = classify.gather_evidence(directory)

    assert evidence["format"] == "mtx_dir"
    assert evidence["n_barcodes"] == 10
    assert evidence["n_features"] == 100
    assert evidence["nnz"] == 250
    assert "n_empty_barcodes" not in evidence, "250 entries over 10 barcodes proves nothing"


def test_fewer_entries_than_barcodes_proves_some_are_empty():
    """Pigeonhole: nnz < n_barcodes means at least the difference must be empty."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "raw_feature_bc_matrix"
        directory.mkdir(parents=True)
        import gzip

        with gzip.open(directory / "barcodes.tsv.gz", "wt") as handle:
            handle.write("".join(f"BC{i}\n" for i in range(1000)))
        with gzip.open(directory / "matrix.mtx.gz", "wt") as handle:
            handle.write("%%MatrixMarket matrix coordinate integer general\n100 1000 400\n")
        (directory / "features.tsv.gz").touch()
        evidence = classify.gather_evidence(directory)

    assert evidence["n_empty_barcodes"] == 600
    assert evidence["n_empty_barcodes_is_lower_bound"] is True
    assert classify.classify(evidence)[0] == "raw"


def test_unsupported_format_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        stray = Path(tmp) / "notes.txt"
        stray.write_text("hello")
        result = classify.run({"artifacts": {"ingest_validate": {"matrix_path": str(stray)}}})
    assert any("unsupported matrix format" in e for e in result["errors"])


def test_missing_path_is_an_error():
    result = classify.run({"artifacts": {"ingest_validate": {"matrix_path": "/nope/x.h5"}}})
    assert any("does not exist" in e for e in result["errors"])


def test_no_matrix_at_all_is_an_error():
    result = classify.run({"artifacts": {}, "input_bundle": {}})
    assert any("no matrix to classify" in e for e in result["errors"])


# --- hints are checked, not trusted -----------------------------------------


def _h5(tmp: Path, n_barcodes: int, n_empty: int) -> Path:
    """A 10x-shaped h5 whose indptr encodes exactly `n_empty` empty barcodes."""
    import h5py
    import numpy as np

    path = Path(tmp) / "m.h5"
    genes_each = [0] * n_empty + [5] * (n_barcodes - n_empty)
    indptr = np.concatenate([[0], np.cumsum(genes_each)]).astype(np.int64)
    with h5py.File(path, "w") as handle:
        group = handle.create_group("matrix")
        group.create_dataset("barcodes", data=np.array([b"BC"] * n_barcodes))
        group.create_dataset("indptr", data=indptr)
        group.create_group("features").create_dataset("id", data=np.array([b"G"] * 100))
    return path


def test_a_hint_that_matches_the_evidence_is_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        path = _h5(Path(tmp), 1_000, 0)
        result = classify.run(
            {"artifacts": {"cellranger_count": {"matrix_path": str(path),
                                                "matrix_kind_hint": "filtered"}}}
        )
    assert result["errors"] == []
    assert result["matrix_class"] == "filtered"
    assert "contents agree" in result["evidence"]["hint_confirmed"]


def test_a_hint_that_contradicts_the_evidence_stops_the_run():
    """A renamed or mis-pointed file must not be routed on either belief."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _h5(Path(tmp), 200_000, 5_000)
        result = classify.run(
            {"artifacts": {"ingest_validate": {"matrix_path": str(path),
                                               "matrix_kind_hint": "filtered"}}}
        )
    assert result["matrix_class"] == "unknown", "must not silently pick a side"
    assert result["errors"]
    assert "called this matrix 'filtered'" in result["errors"][0]
    assert "look 'raw'" in result["errors"][0]


def test_an_unknown_hint_is_ignored_rather_than_treated_as_a_claim():
    with tempfile.TemporaryDirectory() as tmp:
        path = _h5(Path(tmp), 1_000, 0)
        result = classify.run(
            {"artifacts": {"ingest_validate": {"matrix_path": str(path),
                                               "matrix_kind_hint": "unknown"}}}
        )
    assert result["errors"] == []
    assert result["matrix_class"] == "filtered"


def test_cellranger_hint_wins_over_ingest_when_both_are_present():
    with tempfile.TemporaryDirectory() as tmp:
        counted = _h5(Path(tmp), 1_000, 0)
        result = classify.run(
            {
                "artifacts": {
                    "ingest_validate": {"matrix_path": "/stale/path.h5"},
                    "cellranger_count": {"matrix_path": str(counted),
                                         "matrix_kind_hint": "filtered"},
                }
            }
        )
    assert result["matrix_path"] == str(counted)
    assert result["errors"] == []


# --- the real Cell Ranger output --------------------------------------------


def test_real_raw_matrix_if_present():
    path = REAL_OUTS / "raw_feature_bc_matrix.h5"
    if not path.is_file():
        raise Skip("no real cellranger output on this machine")
    result = classify.run({"artifacts": {"ingest_validate": {"matrix_path": str(path)}}})
    assert result["matrix_class"] == "raw"
    assert result["needs_cell_calling"] is True
    assert result["recommended_next_tool"] == "load_raw_counts"
    assert result["metrics"]["n_barcodes"] > 100_000
    assert result["metrics"]["n_empty_barcodes"] > 0


def test_real_filtered_matrix_if_present():
    path = REAL_OUTS / "filtered_feature_bc_matrix.h5"
    if not path.is_file():
        raise Skip("no real cellranger output on this machine")
    result = classify.run({"artifacts": {"ingest_validate": {"matrix_path": str(path)}}})
    assert result["matrix_class"] == "filtered"
    assert result["needs_cell_calling"] is False
    assert result["metrics"]["n_empty_barcodes"] == 0
    # Cell Ranger's own metrics_summary.csv reports 3,201 median genes per cell.
    assert result["metrics"]["median_genes_per_barcode"] == 3201


def test_real_mtx_directory_if_present():
    directory = REAL_OUTS / "filtered_feature_bc_matrix"
    if not directory.is_dir():
        raise Skip("no real cellranger output on this machine")
    result = classify.run({"artifacts": {"ingest_validate": {"matrix_path": str(directory)}}})
    assert result["matrix_class"] == "filtered"
    assert result["evidence"]["format"] == "mtx_dir"


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures, skipped = [], 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except Skip as reason:
            skipped += 1
            print(f"  skip  {test.__name__}: {reason}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    passed = len(tests) - len(failures) - skipped
    print(f"\n{passed}/{len(tests) - skipped} passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
