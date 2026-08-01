"""Unit tests for `cellranger_count`, without ever launching Cell Ranger.

The expensive part is one subprocess call; everything worth testing is around
it — the reference guard, the reuse decision, and the refusals. Those are
exercised with fixture matrices that carry a genome name, which is exactly what
the guard reads.

Run with `python tests/test_cellranger_count.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402
from tests import fixtures  # noqa: E402

count = load_skill("cellranger_count")

#: A binary that exists and is executable, so the "is cellranger installed"
#: check passes without a real Cell Ranger. Nothing here ever runs it.
FAKE_BINARY = "/bin/true"


def _payload(root: Path, *, reference: Path, work: Path, libraries=("SampleA",), **config):
    return {
        "run_dir": str(work),
        "input_bundle": {"paths": [str(root / "fastq")]},
        "config": {"binary": FAKE_BINARY, **config},
        "artifacts": {
            "resolve_reference": {
                "transcriptome": str(reference),
                "species_verified": True,
            },
            "fastq_preflight": {
                "ready_to_count": True,
                "detected_libraries": [{"sample": name} for name in libraries],
            },
        },
    }


# --- the reference guard, which is why this module exists ------------------


def test_matching_reference_is_reused_not_recounted():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        work = root / "run" / "cellranger_count"
        fixtures.make_cellranger_outs_h5(work, "SampleA", genome="GRCh38")
        result = count.run(_payload(root, reference=reference, work=root / "run"))

    assert result["errors"] == []
    assert result["libraries"][0]["disposition"] == "reused: GRCh38"
    assert result["metrics"]["n_counted"] == 0
    assert result["metrics"]["n_reused"] == 1
    assert any("reused an existing matrix" in w for w in result["warnings"])


def test_different_reference_refuses_to_reuse():
    """The silent-failure case: old counts filed under the new reference's name."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "t2t", genomes=["T2T_CHM13v2"])
        work = root / "run" / "cellranger_count"
        fixtures.make_cellranger_outs_h5(work, "SampleA", genome="GRCh38")
        result = count.run(_payload(root, reference=reference, work=root / "run"))

    assert result["errors"], "a mismatched matrix must never be silently reused"
    message = result["errors"][0]
    assert "REFUSING to reuse" in message
    assert "GRCh38" in message and "T2T_CHM13v2" in message
    assert "delete" in message.lower(), "must say how to recover"


def test_unreadable_matrix_is_treated_as_a_mismatch():
    """Cannot verify is not the same as fine: a stop beats an undetectable reuse."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        outs = root / "run" / "cellranger_count" / "SampleA" / "outs"
        outs.mkdir(parents=True)
        (outs / "filtered_feature_bc_matrix.h5").write_text("not an h5 file")
        result = count.run(_payload(root, reference=reference, work=root / "run"))

    assert result["errors"]
    assert "cannot be read" in result["errors"][0]
    assert "Refusing to reuse" in result["errors"][0]


def test_reference_without_reference_json_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = root / "not-a-reference"
        bare.mkdir()
        result = count.run(_payload(root, reference=bare, work=root / "run"))
    assert any("no reference.json" in e for e in result["errors"])


# --- refusing to start ------------------------------------------------------


def test_preflight_failure_stops_before_counting():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        payload = _payload(root, reference=reference, work=root / "run")
        payload["artifacts"]["fastq_preflight"] = {
            "ready_to_count": False,
            "blocking_errors": ["sample 'X' has no R2 (cDNA) read"],
        }
        result = count.run(payload)

    assert result["errors"]
    assert "not ready to count" in result["errors"][0]
    assert "no R2" in result["errors"][0], "must carry preflight's own reason"


def test_missing_transcriptome_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = _payload(root, reference=root / "x", work=root / "run")
        payload["artifacts"]["resolve_reference"] = {}
        payload["config"].pop("transcriptome", None)
        result = count.run(payload)
    assert any("resolve_reference must run first" in e for e in result["errors"])


def test_missing_binary_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        payload = _payload(root, reference=reference, work=root / "run")
        payload["config"]["binary"] = "/nonexistent/cellranger"
        result = count.run(payload)
    assert any("executable not found" in e for e in result["errors"])


def test_partial_run_directory_is_refused_rather_than_resumed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        # A directory with no filtered matrix: an aborted or killed count.
        (root / "run" / "cellranger_count" / "SampleA" / "outs").mkdir(parents=True)
        result = count.run(_payload(root, reference=reference, work=root / "run"))
    assert any("partial or aborted run" in e for e in result["errors"])


def test_unverified_species_warns_but_does_not_block():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "custom", genomes=["custom_build"])
        work = root / "run" / "cellranger_count"
        fixtures.make_cellranger_outs_h5(work, "SampleA", genome="custom_build")
        payload = _payload(root, reference=reference, work=root / "run")
        payload["artifacts"]["resolve_reference"]["species_verified"] = False
        result = count.run(payload)

    assert result["errors"] == [], "a custom reference must not be blocked here"
    assert any("nobody confirmed" in w for w in result["warnings"])


# --- the command ------------------------------------------------------------


def test_command_carries_the_validated_arguments():
    command = count._build_command(
        "cellranger",
        {"library_id": "PBMC", "sample_prefix": "pbmc_1k_v3", "chemistry": "auto"},
        "/data/fastq",
        Path("/ref/T2T"),
        {"localcores": 32, "localmem": 128, "expected_cells": 1000},
    )
    joined = " ".join(command)
    assert "--id=PBMC" in joined
    assert "--sample=pbmc_1k_v3" in joined
    assert "--transcriptome=/ref/T2T" in joined
    assert "--localcores=32" in joined and "--localmem=128" in joined
    assert "--expect-cells=1000" in joined
    assert "--chemistry" not in joined, "auto must let Cell Ranger detect"


def test_declared_chemistry_overrides_auto_detection():
    command = count._build_command(
        "cellranger",
        {"library_id": "L", "sample_prefix": "L", "chemistry": "SC3Pv3"},
        "/f",
        Path("/r"),
        {},
    )
    assert "--chemistry=SC3Pv3" in " ".join(command)


def test_create_bam_is_explicit_in_the_command():
    off = count._build_command(
        "cellranger", {"library_id": "L", "sample_prefix": "L"}, "/f", Path("/r"),
        {"create_bam": False},
    )
    assert "--create-bam=false" in " ".join(off)
    on = count._build_command(
        "cellranger", {"library_id": "L", "sample_prefix": "L"}, "/f", Path("/r"), {}
    )
    assert "--create-bam=true" in " ".join(on)


def test_multiple_libraries_all_appear_in_the_manifest():
    import json

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        work = root / "run" / "cellranger_count"
        for name in ("A", "B"):
            fixtures.make_cellranger_outs_h5(work, name, genome="GRCh38")
        result = count.run(
            _payload(root, reference=reference, work=root / "run", libraries=("A", "B"))
        )

        # Read the manifest before the temp directory goes away.
        assert result["errors"] == []
        assert result["metrics"]["n_libraries"] == 2
        manifest = Path(result["count_manifest"])
        assert manifest.is_file()
        assert set(json.loads(manifest.read_text())) == {"A", "B"}


def test_output_hands_the_filtered_matrix_to_the_classifier():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        work = root / "run" / "cellranger_count"
        fixtures.make_cellranger_outs_h5(work, "SampleA", genome="GRCh38")
        result = count.run(_payload(root, reference=reference, work=root / "run"))

    assert result["matrix_kind"] == "filtered"
    assert result["matrix_path"] == result["filtered_feature_bc_matrix"]
    assert result["raw_feature_bc_matrix"].endswith("raw_feature_bc_matrix.h5")
    assert result["recommended_next_tool"] == "count_matrix_classify"
    assert result["metrics"]["per_library"]["SampleA"]["Estimated Number of Cells"] == "1222"


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
