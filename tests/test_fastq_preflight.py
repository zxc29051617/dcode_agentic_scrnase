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


def test_a_bundle_with_random_barcodes_passes_but_names_no_chemistry():
    """Synthetic reads are in no whitelist, and saying so beats guessing."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r1_length=28)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    assert result["errors"] == []
    assert result["ready_to_count"] is True
    assert result["detected_libraries"][0]["chemistry_guess"] == []
    assert any("could not be identified" in w for w in result["warnings"])
    assert result["recommended_next_tool"] == "cellranger_count"


def test_read_length_is_a_lower_bound_not_an_identification():
    """The heuristic this replaced. 10x's own pbmc_1k_v2 is a v2 library
    sequenced with 28 cycles on R1 — the same length as v3 — so reading the
    chemistry off the length called it v3, confidently and wrongly."""
    from src.registry import load_skill

    module = load_skill("fastq_preflight")
    assert module.MIN_R1_LENGTH["SC3Pv2"] == 26
    assert module.MIN_R1_LENGTH["SC3Pv3"] == 28
    assert not hasattr(module, "CHEMISTRY_BY_R1_LENGTH"), (
        "the length-to-chemistry table was the bug; it must not come back"
    )


def test_an_r1_too_short_for_any_chemistry_is_blocking():
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r1_length=20)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    assert result["ready_to_count"] is False
    assert any("too short for any 10x chemistry" in e for e in result["blocking_errors"])


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


def test_an_over_sequenced_r1_is_not_a_problem():
    """More cycles than a kit needs is a sequencing choice, not a defect."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = fixtures.make_fastq_dir_with_reads(Path(tmp), r1_length=50)
        ref = fixtures.make_reference(Path(tmp))
        result = _run([bundle], reference=str(ref))
    assert result["ready_to_count"] is True
    assert not any("too short" in w for w in result["warnings"])


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


TENX_FASTQ = {
    "pbmc_1k_v2": (Path.home() / "data/pbmc_1k_v2/pbmc_1k_v2_fastqs", ["SC3Pv2", "SC5P-PE", "SC5P-R2"]),
    "pbmc_1k_v3": (Path.home() / "data/pbmc_1k_v3/pbmc_1k_v3_fastqs", ["SC3Pv3"]),
    "neuron_1k_v3": (Path.home() / "data/neuron_1k_v3/neuron_1k_v3_fastqs", ["SC3Pv3"]),
}


def test_real_chemistry_comes_from_the_barcode_whitelist():
    """The check the length heuristic could not pass.

    All three have a 28bp R1. Only the whitelist separates them: pbmc_1k_v2's
    barcodes land in 737K-august-2016 and the v3 sets in 3M-february-2018.
    v2 and 5' share the 737K list, so that hit narrows to three kits and stops —
    telling them apart needs alignment.
    """
    available = {n: v for n, v in TENX_FASTQ.items() if v[0].is_dir()}
    if not available:
        raise Skip("no 10x FASTQ bundles downloaded")

    for name, (bundle, expected) in available.items():
        result = _run([bundle])
        library = result["detected_libraries"][0]
        assert library["reads"]["R1"]["lengths_observed"] == [28], name
        assert library["chemistry_guess"] == expected, name
        rates = library["chemistry_evidence"]["whitelist_hit_rate"]
        best = library["chemistry_evidence"]["matched_whitelist"]
        assert rates[best] > 0.5, f"{name}: only {rates[best]:.0%} matched"


def test_samplesheet_chemistry_mismatch_is_a_warning():
    """Needs a bundle whose chemistry can actually be read, so it needs real data:
    a mismatch cannot be detected against a chemistry nothing identified."""
    bundle, _ = TENX_FASTQ["pbmc_1k_v3"]
    if not bundle.is_dir():
        raise Skip("pbmc_1k_v3 fastqs not present")
    result = _run(
        [bundle], samplesheet=[{"sample": "pbmc_1k_v3", "chemistry": "SC3Pv2"}]
    )
    assert any("declares chemistry" in w for w in result["warnings"])
    assert not any("declares chemistry" in e for e in result["blocking_errors"]), (
        "a mismatch is a warning: Cell Ranger's --chemistry override may be deliberate"
    )


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
