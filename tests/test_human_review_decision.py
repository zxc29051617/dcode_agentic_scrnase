"""Tests for `human_review_decision`: the question asked at the mainline gate.

What matters is that the question is about the *run*. The escalation gate asks
about one step and the last verdict says it all; this gate asks whether to
publish, and the last verdict is only ever a detail about whichever step
happened to finish last.

Run with `python tests/test_human_review_decision.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.registry import load_skill  # noqa: E402
from src.run import run_workflow  # noqa: E402
from tests import fixtures  # noqa: E402

review = load_skill("human_review_decision")


def _finished_artifacts(**overrides):
    """A run that got all the way through, as its artifacts would look."""
    artifacts = {
        "apply_cell_qc_filter": {
            "filter_state": "applied",
            "filter_summary": {"n_after": 2159, "n_removed": 74},
            "thresholds": {"min_genes": 200, "max_pct_mito": 15, "chosen_by": "operator"},
            "warnings": [],
        },
        "detect_doublets": {
            "doublet_summary": {"n_doublets": 22, "removed": False},
            "per_sample": {
                "pbmc_1k_v2": {"assessed": True, "expected_rate": 0.0077,
                               "expected_rate_source": "10x loading table"},
            },
            "warnings": [],
        },
        "run_integration": {"integration_summary": {"integrated": True, "n_batches": 2}},
        "run_clustering": {"clustering_summary": {"n_clusters": 15, "resolution": 1.0}},
        "find_markers": {"marker_summary": {"n_clusters_tested": 15}},
        "annotate_cells": {
            "annotation_state": "annotated",
            "annotation_summary": {"n_cells": 2159, "n_cell_types": 13,
                                   "model": "Immune_All_Low.pkl"},
            "per_cluster": {"0": {"median_conf_score": 1.0}, "8": {"median_conf_score": 0.95}},
            "warnings": [],
        },
    }
    artifacts.update(overrides)
    return artifacts


def _run(artifacts):
    return review.run({"artifacts": artifacts, "run_dir": "."})


# --- it describes the run, not the last step ------------------------------------


def test_the_findings_are_run_level_numbers():
    result = _run(_finished_artifacts())
    findings = result["findings"]
    assert findings["cells_analysed"] == 2159
    assert findings["clusters"] == 15
    assert findings["cell_types"] == 13
    assert findings["samples"] == 2


def test_concerns_are_gathered_from_every_step_not_only_the_last():
    """The reason to stop at a final gate is usually several steps back."""
    artifacts = _finished_artifacts()
    artifacts["run_qc_metrics"] = {"warnings": ["something odd happened early on"]}
    concerns = _run(artifacts)["open_concerns"]
    assert any("something odd happened early on" in c for c in concerns)


def test_the_decisions_made_carry_where_each_value_came_from():
    made = {d["parameter"]: d for d in _run(_finished_artifacts())["decisions_made"]}
    assert made["min_genes"]["value"] == 200
    assert made["min_genes"]["source"] == "operator"
    assert made["expected doublet rate (pbmc_1k_v2)"]["source"] == "10x loading table"


def test_a_low_confidence_cluster_is_named():
    artifacts = _finished_artifacts()
    artifacts["annotate_cells"]["per_cluster"]["9"] = {"median_conf_score": 0.2}
    concerns = _run(artifacts)["open_concerns"]
    assert any("median annotation confidence" in c and "9" in c for c in concerns)


# --- it says what accepting would do ----------------------------------------------


def test_a_complete_analysis_reports_nothing_missing():
    result = _run(_finished_artifacts())
    assert result["accepting_would"]["report_would_be_missing"] == []
    assert result["metrics"]["analysis_complete"] is True
    assert result["warnings"] == []


def test_an_unannotated_run_says_the_report_will_have_no_cell_types():
    """Accepting here is legitimate; not knowing what you accepted is not."""
    artifacts = _finished_artifacts()
    artifacts["annotate_cells"] = {
        "annotation_state": "needs_review",
        "warnings": ["no celltypist_model chosen"],
    }
    result = _run(artifacts)
    missing = result["accepting_would"]["report_would_be_missing"]
    assert any("cell type annotation" in item for item in missing)
    assert result["metrics"]["analysis_complete"] is False
    assert any("incomplete" in w for w in result["warnings"])


def test_an_unfiltered_run_says_every_loaded_cell_is_still_in():
    artifacts = _finished_artifacts()
    artifacts["apply_cell_qc_filter"] = {"filter_state": "needs_review", "warnings": []}
    missing = _run(artifacts)["accepting_would"]["report_would_be_missing"]
    assert any("QC filtering" in item for item in missing)


def test_an_unmade_choice_is_listed_as_a_concern():
    artifacts = _finished_artifacts()
    artifacts["apply_cell_qc_filter"] = {"filter_state": "needs_review", "warnings": []}
    concerns = _run(artifacts)["open_concerns"]
    assert any("filter_state" in c and "never made" in c for c in concerns)


# --- it does not decide ---------------------------------------------------------------


def test_it_returns_a_question_not_an_answer():
    """Choosing on the operator's behalf is the one thing this must never do."""
    result = _run(_finished_artifacts())
    assert "decision" not in result
    assert result["recommended_next_tool"] == "build_report"


def test_no_artifacts_is_an_error_rather_than_an_empty_review():
    assert _run({})["errors"]


# --- and the graph actually calls it ---------------------------------------------------


def test_the_mainline_gate_asks_this_question_instead_of_the_last_verdict():
    """It was dead code: the graph built the final gate from the last judge."""
    asked: list[dict] = []

    def decide(request):
        asked.append(request)
        return {"decision": "accept", "rationale": "test"}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = fixtures.bundle_for({"input_type": "matrix", "matrix_kind": "filtered"},
                                     root / "b")
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        run_workflow(
            project="test",
            input_bundle={"paths": [str(bundle)]},
            config={"species": "human", "transcriptome": str(reference),
                    "min_genes": 1, "max_pct_mito": 100},
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=decide,
            runs_dir=str(root / "runs"),
        )

    final = [r for r in asked if r.get("gate") == "human_review_decision"]
    assert final, "the mainline gate was never reached"
    request = final[-1]
    assert "review" in request, "the gate did not consult human_review_decision"
    assert {"findings", "accepting_would", "open_concerns"} <= set(request["review"])
    assert "evidence" not in request, "the last step's evidence is not the run-level question"


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
