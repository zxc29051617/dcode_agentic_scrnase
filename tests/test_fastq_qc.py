"""Unit tests for `fastq_qc`.

The parsing tests use inline `fastqc_data.txt` text, so they are instant and do
not need FastQC installed. The end-to-end tests do run FastQC on a tiny fixture
and skip when it is absent.

Run with `python tests/test_fastq_qc.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402
from tests import fixtures  # noqa: E402

qc = load_skill("fastq_qc")


class Skip(Exception):
    """Raised by a test that needs a tool this machine does not have."""


def _need_fastqc():
    if shutil.which("fastqc") is None:
        raise Skip("fastqc not installed")


# --- parsing, without running anything -------------------------------------

SAMPLE_DATA = """##FastQC\t0.12.1
>>Basic Statistics\tpass
#Measure\tValue
Filename\tX_S1_L001_R2_001.fastq.gz
Total Sequences\t1000
Sequence length\t91
%GC\t45
>>END_MODULE
>>Per sequence quality scores\tpass
#Quality\tCount
20\t100
30\t400
35\t500
>>END_MODULE
>>Sequence Duplication Levels\tpass
#Total Deduplicated Percentage\t85.0
>>END_MODULE
>>Adapter Content\tpass
#Position\tIllumina Universal\tNextera
1\t0.0\t0.5
2\t12.5\t0.5
>>END_MODULE
"""


def test_q30_is_computed_from_the_quality_histogram():
    """FastQC does not report Q30 directly; it comes from the distribution."""
    modules = qc._split_modules(SAMPLE_DATA)
    fraction = qc._q30_fraction(modules["Per sequence quality scores"][1])
    assert fraction == 0.9, "900 of 1000 reads are at Q30 or better"


def test_duplicate_fraction_inverts_the_deduplicated_percentage():
    modules = qc._split_modules(SAMPLE_DATA)
    assert qc._duplicate_fraction(modules["Sequence Duplication Levels"][1]) == 0.15


def test_adapter_content_takes_the_worst_position_of_any_adapter():
    modules = qc._split_modules(SAMPLE_DATA)
    assert qc._max_adapter_pct(modules["Adapter Content"][1]) == 12.5


def test_read_role_is_parsed_from_illumina_names():
    assert qc._read_role("X_S1_L001_R2_001.fastq.gz") == "R2"
    assert qc._read_role("X_S1_L002_I1_001.fastq.gz") == "I1"
    assert qc._read_role("something_else.fastq.gz") is None


# --- the 10x read-role rule -------------------------------------------------


def _data_with(filename: str, failing_module: str) -> str:
    return (
        "##FastQC\t0.12.1\n"
        ">>Basic Statistics\tpass\n#Measure\tValue\n"
        f"Filename\t{filename}\nTotal Sequences\t10\n>>END_MODULE\n"
        f">>{failing_module}\tfail\n>>END_MODULE\n"
    )


def _parse_text(tmp: Path, text: str) -> dict:
    """Wrap inline data in the zip layout `parse_fastqc_zip` expects."""
    import zipfile

    path = Path(tmp) / "x_fastqc.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("x_fastqc/fastqc_data.txt", text)
    return qc.parse_fastqc_zip(path)


def test_barcode_read_composition_failure_is_expected_not_a_finding():
    """R1 is 28bp of barcode+UMI: skewed composition is what a barcode IS."""
    with tempfile.TemporaryDirectory() as tmp:
        parsed = _parse_text(
            Path(tmp), _data_with("S_S1_L001_R1_001.fastq.gz", "Per base sequence content")
        )
    assert parsed["read_role"] == "R1"
    assert parsed["modules_failed"] == [], "must not count against the run"
    assert parsed["modules_expected_for_read_role"] == ["Per base sequence content"]


def test_the_same_failure_on_R2_is_a_real_finding():
    """R2 is the cDNA read; skewed composition there is a genuine problem."""
    with tempfile.TemporaryDirectory() as tmp:
        parsed = _parse_text(
            Path(tmp), _data_with("S_S1_L001_R2_001.fastq.gz", "Per base sequence content")
        )
    assert parsed["read_role"] == "R2"
    assert parsed["modules_failed"] == ["Per base sequence content"]
    assert parsed["modules_expected_for_read_role"] == []


def test_quality_failure_on_a_barcode_read_is_still_reported():
    """Only structural modules are downgraded — low quality never is."""
    with tempfile.TemporaryDirectory() as tmp:
        parsed = _parse_text(
            Path(tmp), _data_with("S_S1_L001_R1_001.fastq.gz", "Per base sequence quality")
        )
    assert parsed["modules_failed"] == ["Per base sequence quality"]


def test_read_role_survives_fastqc_stripping_the_extension():
    """FastQC names the zip `..._R2_001_fastqc.zip`, so the archive name alone lies."""
    with tempfile.TemporaryDirectory() as tmp:
        import zipfile

        path = Path(tmp) / "S_S1_L001_R2_001_fastqc.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("x/fastqc_data.txt", SAMPLE_DATA)
        parsed = qc.parse_fastqc_zip(path)
    assert parsed["read_role"] == "R2"


# --- missing tools are advisory --------------------------------------------


def test_missing_fastqc_warns_instead_of_blocking_the_count():
    result = qc.run(
        {
            "input_bundle": {"paths": ["/whatever"]},
            "config": {"fastqc_binary": None},
            "run_dir": ".",
        }
    ) if shutil.which("fastqc") is None else qc.run(
        {"input_bundle": {"paths": ["/whatever"]}, "config": {"skip_fastq_qc": True}}
    )
    assert result["errors"] == [], "sequencing QC is advisory, not a gate"
    assert result["warnings"]
    assert result["recommended_next_tool"] == "cellranger_count"


def test_skip_flag_says_so_rather_than_pretending_it_ran():
    result = qc.run({"input_bundle": {"paths": ["/x"]}, "config": {"skip_fastq_qc": True}})
    assert result["errors"] == []
    assert any("skipped by config" in w for w in result["warnings"])
    assert result["metrics"] == {}


def test_missing_path_is_an_error():
    _need_fastqc()
    result = qc.run({"input_bundle": {"paths": ["/nonexistent"]}, "config": {}, "run_dir": "."})
    assert any("does not exist" in e for e in result["errors"])


# --- end to end -------------------------------------------------------------


def test_clean_10x_trio_passes_with_no_findings():
    _need_fastqc()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = fixtures.make_10x_fastq_trio(root)
        result = qc.run(
            {
                "input_bundle": {"paths": [str(bundle)]},
                "run_dir": str(root / "run"),
                "config": {"fastqc_threads": 2},
            }
        )
        assert result["errors"] == []
        assert result["module_failures"] == {}, "a clean 10x trio must produce no findings"
        assert sorted(result["per_read_role"]) == ["I1", "R1", "R2"]
        assert result["metrics"]["q30_r2"] == 1.0
        assert Path(result["report_dir"]).is_dir()


def test_low_quality_r2_is_flagged_against_the_threshold():
    _need_fastqc()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = fixtures.make_10x_fastq_trio(root, r2_quality=15)
        result = qc.run(
            {
                "input_bundle": {"paths": [str(bundle)]},
                "run_dir": str(root / "run"),
                "config": {"fastqc_threads": 2, "min_q30": 0.75},
            }
        )
        assert result["metrics"]["q30_r2"] == 0.0
        assert any("reach Q30" in w for w in result["warnings"])


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
