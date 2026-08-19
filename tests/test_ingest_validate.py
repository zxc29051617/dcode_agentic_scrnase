"""Unit tests for `ingest_validate`: real filesystem detection, no graph involved.

Run with `python tests/test_ingest_validate.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402
from tests import paths  # noqa: E402
from tests import fixtures  # noqa: E402

ingest = load_skill("ingest_validate")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _run(paths, **config):
    return ingest.run({"input_bundle": {"paths": [str(p) for p in paths]}, "config": config})


def test_filtered_matrix_is_recognized():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_mtx_dir(Path(tmp))])
    assert result["errors"] == []
    assert result["input_type"] == "matrix"
    assert result["artifact_kind"] == "mtx_dir"
    assert result["matrix_kind_hint"] == "filtered"
    assert result["needs_cell_calling"] is False
    assert result["needs_upstream_preprocessing"] is False
    assert result["recommended_next_tool"] == "count_matrix_classify"


def test_raw_matrix_requires_cell_calling():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_mtx_dir(Path(tmp), "raw_feature_bc_matrix")])
    assert result["matrix_kind_hint"] == "raw"
    assert result["needs_cell_calling"] is True


def test_cellranger_outs_routes_on_filtered_and_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_cellranger_outs(Path(tmp))])
    assert result["errors"] == []
    assert result["matrix_kind_hint"] == "filtered"
    assert "filtered_feature_bc_matrix" in result["matrix_path"]
    assert any("both raw and filtered" in w for w in result["warnings"])
    assert len(result["detected"]) == 2


def test_cellranger_outs_ignores_molecule_info_h5():
    with tempfile.TemporaryDirectory() as tmp:
        outs = fixtures.make_cellranger_outs(Path(tmp))
        (outs / "molecule_info.h5").touch()
        result = _run([outs])
    assert result["errors"] == []
    assert len(result["detected"]) == 2
    assert all("molecule_info.h5" not in item["path"] for item in result["detected"])


def test_cellranger_outs_prefers_h5_over_duplicate_mtx_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        outs = fixtures.make_cellranger_outs(Path(tmp))
        (outs / "filtered_feature_bc_matrix.h5").touch()
        (outs / "raw_feature_bc_matrix.h5").touch()
        result = _run([outs])
    assert result["errors"] == []
    assert len(result["detected"]) == 2
    assert all(item["artifact_kind"] == "tenx_h5" for item in result["detected"])


def test_unnamed_matrix_is_left_unknown_not_guessed():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_mtx_dir(Path(tmp), "counts")])
    assert result["matrix_kind_hint"] == "unknown"
    assert result["needs_cell_calling"] is None, "must not guess whether cells were called"
    assert any("raw vs filtered could not be determined" in w for w in result["warnings"])


def test_paired_fastq_bundle_routes_upstream():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_fastq_dir(Path(tmp), ("SampleA", "SampleB"))])
    assert result["errors"] == []
    assert result["input_type"] == "fastq"
    assert result["needs_upstream_preprocessing"] is True
    assert result["sample_ids"] == ["SampleA", "SampleB"]
    assert result["recommended_next_tool"] == "fastq_preflight"
    assert result["warnings"] == []


def test_multi_lane_layout_is_resolved_once():
    """`fastq_preflight` and `cellranger_count` both need the lane list."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir(
            Path(tmp), ("PBMC",), reads=("I1", "R1", "R2"), lanes=("001", "002")
        )
        result = _run([bundle])
    layout = result["fastq_layout"]["PBMC"]
    assert layout["lanes"] == ["001", "002"]
    assert layout["reads"] == ["I1", "R1", "R2"]
    assert layout["n_files"] == 6
    assert result["metrics"] == {
        "n_samples": 1,
        "n_fastq_files": 6,
        "n_lanes": 2,
        "n_artifacts": 1,
    }
    assert result["warnings"] == []


def test_real_pbmc_1k_v3_bundle_if_present():
    """The 10x official test set, when it has been downloaded locally."""
    bundle = paths.FASTQ_BUNDLES["pbmc_1k_v3"]
    if not bundle.exists():
        raise Skip("pbmc_1k_v3 fastqs not present")
    result = _run([bundle])
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["input_type"] == "fastq"
    assert result["sample_ids"] == ["pbmc_1k_v3"]
    assert result["fastq_layout"]["pbmc_1k_v3"] == {
        "lanes": ["001", "002"],
        "reads": ["I1", "R1", "R2"],
        "n_files": 6,
    }
    assert result["recommended_next_tool"] == "fastq_preflight"


def test_unpaired_fastq_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_fastq_dir(Path(tmp), ("Solo",), reads=("R1",))])
    assert result["input_type"] == "fastq"
    assert any("has R1 but no R2" in w for w in result["warnings"])


def test_mixed_assay_types_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = _run([fixtures.make_fastq_dir(root), fixtures.make_mtx_dir(root)])
    assert result["errors"], "a FASTQ + matrix bundle has no single entry point"
    assert "mixed assay types" in result["errors"][0]
    assert result["input_type"] == "unknown"


def test_missing_path_is_an_error():
    result = _run(["/nonexistent/bundle"])
    assert any("does not exist" in e for e in result["errors"])


def test_empty_bundle_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        result = _run([empty])
    assert any("nothing recognized" in e for e in result["errors"])
    result = ingest.run({"input_bundle": {}, "config": {}})
    assert any("nothing to classify" in e for e in result["errors"])


def test_h5ad_shape_is_read_without_loading_counts():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run([fixtures.make_h5ad(Path(tmp), n_obs=500)])
    assert result["input_type"] == "matrix"
    assert result["artifact_kind"] == "h5ad"
    assert result["metrics"]["n_obs"] == 500
    assert result["matrix_kind_hint"] == "unknown", "500 cells proves nothing on its own"


def test_large_h5ad_is_inferred_raw():
    with tempfile.TemporaryDirectory() as tmp:
        path = fixtures.make_h5ad(Path(tmp), n_obs=120_000, name="counts.h5ad")
        result = _run([path])
    assert result["matrix_kind_hint"] == "raw"
    assert result["needs_cell_calling"] is True
    assert "raw_inferred_from" in result["metrics"]


def test_h5ad_name_beats_the_size_heuristic():
    with tempfile.TemporaryDirectory() as tmp:
        path = fixtures.make_h5ad(Path(tmp), n_obs=120_000, name="filtered_feature_bc_matrix.h5ad")
        result = _run([path])
    assert result["matrix_kind_hint"] == "filtered"
    assert result["needs_cell_calling"] is False


def test_sample_qc_triage_takes_priority_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        directory = fixtures.make_mtx_dir(Path(tmp))
        result = ingest.run(
            {
                "input_bundle": {"paths": [str(directory)]},
                "config": {"sample_qc_triage": True},
                "sample_metadata": {"samples": ["A", "B"]},
            }
        )
    assert result["recommended_next_tool"] == "sample_qc_triage"


def test_every_matrix_travels_on_not_just_the_first():
    """N inputs became one library, silently, until this was pinned.

    `ingest_validate` emitted only a singular `matrix_path`; downstream steps
    fall back to it and call the result `sample1`, so a two-sample run analysed
    one library and reported on it as though it were the whole thing — with no
    warning, because nothing noticed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = fixtures.make_mtx_dir(root / "libA", "filtered_feature_bc_matrix")
        second = fixtures.make_mtx_dir(root / "libB", "filtered_feature_bc_matrix")
        result = ingest.run({"input_bundle": {"paths": [str(first), str(second)]}, "config": {}})

    assert result["errors"] == []
    assert len(result["matrix_paths"]) == 2, result["matrix_paths"]
    assert len(result["sample_ids"]) == 2
    assert result["metrics"]["n_samples"] == 2
    assert set(result["matrix_paths"].values()) == {str(first), str(second)}


def test_sample_names_come_from_the_path_not_a_counter():
    """`sample1` tells a reader nothing; the directory name is what they call it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = fixtures.make_mtx_dir(root / "pbmc_donor_A", "filtered_feature_bc_matrix")
        second = fixtures.make_mtx_dir(root / "pbmc_donor_B", "filtered_feature_bc_matrix")
        result = ingest.run({"input_bundle": {"paths": [str(first), str(second)]}, "config": {}})
    assert set(result["sample_ids"]) == {"pbmc_donor_A", "pbmc_donor_B"}


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
