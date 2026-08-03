"""Wiring checks for the workflow graph.

These assert *routing*, not biology: that every registry step is reachable, that
each route lands where the v4 graph says it should, and that a bad verdict
cannot slip past the human gate. Bundles are real fixtures, so `ingest_validate`
does genuine detection; the steps after it are still scaffolds.

Run with `python tests/test_graph_smoke.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph import build_graph  # noqa: E402
from src.judge import JudgeResult, StubJudge  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.provenance import AuditLog  # noqa: E402
from src.registry import MAINLINE, REGISTRY  # noqa: E402
from src.run import DEFAULT_RECURSION_LIMIT  # noqa: E402
from src.state import new_run_state, summarize  # noqa: E402
from tests import fixtures  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "judge_result.schema.json"

WALK = GatePolicy(headless_decision="accept")
"""Opt-in policy that accepts at gates so a full route can be walked."""

IMPLEMENTED = [
    "ingest_validate",
    "resolve_species",
    "count_matrix_classify",
    "load_filtered_counts",
    "standardize_count_data",
]
"""Skills with a real `run()` on the filtered-matrix route; everything else is a scaffold."""


def _run(config, *, policy=WALK, judge=None):
    """Run the graph on a fixture bundle with a working reference.

    A reference is supplied by default so these tests exercise *routing*;
    reference resolution has its own suite. Pass `transcriptome` in `config` to
    override it, or point it somewhere missing to test the blocked path.
    """
    graph = build_graph(policy=policy, judge=judge or StubJudge())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = fixtures.bundle_for(config, root / "bundle")
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        state = new_run_state(
            project="test",
            config={"species": "human", "transcriptome": str(reference), **config},
            input_bundle={"paths": [str(bundle)]},
            runs_dir=root / "runs",
        )
        final = graph.invoke(state, config={"recursion_limit": DEFAULT_RECURSION_LIMIT})
        final["_audit"] = AuditLog(state["audit_log_path"]).read()
    return final


def _steps(final) -> list[str]:
    return [r["step"] for r in final["step_results"]]


def test_filtered_matrix_route_reaches_report():
    final = _run({"input_type": "matrix", "matrix_kind": "filtered"})
    steps = _steps(final)
    assert "load_filtered_counts" in steps
    assert "load_raw_counts" not in steps
    assert steps[-1] == "build_report"
    for step in MAINLINE:
        assert step in steps, f"mainline step {step} never ran"


def test_fastq_route_visits_the_upstream_steps_in_order():
    """Routing only.

    Three of these steps now shell out to real tools, and no fixture can satisfy
    Cell Ranger — a completed FASTQ-to-report run needs real data and ~20
    minutes, which is verified outside the suite (see cellranger_count/SKILL.md).
    What is checked here is that the route visits the right nodes in the right
    order: structural checks before the expensive quality pass, both before
    counting, and the classifier after.
    """
    final = _run({"input_type": "fastq"})
    steps = _steps(final)
    assert steps[:6] == [
        "ingest_validate",
        # A table lookup both routes need, before the split...
        "resolve_species",
        # ...and the 32 GB transcriptome only this one does, after it.
        "resolve_reference",
        "fastq_preflight",
        "fastq_qc",
        "cellranger_count",
    ]
    assert steps[6] == "count_matrix_classify"


def test_fastq_qc_really_runs_inside_the_graph():
    """The fixture carries real reads, so FastQC has something to assess."""
    final = _run({"input_type": "fastq"})
    report = next(r for r in final["step_results"] if r["step"] == "fastq_qc")
    assert report["status"] == "ok"
    assert report["errors"] == []
    qc = final["artifacts"]["fastq_qc"]
    assert sorted(qc["per_read_role"]) == ["I1", "R1", "R2"]
    assert qc["metrics"]["q30_r2"] is not None


def test_a_failed_count_never_reaches_the_report():
    """Cell Ranger cannot run on a fixture, and that must stop the pipeline."""
    final = _run({"input_type": "fastq"}, policy=GatePolicy())
    steps = _steps(final)
    assert final["halted"] is True
    assert "build_report" not in steps
    assert "run_qc_metrics" not in steps
    assert any("cellranger" in e for e in final["errors"])


def test_a_raw_matrix_always_goes_through_cell_calling_review():
    """Nothing has called cells on a raw matrix, so the route cannot skip it."""
    final = _run({"input_type": "matrix", "matrix_kind": "raw"})
    steps = _steps(final)
    assert "load_raw_counts" in steps
    assert "cell_calling_review" in steps
    assert final["artifacts"]["load_raw_counts"]["cell_calling_resolved"] is False


def test_an_unchosen_cell_count_cannot_be_accepted_into_the_mainline():
    """How many cells to keep is the operator's call; `accept` is not a number."""
    final = _run({"input_type": "matrix", "matrix_kind": "raw"}, policy=WALK)
    steps = _steps(final)
    assert "cell_calling_review" in steps
    assert final["artifacts"]["cell_calling_review"]["cell_calling_state"] == "needs_review"
    assert "run_qc_metrics" not in steps, "the mainline must not run on every barcode"
    assert "build_report" not in steps


def test_choosing_a_cell_count_lets_the_raw_route_continue():
    final = _run({"input_type": "matrix", "matrix_kind": "raw", "force_cells": 400})
    steps = _steps(final)
    review = final["artifacts"]["cell_calling_review"]
    assert review["cell_calling_state"] == "resolved"
    assert review["n_cells"] == 400
    assert review["selection"]["chosen_by"] == "operator"
    assert steps.index("cell_calling_review") < steps.index("run_qc_metrics")
    assert steps[-1] == "build_report"


def test_sample_qc_triage_runs_when_enabled():
    final = _run({"input_type": "matrix", "matrix_kind": "filtered", "sample_qc_triage": True})
    steps = _steps(final)
    assert steps[:3] == ["ingest_validate", "resolve_species", "sample_qc_triage"]
    assert steps.count("sample_qc_triage") == 1, "triage must not loop"


def test_unnamed_matrix_bundle_stops_at_the_first_gate():
    """A directory with no raw/filtered signal is a question for a person."""
    final = _run({"input_type": "matrix", "matrix_kind": "unknown"}, policy=GatePolicy())
    assert final["halted"] is True
    assert _steps(final) == ["ingest_validate"]
    assert final["judge_results"][-1]["verdict"] == "warn"


def test_ambiguous_matrix_cannot_be_accepted_into_the_mainline():
    """Even `accept` cannot route an unresolved raw/filtered split forward."""
    final = _run({"input_type": "matrix", "matrix_kind": "unknown"}, policy=WALK)
    steps = _steps(final)
    assert "count_matrix_classify" in steps
    assert "run_qc_metrics" not in steps
    assert "build_report" not in steps


def test_default_policy_will_not_wave_the_final_gate_through():
    final = _run({"input_type": "matrix", "matrix_kind": "filtered"}, policy=GatePolicy())
    assert final["halted"] is True
    assert "build_report" not in _steps(final), "report must not be built without a decision"
    assert "annotate_cells" in _steps(final)


def test_failing_verdict_halts_the_run():
    class FailAtQC(StubJudge):
        def judge(self, step, payload):
            if step != "run_qc_metrics":
                return super().judge(step, payload)
            return JudgeResult(
                step=step,
                verdict="fail",
                score=5,
                reasons=["synthetic failure"],
                evidence={},
                needs_human_review=True,
            )

    final = _run({"input_type": "matrix", "matrix_kind": "filtered"}, policy=GatePolicy(), judge=FailAtQC())
    assert final["halted"] is True
    assert "apply_cell_qc_filter" not in _steps(final)
    assert final["human_decisions"][-1]["step"] == "run_qc_metrics"


def test_every_step_is_judged_and_audited():
    final = _run({"input_type": "matrix", "matrix_kind": "filtered"})
    judged = {j["step"] for j in final["judge_results"]}
    for record in final["step_results"]:
        if REGISTRY[record["step"]].judge:
            assert record["step"] in judged, f"{record['step']} ran without a judge"

    events = {entry["event"] for entry in final["_audit"]}
    assert {"step_start", "step_end", "judge", "human_gate_open"} <= events


def test_scaffolds_are_reported_not_hidden():
    final = _run({"input_type": "matrix", "matrix_kind": "filtered"})
    report = summarize(final)

    assert report["implemented"] == IMPLEMENTED
    assert report["crashed"] == []
    assert report["errors"] == []
    assert set(report["scaffolds"]) == {r["step"] for r in final["step_results"]} - set(IMPLEMENTED)
    assert report["verdicts"]["ingest_validate"] == "pass"
    assert report["verdicts"]["standardize_count_data"] == "pass"
    assert report["verdicts"]["run_qc_metrics"] == "pass (scaffold)"

    for verdict in final["judge_results"]:
        if verdict["step"] in IMPLEMENTED:
            assert verdict["score"] > 0
        else:
            assert verdict["score"] == 0
            assert any("SCAFFOLD" in reason for reason in verdict["reasons"])


def test_missing_reference_blocks_before_preflight_even_runs():
    """`resolve_reference` catches it first, so no FASTQ work is started at all.

    It only runs on the FASTQ branch, so a count-matrix run never reaches it.
    """
    final = _run(
        {"input_type": "fastq", "transcriptome": "/nonexistent/reference"},
        policy=GatePolicy(),
    )
    assert _steps(final) == ["ingest_validate", "resolve_species", "resolve_reference"]
    assert final["halted"] is True
    verdict = next(j for j in final["judge_results"] if j["step"] == "resolve_reference")
    assert verdict["verdict"] == "fail"
    assert any("does not exist" in e for e in final["errors"])


def test_species_mismatch_stops_the_run():
    """The silent-failure case: a mouse run pointed at a human reference."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        human_ref = fixtures.make_reference(root, "human_ref", genomes=["GRCh38"])
        final = _run(
            {"input_type": "fastq", "species": "mouse", "transcriptome": str(human_ref)},
            policy=GatePolicy(),
        )
    assert final["halted"] is True
    assert "fastq_preflight" not in _steps(final)
    assert any("species mismatch" in e for e in final["errors"])


def test_fastq_preflight_passes_with_a_valid_reference_and_real_reads():
    """`bundle_for`'s FASTQs are empty placeholders; this needs real read content."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ref = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        bundle = fixtures.make_fastq_dir_with_reads(root / "bundle")
        graph = build_graph(policy=WALK, judge=StubJudge())
        state = new_run_state(
            project="test",
            config={"species": "human", "transcriptome": str(ref)},
            input_bundle={"paths": [str(bundle)]},
            runs_dir=root / "runs",
        )
        final = graph.invoke(state, config={"recursion_limit": DEFAULT_RECURSION_LIMIT})

    steps = _steps(final)
    assert "fastq_preflight" in steps
    assert "cellranger_count" in steps
    preflight_verdict = next(j for j in final["judge_results"] if j["step"] == "fastq_preflight")
    assert preflight_verdict["verdict"] == "pass"


def test_ingest_detection_drives_routing_not_config():
    """The FASTQ fixture routes upstream even though config claims a matrix."""
    final = _run({"input_type": "fastq", "matrix_kind": "filtered"})
    detected = final["artifacts"]["ingest_validate"]
    assert detected["input_type"] == "fastq"
    assert detected["needs_upstream_preprocessing"] is True
    assert detected["sample_ids"] == ["S"], "parsed from the fixture's Illumina names"
    assert "fastq_preflight" in _steps(final)
    assert "count_matrix_classify" in _steps(final), "config said matrix; detection won"


def test_judge_results_match_the_published_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    allowed = set(schema["properties"])
    required = set(schema["required"])

    final = _run({"input_type": "matrix", "matrix_kind": "filtered"})
    assert final["judge_results"], "nothing was judged"
    for verdict in final["judge_results"]:
        assert set(verdict) <= allowed, f"extra keys: {set(verdict) - allowed}"
        assert required <= set(verdict), f"missing keys: {required - set(verdict)}"
        assert verdict["verdict"] in {"pass", "warn", "fail"}
        assert 0 <= verdict["score"] <= 100


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append((test.__name__, exc))
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
