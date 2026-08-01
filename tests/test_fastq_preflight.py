"""Unit tests for `fastq_preflight`: real gzip FASTQ content, no graph involved.

Run with `python tests/test_fastq_preflight.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402
from tests import fixtures  # noqa: E402

preflight = load_skill("fastq_preflight")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _run(paths, **config):
    return preflight.run({"input_bundle": {"paths": [str(p) for p in paths]}, "config": config})


def test_v3_chemistry_is_recognized_from_read_length():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r1_length=28)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    assert result["errors"] == []
    assert result["ready_to_count"] is True
    lib = result["detected_libraries"][0]
    assert lib["chemistry_guess"] == ["SC3Pv3"]
    assert result["recommended_next_tool"] == "cellranger_count"


def test_v2_length_is_reported_as_ambiguous_not_a_single_guess():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r1_length=26)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    guess = result["detected_libraries"][0]["chemistry_guess"]
    assert set(guess) == {"SC3Pv2", "SC5P-PE", "SC5P-R2"}


def test_missing_reference_is_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp))
        result = _run([bundle])
    assert result["ready_to_count"] is False
    assert any("no reference provided" in e for e in result["blocking_errors"])
    assert result["errors"] == result["blocking_errors"]


def test_reference_missing_marker_file_is_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp))
        empty_ref = Path(tmp) / "not-a-real-reference"
        empty_ref.mkdir()
        result = _run([bundle], reference=str(empty_ref))
    assert result["ready_to_count"] is False
    assert any("does not look like a Cell Ranger transcriptome" in e for e in result["blocking_errors"])


def test_missing_r2_is_blocking_not_just_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "fastq"
        directory.mkdir()
        fixtures.write_fastq_record(directory / "Solo_S1_L001_R1_001.fastq.gz", "A" * 28)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([directory], reference=str(ref))
    assert result["ready_to_count"] is False
    assert any("has no R2" in e for e in result["blocking_errors"])


def test_unknown_r1_length_is_a_warning_not_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r1_length=50)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    assert result["ready_to_count"] is True
    assert any("does not match a known 10x chemistry" in w for w in result["warnings"])
    assert result["detected_libraries"][0]["chemistry_guess"] == []


def test_short_r2_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r2_length=20)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    assert any("short for a cDNA read" in w for w in result["warnings"])


def test_samplesheet_flags_missing_sample_as_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), sample="SampleA")
        ref = fixtures.make_reference(Path(tmp))
        result = _run(
            [bundle], reference=str(ref), samplesheet=[{"sample": "SampleB"}]
        )
    assert result["ready_to_count"] is False
    assert any("SampleB" in e and "no matching FASTQ" in e for e in result["blocking_errors"])


def test_samplesheet_flags_extra_sample_as_warning_only():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), sample="SampleA")
        ref = fixtures.make_reference(Path(tmp))
        result = _run(
            [bundle], reference=str(ref), samplesheet=[{"sample": "SampleA"}]
        )
    assert result["ready_to_count"] is True
    assert result["blocking_errors"] == []


def test_samplesheet_chemistry_mismatch_is_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), sample="SampleA", r1_length=28)
        ref = fixtures.make_reference(Path(tmp))
        result = _run(
            [bundle],
            reference=str(ref),
            samplesheet=[{"sample": "SampleA", "chemistry": "SC3Pv2"}],
        )
    assert result["ready_to_count"] is True, "a mismatch is a warning, not a blocker"
    assert any("declares chemistry" in w for w in result["warnings"])


def test_multi_lane_is_read_from_every_lane():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), lanes=("001", "002"))
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    lib = result["detected_libraries"][0]
    assert lib["lanes"] == ["001", "002"]
    assert lib["n_files"] == 6


def test_missing_path_is_an_error():
    result = _run(["/nonexistent/bundle"])
    assert any("does not exist" in e for e in result["errors"])


def test_no_fastq_found_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        result = _run([empty])
    assert any("no FASTQ files found" in e for e in result["errors"])


def test_real_pbmc_1k_v3_bundle_if_present():
    """The 10x official test set: known-good SC3Pv3 chemistry, no reference on hand."""
    bundle = Path.home() / "data" / "pbmc_1k_v3" / "pbmc_1k_v3_fastqs"
    if not bundle.exists():
        raise Skip("pbmc_1k_v3 fastqs not present")
    result = _run([bundle])
    lib = result["detected_libraries"][0]
    assert lib["sample"] == "pbmc_1k_v3"
    assert lib["chemistry_guess"] == ["SC3Pv3"]
    assert lib["reads"]["R1"]["lengths_observed"] == [28]
    assert lib["reads"]["I1"]["lengths_observed"] == [8]
    assert lib["blocking"] == []
    assert result["ready_to_count"] is False, "no reference was supplied"
    assert any("no reference provided" in e for e in result["blocking_errors"])


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
