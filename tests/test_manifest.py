"""The sample manifest contract: what a study design has to say before it counts.

A manifest exists to separate five things the pipeline used to conflate into one
string: which library, which specimen, which subject, which biological group,
and which technical batch. `obs["sample"]` was all five at once, and it was
derived from a FASTQ filename.

Everything here is deterministic Python. No model reads a manifest, scores a
manifest, or decides whether one is valid — see `tests/test_study_design.py`
for the tests that pin that boundary.

Run with `python tests/test_manifest.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import manifest as mf  # noqa: E402

BALANCED = """library_id,sample_id,donor_id,condition,technical_batch
LIB001,S001,D001,control,BATCH_A
LIB002,S002,D002,disease,BATCH_A
LIB003,S003,D003,control,BATCH_B
LIB004,S004,D004,disease,BATCH_B
"""

CONFOUNDED = """library_id,sample_id,donor_id,condition,technical_batch
LIB001,S001,D001,control,BATCH_A
LIB002,S002,D002,control,BATCH_A
LIB003,S003,D003,disease,BATCH_B
LIB004,S004,D004,disease,BATCH_B
"""

IMBALANCED = """library_id,sample_id,donor_id,condition,technical_batch
LIB001,S001,D001,control,BATCH_A
LIB002,S002,D002,control,BATCH_A
LIB003,S003,D003,control,BATCH_A
LIB004,S004,D004,disease,BATCH_A
LIB005,S005,D005,control,BATCH_B
LIB006,S006,D006,disease,BATCH_B
LIB007,S007,D007,disease,BATCH_B
LIB008,S008,D008,disease,BATCH_B
"""


def _load(text: str):
    return mf.parse_manifest(text, source="test.csv")


def _ok(text: str):
    parsed, errors = _load(text)
    assert not errors, errors
    assert parsed is not None
    return parsed


# --- parsing and the required contract ---------------------------------------------------


def test_a_balanced_manifest_parses():
    parsed = _ok(BALANCED)
    assert parsed.library_ids == ("LIB001", "LIB002", "LIB003", "LIB004")
    assert parsed.schema_version == mf.SCHEMA_VERSION
    assert set(mf.REQUIRED_COLUMNS) <= set(parsed.columns)


def test_every_required_column_is_required():
    for missing in mf.REQUIRED_COLUMNS:
        header = [c for c in mf.REQUIRED_COLUMNS if c != missing]
        text = ",".join(header) + "\n" + ",".join("x" for _ in header) + "\n"
        _, errors = _load(text)
        assert errors, f"a manifest without {missing} must be refused"
        assert any(missing in e for e in errors), errors


def test_a_library_id_must_be_unique():
    text = BALANCED + "LIB001,S009,D009,disease,BATCH_B\n"
    _, errors = _load(text)
    assert errors
    assert any("LIB001" in e for e in errors), errors


def test_an_empty_library_id_is_refused():
    text = "library_id,sample_id,donor_id,condition,technical_batch\n,S1,D1,control,B1\n"
    _, errors = _load(text)
    assert errors, "a row with no library_id cannot be matched to anything"


def test_row_order_does_not_change_the_manifest():
    lines = BALANCED.strip().splitlines()
    reversed_text = lines[0] + "\n" + "\n".join(reversed(lines[1:])) + "\n"
    assert _ok(BALANCED).sha256 == _ok(reversed_text).sha256, (
        "the same libraries in a different order are the same manifest"
    )


def test_trailing_whitespace_and_blank_lines_do_not_change_the_digest():
    noisy = BALANCED.replace(",BATCH_A\n", " ,BATCH_A \n", 1) + "\n\n"
    assert _ok(noisy).sha256 == _ok(BALANCED).sha256


def test_a_different_condition_does_change_the_digest():
    changed = BALANCED.replace("LIB002,S002,D002,disease", "LIB002,S002,D002,control")
    assert _ok(changed).sha256 != _ok(BALANCED).sha256


# --- privacy -----------------------------------------------------------------------------


def test_direct_identifier_columns_are_refused_by_name():
    for banned in ("patient_name", "name", "mrn", "medical_record_number"):
        text = (
            "library_id,sample_id,donor_id,condition,technical_batch," + banned + "\n"
            "LIB001,S001,D001,control,BATCH_A,whatever\n"
        )
        _, errors = _load(text)
        assert errors, f"{banned} must be refused"
        assert any(banned in e for e in errors), errors


def test_an_unknown_column_is_refused_rather_than_carried():
    text = (
        "library_id,sample_id,donor_id,condition,technical_batch,favourite_colour\n"
        "LIB001,S001,D001,control,BATCH_A,green\n"
    )
    _, errors = _load(text)
    assert errors, "a strict column contract cannot carry columns it has never heard of"


def test_the_documented_optional_columns_are_accepted():
    header = ",".join(mf.REQUIRED_COLUMNS + mf.OPTIONAL_COLUMNS)
    row = ",".join(["LIB001", "S001", "D001", "control", "BATCH_A"]
                   + ["v" for _ in mf.OPTIONAL_COLUMNS])
    parsed = _ok(header + "\n" + row + "\n")
    for column in mf.OPTIONAL_COLUMNS:
        assert column in parsed.columns


def test_a_value_that_looks_like_a_direct_identifier_is_refused():
    for value, label in [("Chen Wei-Ting", "a name with a space"),
                         ("A123456789", "an ID-card pattern"),
                         ("0912345678", "a phone number"),
                         ("1987-05-04", "a date of birth")]:
        text = (
            "library_id,sample_id,donor_id,condition,technical_batch\n"
            f"LIB001,S001,{value},control,BATCH_A\n"
        )
        _, errors = _load(text)
        assert errors, f"{label} must be refused as a donor_id"


# --- the shapes a real study takes -------------------------------------------------------


def test_one_sample_may_be_split_across_libraries():
    text = (
        "library_id,sample_id,donor_id,condition,technical_batch\n"
        "LIB001,S001,D001,control,BATCH_A\n"
        "LIB002,S001,D001,control,BATCH_A\n"
    )
    parsed = _ok(text)
    assert parsed.library_ids == ("LIB001", "LIB002")
    assert len(set(parsed.column("sample_id").values())) == 1


def test_one_donor_may_have_several_samples():
    text = (
        "library_id,sample_id,donor_id,condition,technical_batch\n"
        "LIB001,S001,D001,control,BATCH_A\n"
        "LIB002,S002,D001,control,BATCH_A\n"
    )
    parsed = _ok(text)
    assert len(set(parsed.column("donor_id").values())) == 1
    assert len(set(parsed.column("sample_id").values())) == 2


def test_a_blank_value_stays_unknown_and_is_never_filled_in():
    text = (
        "library_id,sample_id,donor_id,condition,technical_batch\n"
        "LIB001,S001,D001,control,BATCH_A\n"
        "LIB002,S002,D002,,BATCH_A\n"
    )
    parsed = _ok(text)
    assert parsed.column("condition")["LIB002"] is None, "blank must not become a value"
    assert parsed.column("condition")["LIB001"] == "control"


# --- matching against what the pipeline actually found ------------------------------------


def test_matching_is_exact_and_order_independent():
    parsed = _ok(BALANCED)
    forward = mf.match_libraries(parsed, ["LIB001", "LIB002", "LIB003", "LIB004"])
    backward = mf.match_libraries(parsed, ["LIB004", "LIB003", "LIB002", "LIB001"])
    assert forward == [] and backward == [], (forward, backward)


def test_a_library_missing_from_the_manifest_fails_closed():
    parsed = _ok(BALANCED)
    errors = mf.match_libraries(parsed, ["LIB001", "LIB002", "LIB003", "LIB004", "LIB005"])
    assert errors and any("LIB005" in e for e in errors), errors


def test_a_manifest_row_with_no_library_fails_closed():
    parsed = _ok(BALANCED)
    errors = mf.match_libraries(parsed, ["LIB001", "LIB002", "LIB003"])
    assert errors and any("LIB004" in e for e in errors), errors


def test_matching_is_never_fuzzy():
    parsed = _ok(BALANCED)
    for found, label in [(["lib001", "LIB002", "LIB003", "LIB004"], "case"),
                         (["LIB001_1", "LIB002", "LIB003", "LIB004"], "suffix"),
                         (["LIB1", "LIB002", "LIB003", "LIB004"], "zero padding")]:
        errors = mf.match_libraries(parsed, found)
        assert errors, f"{label} must not be matched through"


# --- confounding: structural, not statistical ---------------------------------------------


def test_a_balanced_design_is_separable():
    report = mf.confounding(_ok(BALANCED), "condition", "technical_batch")
    assert report["separable"] is True
    assert report["fully_confounded"] is False
    assert report["n_components"] == 1


def test_a_fully_confounded_design_is_named_as_such():
    report = mf.confounding(_ok(CONFOUNDED), "condition", "technical_batch")
    assert report["separable"] is False
    assert report["fully_confounded"] is True
    assert report["n_components"] == 2, report


def test_an_imbalanced_but_connected_design_is_still_separable():
    """Partial imbalance is reported, not used to block anything."""
    report = mf.confounding(_ok(IMBALANCED), "condition", "technical_batch")
    assert report["separable"] is True, "every batch holds both conditions"
    assert report["fully_confounded"] is False
    assert report["balanced"] is False, "5/1 and 1/5 is not balanced"


def test_the_contingency_table_is_reported_as_counts_only():
    report = mf.confounding(_ok(BALANCED), "condition", "technical_batch")
    table = report["table"]
    assert table["control"]["BATCH_A"] == 1
    assert table["disease"]["BATCH_B"] == 1
    flat = repr(report)
    for library in ("LIB001", "LIB002", "D001", "S001"):
        assert library not in flat, f"{library} must not appear in a confounding report"


def test_a_single_batch_is_not_confounded_it_is_just_nothing_to_correct():
    text = (
        "library_id,sample_id,donor_id,condition,technical_batch\n"
        "LIB001,S001,D001,control,BATCH_A\n"
        "LIB002,S002,D002,disease,BATCH_A\n"
    )
    report = mf.confounding(_ok(text), "condition", "technical_batch")
    assert report["n_batches"] == 1
    assert report["separable"] is True


def test_no_arbitrary_statistical_threshold_decides_anything():
    """The decision is structural; Cramer's V must not be what gates the run."""
    source = Path(mf.__file__).read_text()
    lowered = source.lower()
    assert "cramer" not in lowered and "0.8" not in source, (
        "confounding must be decided by identifiability, not by a tuned threshold"
    )


def test_unknown_conditions_do_not_join_the_confounding_check():
    text = (
        "library_id,sample_id,donor_id,condition,technical_batch\n"
        "LIB001,S001,D001,control,BATCH_A\n"
        "LIB002,S002,D002,disease,BATCH_A\n"
        "LIB003,S003,D003,,BATCH_B\n"
    )
    report = mf.confounding(_ok(text), "condition", "technical_batch")
    assert "unknown" not in report["table"], "a blank condition is not a group"
    assert report["n_unknown_condition"] == 1


# --- the snapshot -------------------------------------------------------------------------


def test_a_snapshot_round_trips_to_the_same_digest():
    parsed = _ok(BALANCED)
    reloaded, errors = mf.parse_manifest(mf.normalized_csv(parsed), source="snapshot")
    assert not errors, errors
    assert reloaded.sha256 == parsed.sha256


def test_the_public_summary_carries_no_row_level_detail():
    summary = mf.public_summary(_ok(BALANCED))
    flat = repr(summary)
    for secret in ("LIB001", "S001", "D001"):
        assert secret not in flat, f"{secret} leaked into the public summary"
    assert summary["n_libraries"] == 4
    assert summary["sha256"].startswith(_ok(BALANCED).sha256[:12])


def test_loading_from_a_file_reports_a_missing_path_rather_than_raising():
    with tempfile.TemporaryDirectory() as tmp:
        parsed, errors = mf.load_manifest(Path(tmp) / "nope.csv")
    assert parsed is None and errors


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, not a stop
            failures.append(test.__name__)
            print(f"  ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
