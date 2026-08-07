"""Tests for `cross_check_annotation`.

The marker database is committed, so these run against the real one rather than
a stand-in: the shape of `marker_db/scmayomap/markers.csv` is part of the contract,
and a fixture that agreed with the code but not with the file would hide a
break in exactly the place it matters.

Run with `python tests/test_cross_check_annotation.py` (or `tests/run_all.py`).
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.registry import load_skill  # noqa: E402

cc = load_skill("cross_check_annotation")

MARKER_COLUMNS = ("group", "names", "scores", "logfoldchanges",
                  "pvals", "pvals_adj", "pct_nz_group", "pct_nz_reference")


def _markers_for(cell_type: str, tissue: str = "blood") -> list[str]:
    database, _ = cc._load_database(tissue)
    return sorted(database[cell_type])


def _write_markers(path: Path, clusters: dict[str, list[str]], *,
                   lfc: float = 3.0, pct1: float = 0.9, pct2: float = 0.1,
                   padj: float = 1e-10) -> Path:
    """A find_markers-shaped table where each cluster's genes are given outright."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MARKER_COLUMNS)
        for cluster, genes in clusters.items():
            for gene in genes:
                writer.writerow([cluster, gene, 10.0, lfc, 1e-12, padj, pct1, pct2])
    return path


def _payload(root: Path, clusters: dict[str, list[str]], *,
             tissue: str | None = "blood",
             celltypist: dict[str, dict] | None = None) -> dict:
    table = _write_markers(root / "find_markers" / "markers.csv", clusters)
    return {
        "artifacts": {
            "find_markers": {"marker_table_path": str(table)},
            "annotate_cells": {"per_cluster": celltypist or {}},
        },
        "config": {"scmayomap_tissue": tissue},
        "run_dir": str(root),
    }


# --------------------------------------------------------------- the database

def test_committed_database_has_the_shape_the_step_reads():
    assert cc.DATABASE_PATH.exists(), f"{cc.DATABASE_PATH} is missing"
    with cc.DATABASE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["tissue", "cell_type", "gene"]
        rows = list(reader)
    assert len(rows) > 20000, f"only {len(rows)} markers; the file looks truncated"
    assert all(row["gene"] == row["gene"].upper() for row in rows[:500]), \
        "genes must be upper-cased at conversion time; the step does not fold case"


def test_blood_carries_the_cell_types_the_pbmc_run_depends_on():
    database, tissues = cc._load_database("blood")
    assert len(tissues) == 28, f"expected 28 tissues, got {len(tissues)}"
    for expected in ("CD14 Monocyte", "CD16 Monocyte", "Naive B cell",
                     "Memory B cell", "Neutrophil", "Platelet"):
        assert expected in database, f"{expected!r} missing from the blood database"


# ------------------------------------------------------- refusing to guess

def test_no_tissue_compares_nothing_and_hands_back_the_choice():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": _markers_for("CD14 Monocyte")}, tissue=None))

    assert result["cross_check_state"] == "not_compared"
    assert result["per_cluster"] == {}, "nothing may be compared without a tissue"
    assert len(result["evidence"]["available_tissues"]) == 28
    assert result["recommended_next_tool"] == "human_review_decision"
    assert not result["errors"], "an unmade choice is not an error"
    assert result["warnings"], "the run must say why it compared nothing"


def test_unknown_tissue_errors_rather_than_scoring_against_everything():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": ["CD14"]}, tissue="bloood"))
    assert result["cross_check_state"] == "unavailable"
    assert any("bloood" in e for e in result["errors"])


def test_missing_marker_table_is_an_error_not_an_empty_comparison():
    with tempfile.TemporaryDirectory() as tmp:
        result = cc.run({
            "artifacts": {"annotate_cells": {"per_cluster": {}}},
            "config": {"scmayomap_tissue": "blood"},
            "run_dir": tmp,
        })
    assert any("find_markers must run first" in e for e in result["errors"])


# ------------------------------------------------------------------ scoring

def test_a_cluster_made_of_one_cell_types_markers_scores_that_cell_type():
    """The end-to-end sanity check: plant the answer, see it come back first."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": _markers_for("Naive B cell")}))

    assert result["cross_check_state"] == "compared"
    candidates = result["per_cluster"]["0"]["database_candidates"]
    assert candidates[0]["cell_type"] == "Naive B cell", \
        f"planted Naive B cell markers, ranked {[c['cell_type'] for c in candidates]}"


def test_marker_rows_failing_scmayomaps_own_cutoffs_are_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genes = _markers_for("Naive B cell")
        # Every row fails pct.1 >= 0.25, so nothing survives to be scored.
        table = _write_markers(root / "find_markers" / "markers.csv",
                               {"0": genes}, pct1=0.10)
        result = cc.run({
            "artifacts": {"find_markers": {"marker_table_path": str(table)},
                          "annotate_cells": {"per_cluster": {}}},
            "config": {"scmayomap_tissue": "blood"},
            "run_dir": str(root),
        })
    assert any("survived" in e for e in result["errors"])


def test_a_gene_in_no_other_cluster_does_not_divide_by_zero():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        table = _write_markers(root / "find_markers" / "markers.csv",
                               {"0": _markers_for("Platelet")}, pct2=0.0)
        result = cc.run({
            "artifacts": {"find_markers": {"marker_table_path": str(table)},
                          "annotate_cells": {"per_cluster": {}}},
            "config": {"scmayomap_tissue": "blood"},
            "run_dir": str(root),
        })
    assert result["cross_check_state"] == "compared"
    assert not result["errors"]
    assert result["per_cluster"]["0"]["database_candidates"], "scored nothing"


def test_candidate_count_follows_the_jump_in_cumulative_variance():
    """One clear leader returns one candidate; a tied group returns the group."""
    one_leader = [("a", 0.60)] + [(chr(98 + i), 0.04) for i in range(10)]
    assert cc._top_candidates(one_leader) == 1

    three_tied = [("a", 0.30), ("b", 0.29), ("c", 0.28)] + \
                 [(chr(100 + i), 0.01) for i in range(10)]
    assert cc._top_candidates(three_tied) == 3


# -------------------------------------------------------------------- flags

def test_thin_evidence_is_flagged_however_high_the_score():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        few = _markers_for("Platelet")[: cc.MIN_MATCHED_GENES - 1]
        result = cc.run(_payload(root, {"0": few}))

    entry = result["per_cluster"]["0"]
    assert entry["n_matched_genes"] < cc.MIN_MATCHED_GENES
    assert "low_marker_evidence" in entry["flags"]
    assert any("little evidence" in w for w in result["warnings"])


def _tied_cluster(n: int = 25) -> list[str]:
    """Genes that split evenly between two cell types sharing no markers.

    Equal counts at equal per-gene scores means equal sums, so neither wins —
    which is what `ambiguous` is meant to catch. Mixing two whole marker lists
    does the opposite: see the marker-count test below.
    """
    database, _ = cc._load_database("blood")
    a, b = database["Basophil"], database["Erythroblast"]
    assert not (a & b), "the pair must be disjoint for the counts to decide"
    return sorted(a - b)[:n] + sorted(b - a)[:n]


def test_confidence_conflict_needs_both_halves():
    """Ambiguity alone is not a conflict — CellTypist has to have been sure."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tied = _tied_cluster()

        sure = cc.run(_payload(root, {"0": tied}, celltypist={
            "0": {"cell_type": "Basophils", "median_conf_score": 0.99, "n_cells": 400}}))
        unsure = cc.run(_payload(root, {"0": tied}, celltypist={
            "0": {"cell_type": "Basophils", "median_conf_score": 0.40, "n_cells": 400}}))

    flags = sure["per_cluster"]["0"]["flags"]
    assert "ambiguous" in flags, \
        f"evenly split markers should not resolve: {sure['per_cluster']['0']}"
    assert "confidence_conflict" in flags

    assert "ambiguous" in unsure["per_cluster"]["0"]["flags"], "same scores, same ambiguity"
    assert "confidence_conflict" not in unsure["per_cluster"]["0"]["flags"], \
        "both methods unsure is agreement about difficulty, not a conflict"


def test_a_long_marker_list_outscores_a_short_one():
    """The known bias, pinned so a future change to the scoring has to face it.

    Handed every marker of both CD14 Monocyte (12 in the database) and
    Neutrophil (84, of which 6 are shared), the score does not report a tie: it
    reports Neutrophil, decisively, because the denominator counts the cluster's
    matched genes rather than each cell type's own list. This is why clusters 0
    and 1 of the PBMC object come back as neutrophils, and why this step reports
    instead of deciding.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": sorted(
            set(_markers_for("CD14 Monocyte")) | set(_markers_for("Neutrophil")))}))

    entry = result["per_cluster"]["0"]
    assert entry["database_candidates"][0]["cell_type"] == "Neutrophil"
    assert entry["relative_margin"] > 0.5, \
        f"the longer list should win outright, margin was {entry['relative_margin']}"
    assert "ambiguous" not in entry["flags"], \
        "the method is not uncertain here — it is confidently biased, which is worse"


def test_labels_are_reported_verbatim_and_never_string_matched():
    """The design line: differing names raise no flag, and both survive intact.

    `Naive B cell` and `Naive B cells` name one population; `Platelet` and
    `Neutrophil` do not. Neither pair is the step's business — the flags are
    numeric, and the judge is handed both strings to read.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(
            root,
            {"0": _markers_for("Naive B cell"), "1": _markers_for("Platelet")},
            celltypist={
                "0": {"cell_type": "Naive B cells", "median_conf_score": 1.0, "n_cells": 249},
                "1": {"cell_type": "Neutrophils", "median_conf_score": 1.0, "n_cells": 22},
            },
        ))

    agreeing, disagreeing = result["per_cluster"]["0"], result["per_cluster"]["1"]
    assert agreeing["celltypist_label"] == "Naive B cells"
    assert agreeing["database_candidates"][0]["cell_type"] == "Naive B cell"
    assert disagreeing["celltypist_label"] == "Neutrophils"
    assert disagreeing["database_candidates"][0]["cell_type"] == "Platelet"
    assert "label_mismatch" not in disagreeing["flags"], \
        "the step must not adjudicate vocabulary; that is the judge's payload"


# ------------------------------------------------------------------ outputs

def test_full_scores_go_to_a_file_not_into_the_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": _markers_for("Memory B cell")}))

        path = Path(result["score_table_path"])
        assert path.exists()
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0].keys() >= {"cluster", "cell_type", "score", "rank"}
        # Only the top few candidates travel back; the file holds every cell type.
        assert len(rows) > len(result["per_cluster"]["0"]["database_candidates"])


def test_the_result_survives_json_round_trip():
    """State is checkpointed, so every value has to be plain JSON, not numpy."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": _markers_for("CD16 Monocyte")}))
    assert json.loads(json.dumps(result)) == result


def test_output_fields_match_what_the_contract_declares():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = cc.run(_payload(root, {"0": _markers_for("Platelet")}))
    for field in cc.OUTPUT_FIELDS:
        assert field in result, f"{field} declared in OUTPUT_FIELDS but not returned"


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
