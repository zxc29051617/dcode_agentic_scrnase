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


def test_paths_in_the_command_are_absolute():
    """Cell Ranger runs with cwd set to the output directory.

    `resolve_reference` hands over `reference/<name>` on purpose — that is what
    keeps config portable — but a relative path stops meaning anything once the
    subprocess has changed directory. A full-graph mouse run failed on exactly
    this after the standalone CLI, which passes absolute paths, had passed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        work = root / "run" / "cellranger_count"
        fixtures.make_cellranger_outs_h5(work, "SampleA", genome="GRCh38")

        import os

        previous = os.getcwd()
        os.chdir(root)
        try:
            payload = _payload(root, reference=Path("ref"), work=root / "run")
            payload["input_bundle"] = {"paths": ["fastq"]}
            result = count.run(payload)
        finally:
            os.chdir(previous)

    assert result["errors"] == [], "a relative reference must still resolve"
    assert result["libraries"][0]["disposition"].startswith("reused")


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


def test_every_library_is_passed_downstream_not_just_the_first():
    """Silently analysing one of N was the bug; `merge_samples` is the fix.

    Passing only the first matrix would produce a report named for the project
    that describes a fraction of it, with the audit log agreeing — the error
    shape nothing downstream can notice.
    """
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

        assert result["errors"] == []
        assert set(result["matrix_paths"]) == {"A", "B"}, "every library, not just one"
        assert result["metrics"]["n_libraries"] == 2

        manifest = Path(result["count_manifest"])
        assert manifest.is_file()
        assert set(json.loads(manifest.read_text())) == {"A", "B"}


def test_output_hands_the_raw_matrix_to_the_classifier():
    """Always raw, so how many cells to keep stays a decision somebody makes.

    This used to be conditional — raw when `force_cells` or `min_umi` was set,
    filtered otherwise — which made the cell count the one choice in this
    pipeline you had to answer before you could see the evidence for it. On the
    FASTQ route `cell_calling_review` was simply unreachable without already
    having the answer, while everywhere else (`apply_cell_qc_filter`) the shape
    is to cost out the candidates and stop.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        work = root / "run" / "cellranger_count"
        fixtures.make_cellranger_outs_h5(work, "SampleA", genome="GRCh38")
        result = count.run(_payload(root, reference=reference, work=root / "run"))

    assert result["matrix_kind_hint"] == "raw"
    library = result["libraries"][0]
    assert result["matrix_paths"]["SampleA"] == library["raw_feature_bc_matrix"]
    assert result["recommended_next_tool"] == "count_matrix_classify"
    assert result["metrics"]["per_library"]["SampleA"]["Estimated Number of Cells"] == "1222"


def test_the_route_no_longer_depends_on_whether_a_cell_count_was_given():
    """Same output with and without one, so the gate cannot be pre-empted."""
    results = []
    for config in ({}, {"force_cells": 400}, {"min_umi": 500}):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
            fixtures.make_cellranger_outs_h5(root / "run" / "cellranger_count",
                                             "SampleA", genome="GRCh38")
            payload = _payload(root, reference=reference, work=root / "run")
            payload["config"].update(config)
            results.append(count.run(payload)["matrix_kind_hint"])
    assert results == ["raw", "raw", "raw"], results


def test_cell_ranger_own_call_stays_reachable_for_comparison():
    """The filtered matrix is recorded even though it is not what goes on.

    `cell_calling_review` reads `libraries[].filtered_feature_bc_matrix` from
    this step's record and reports `evidence.cellranger_cells` beside the
    barcode-rank curve. Dropping it would turn Cell Ranger's call from a
    suggestion into something nobody can see.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        fixtures.make_cellranger_outs_h5(root / "run" / "cellranger_count",
                                         "SampleA", genome="GRCh38")
        result = count.run(_payload(root, reference=reference, work=root / "run"))

    library = result["libraries"][0]
    assert library["filtered_feature_bc_matrix"].endswith("filtered_feature_bc_matrix.h5")
    assert library["raw_feature_bc_matrix"].endswith("raw_feature_bc_matrix.h5")
    assert result["available_matrices"]["raw"], "the raw matrices stay reachable"
    assert result["available_matrices"]["filtered"], "and so does Cell Ranger's own call"


def test_answering_the_cell_calling_gate_does_not_recount():
    """`force_cells` must cut at the review, not at the count.

    It was listed in `cellranger_count`'s `config_keys` while the route was
    chosen from it. Now that it cannot change a byte this step writes, leaving
    it there would mean answering the gate re-ran Cell Ranger on every library
    — twenty to forty minutes each — to reproduce a matrix nothing had
    invalidated.
    """
    from src.registry import REGISTRY, earliest_step_reading

    keys = REGISTRY["cellranger_count"].config_keys
    assert "force_cells" not in keys and "min_umi" not in keys
    for key in ("force_cells", "min_umi"):
        cut, _ = earliest_step_reading([key])
        assert cut == "cell_calling_review", f"{key} cuts at {cut}"
    # And what genuinely does change the count still cuts there.
    assert earliest_step_reading(["expected_cells"])[0] == "cellranger_count"


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
