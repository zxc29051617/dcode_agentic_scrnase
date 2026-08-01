"""Unit tests for `resolve_reference` and the `src.species` table.

Run with `python tests/test_resolve_reference.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import species  # noqa: E402
from src.registry import load_skill  # noqa: E402
from tests import fixtures  # noqa: E402

resolve = load_skill("resolve_reference")


class Skip(Exception):
    """Raised by a test that needs data this machine does not have."""


def _run(*, fastq=True, artifacts=None, **config):
    return resolve.run(
        {
            "config": config,
            "artifacts": artifacts
            or {"ingest_validate": {"needs_upstream_preprocessing": fastq}},
        }
    )


# --- the table ------------------------------------------------------------


def test_aliases_cover_the_languages_people_actually_type():
    for written in ("human", "Homo Sapiens", "人類", "HUMAN"):
        assert species.canonical(written) == "human", written
    for written in ("mouse", "小鼠", "Mus musculus"):
        assert species.canonical(written) == "mouse", written
    assert species.canonical("banana") is None
    assert species.canonical(None) is None


def test_unregistered_species_is_recognised_but_has_no_profile():
    """Rat is a known name with no vetted gene list — that gap is deliberate."""
    assert species.canonical("大鼠") == "rat"
    assert species.profile("rat") is None
    assert species.known() == ["human", "mouse"]


def test_identify_reference_reads_metadata_not_directory_names():
    assert species.identify_reference({"genomes": ["T2T_CHM13v2_RefSeqLiftoff_v5_3"]}) == {"human"}
    assert species.identify_reference({"genomes": ["GRCm39"]}) == {"mouse"}
    assert species.identify_reference(
        {"input_fasta_files": ["Rattus_norvegicus.mRatBN7.2.fa"]}
    ) == {"rat"}


def test_identify_reference_is_silent_when_it_cannot_tell():
    assert species.identify_reference({"genomes": ["my_custom_thing"]}) == set()
    barnyard = species.identify_reference({"genomes": ["GRCh38", "GRCm39"]})
    assert barnyard == {"human", "mouse"}, "a barnyard reference matches both, on purpose"


# --- resolution -----------------------------------------------------------


def test_registered_species_resolves_to_a_project_local_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixtures.make_reference(root, "T2T_CHM13v2_RefSeqLiftoff_v5_3",
                                genomes=["T2T_CHM13v2_RefSeqLiftoff_v5_3"])
        result = _run(species="human", reference_root=str(root))
    assert result["errors"] == []
    assert result["species"] == "human"
    assert result["transcriptome"].endswith("T2T_CHM13v2_RefSeqLiftoff_v5_3")
    assert result["reference_available"] is True
    assert result["species_verified"] is True
    assert result["mito_prefix"] == "MT-"
    assert "HBB" in result["erythroid_genes"]
    assert result["recommended_next_tool"] == "fastq_preflight"


def test_species_mismatch_is_an_error():
    """The silent-failure case this step exists to catch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ref = fixtures.make_reference(root, "some_ref", genomes=["GRCh38"])
        result = _run(species="mouse", transcriptome=str(ref))
    assert result["species_verified"] is False
    assert any("species mismatch" in e for e in result["errors"])
    assert any("mouse" in e and "human" in e for e in result["errors"])


def test_explicit_transcriptome_wins_over_the_species_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ref = fixtures.make_reference(root, "my_own_build", genomes=["GRCh38"])
        result = _run(species="human", transcriptome=str(ref), reference_root=str(root))
    assert result["transcriptome"] == str(ref)
    assert result["errors"] == []


def test_missing_reference_blocks_a_fastq_run_and_says_how_to_get_it():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(species="mouse", reference_root=str(Path(tmp) / "empty"), fastq=True)
    assert result["reference_available"] is False
    assert any("does not exist" in e for e in result["errors"])
    assert any("cf.10xgenomics.com" in e for e in result["errors"]), "must say how to get it"


def test_missing_reference_only_warns_on_a_matrix_run():
    """A count matrix never needs the 32 GB index — only the species constants."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(species="human", reference_root=str(Path(tmp) / "empty"), fastq=False)
    assert result["errors"] == []
    assert any("does not exist" in w for w in result["warnings"])
    assert result["mito_prefix"] == "MT-", "constants still resolve without the reference"
    assert result["recommended_next_tool"] == "count_matrix_classify"


def test_unregistered_species_blocks_a_fastq_run_without_an_explicit_path():
    result = _run(species="rat", fastq=True)
    assert any("no reference for species" in e for e in result["errors"])
    assert any("human, mouse" in e for e in result["errors"]), "must list what is registered"


def test_unregistered_species_runs_with_an_explicit_path():
    with tempfile.TemporaryDirectory() as tmp:
        ref = fixtures.make_reference(Path(tmp), "rat_ref", genomes=["mRatBN7.2"])
        result = _run(species="rat", transcriptome=str(ref), fastq=True)
    assert result["errors"] == [], "a non-model organism must not be blocked"
    assert result["species"] == "rat"
    assert result["species_verified"] is True


def test_custom_reference_skips_verification_instead_of_failing():
    with tempfile.TemporaryDirectory() as tmp:
        ref = fixtures.make_reference(Path(tmp), "mystery", genomes=["something_custom"])
        result = _run(species="human", transcriptome=str(ref))
    assert result["errors"] == [], "an unrecognised reference must not block"
    assert result["species_verified"] is False
    assert any("verification skipped" in w for w in result["warnings"])


def test_barnyard_reference_skips_verification():
    with tempfile.TemporaryDirectory() as tmp:
        ref = fixtures.make_reference(Path(tmp), "pdx", genomes=["GRCh38", "GRCm39"])
        result = _run(species="human", transcriptome=str(ref))
    assert result["errors"] == [], "both species are correct; there is no wrong answer"
    assert any("barnyard" in w for w in result["warnings"])


def test_directory_without_reference_json_is_not_a_reference():
    with tempfile.TemporaryDirectory() as tmp:
        bare = Path(tmp) / "not-a-reference"
        bare.mkdir()
        result = _run(species="human", transcriptome=str(bare))
    assert any("no reference.json" in e for e in result["errors"])


def test_mouse_warns_that_its_qc_defaults_are_borrowed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fixtures.make_reference(root, "refdata-gex-GRCm39-2024-A", genomes=["GRCm39"])
        result = _run(species="mouse", reference_root=str(root))
    assert result["errors"] == []
    assert any("derived from another species" in w for w in result["warnings"])


def test_real_t2t_reference_if_linked():
    """The project-local symlink made by scripts/link_reference.sh."""
    root = Path(__file__).resolve().parent.parent / "reference"
    if not (root / "T2T_CHM13v2_RefSeqLiftoff_v5_3" / "reference.json").is_file():
        raise Skip("reference/ not linked; run scripts/link_reference.sh")
    result = _run(species="human", reference_root=str(root))
    assert result["errors"] == []
    assert result["species_verified"] is True
    assert result["reference_genomes"] == ["T2T_CHM13v2_RefSeqLiftoff_v5_3"]
    assert result["reference_version"] == "T2T-CHM13v2.0_RefSeqLiftoff-v5.3"


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
