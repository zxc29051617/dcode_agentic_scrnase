"""Pausing at a gate and resuming a run, through the real graph.

The behaviour worth pinning is not that resume is fast — it is that it is
honest: a paused run must not look finished, a resumed step must not be reused
when its artifacts are gone or the config moved, and asking to redo a step must
actually redo it.

Run with `python tests/test_resume.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.graph import build_graph  # noqa: E402
from src.judge import StubJudge  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.run import DEFAULT_RECURSION_LIMIT, run_workflow  # noqa: E402
from src.state import new_run_state, summarize  # noqa: E402
from tests import fixtures  # noqa: E402

#: The same operator choices the graph suite uses, for the same reason: these
#: tests are about resuming, not about thresholds.
CHOICES = {"min_genes": 1, "max_pct_mito": 100}


#: Built once per run directory. Regenerating it would re-gzip the matrix, and
#: gzip writes its own modification time into the header — so the bytes change
#: even though the contents do not, and `plan_resume` correctly reads that as a
#: new input and refuses to reuse anything. A resume in real life points at the
#: data that is already there; so does this.
_BUNDLES: dict[str, tuple[dict, dict]] = {}


def _bundle(root: Path):
    key = str(root)
    if key not in _BUNDLES:
        bundle = fixtures.bundle_for(
            {"input_type": "matrix", "matrix_kind": "filtered"}, root / "b"
        )
        reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
        _BUNDLES[key] = (
            {"paths": [str(bundle)]},
            {"species": "human", "transcriptome": str(reference), **CHOICES},
        )
    return _BUNDLES[key]


def _run(root: Path, **kwargs):
    bundle, config = _bundle(root)
    return run_workflow(
        project="test", input_bundle=bundle, config=config,
        runs_dir=str(root / "runs"), **kwargs,
    )


# --- nothing changes unless asked ---------------------------------------------


def test_a_plain_run_still_reports_exactly_what_it_used_to():
    """No checkpointer, no decide, no resume: the previous behaviour verbatim.

    Every field the summary carried before is unchanged; `status` is new and
    says the run finished rather than leaving a reader to infer it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), policy=GatePolicy(headless_decision="accept"))
    report = summarize(final)
    assert report["halted"] is False
    assert report["errors"] == []
    assert report["status"] == "completed"
    assert report["pending_review"] is None
    assert report["skipped"] == [], "nothing may be skipped in a fresh run"


def test_a_stopped_run_says_it_halted():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), policy=GatePolicy(headless_decision="stop"))
    report = summarize(final)
    assert report["halted"] is True
    assert report["status"] == "halted"


# --- pausing --------------------------------------------------------------------


def test_a_paused_run_does_not_report_itself_as_finished():
    """`interrupt()` used to return quietly and summarize as a clean completion."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True, headless_decision="stop"),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=None,  # nobody available to answer
        )
    report = summarize(final)
    assert report["status"] == "needs_review"
    assert report["pending_review"], "the question has to survive into the summary"
    assert report["pending_review"]["step"]


def test_a_paused_graph_says_so_in_its_own_state_without_run_workflow():
    """The pause has to be a fact in state, not something one caller reconstructs.

    `run_workflow` used to attach `status` and `pending_review` to the dict it
    returned, so anything else driving the graph — this suite's own smoke tests,
    an API, a UI — saw `status="running"`, `halted=False`, `pending_review=None`
    and could not tell a suspended run from a live one. Nothing here goes
    through `run_workflow`; the assertions are on what the graph itself left.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle, config = _bundle(root)
        graph = build_graph(
            policy=GatePolicy(interactive=True),
            judge=StubJudge(),
            checkpointer=persistence.make_checkpointer("memory"),
        )
        state = new_run_state(
            project="test", config=config, input_bundle=bundle, runs_dir=root / "runs",
        )
        final = graph.invoke(state, config={
            "recursion_limit": DEFAULT_RECURSION_LIMIT,
            "configurable": {"thread_id": state["run_id"]},
        })

    assert "__interrupt__" in final, "precondition: the run actually paused"
    assert final["status"] == "needs_review"
    assert final["halted"] is False
    assert final["pending_review"], "the question has to be in state, not only in __interrupt__"
    assert final["pending_review"]["step"]
    assert final["pending_review"]["gate"]
    # The two views of the same pause must not be able to disagree.
    assert final["pending_review"] == getattr(final["__interrupt__"][0], "value", None)


def test_the_pending_question_carries_the_evidence_to_decide_on():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
        )
    request = summarize(final)["pending_review"]
    assert {"gate", "step", "verdict", "reasons", "evidence"} <= set(request)


def test_answering_the_gate_resumes_the_run():
    """interrupt -> Command(resume=) -> the run carries on to the end."""
    answered: list[str] = []

    def decide(request):
        answered.append(request["step"])
        return {"decision": "accept", "rationale": "test", "operator": "tester"}

    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=decide,
        )
    report = summarize(final)
    assert answered, "the gate must actually have been put to the decider"
    assert report["pending_review"] is None, "an answered question is no longer pending"
    assert final["human_decisions"][-1]["operator"] == "tester"
    assert final["human_decisions"][-1]["decided_at"]


def test_a_stop_answer_halts_rather_than_continuing():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(
            Path(tmp),
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=lambda request: {"decision": "stop", "rationale": "no"},
        )
    assert summarize(final)["status"] == "halted"


# --- resuming ---------------------------------------------------------------------


def test_a_resumed_run_skips_the_steps_it_already_finished():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, policy=GatePolicy(headless_decision="accept"))
        run_id = first["run_id"]
        completed = [r["step"] for r in first["step_results"] if r["status"] == "ok"]

        second = _run(root, policy=GatePolicy(headless_decision="accept"), resume_run_id=run_id)

    assert second["run_id"] == run_id, "a resume continues the same run, not a new one"
    skipped = summarize(second)["skipped"]
    assert skipped, "a completed run offers something to skip"
    assert set(skipped) <= set(completed)


def test_resuming_reuses_the_run_directory_rather_than_making_a_new_one():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, policy=GatePolicy(headless_decision="accept"))
        before = sorted(p.name for p in (root / "runs").iterdir())
        _run(root, policy=GatePolicy(headless_decision="accept"), resume_run_id=first["run_id"])
        after = sorted(p.name for p in (root / "runs").iterdir())
    assert before == after


def test_the_original_provenance_is_not_overwritten_by_a_resume():
    """Rewriting it would replace the commit and versions that produced the data."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, policy=GatePolicy(headless_decision="accept"))
        metadata_path = Path(first["run_metadata_path"])
        original = json.loads(metadata_path.read_text(encoding="utf-8"))
        _run(root, policy=GatePolicy(headless_decision="accept"), resume_run_id=first["run_id"])
        after = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert after == original


def test_a_deleted_artifact_means_that_step_runs_again():
    """The record is not the result; only the file on disk settles it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, policy=GatePolicy(headless_decision="accept"))
        run_dir = root / "runs" / first["run_id"]

        target = "run_pca"
        artifact = run_dir / target / "adata.h5ad"
        assert artifact.exists(), "precondition: the step wrote something"
        artifact.unlink()

        second = _run(root, policy=GatePolicy(headless_decision="accept"),
                      resume_run_id=first["run_id"])
    assert target not in summarize(second)["skipped"]


def test_a_changed_threshold_reruns_the_step_that_reads_it_and_everything_after():
    """Resuming onto a different config must not mix results from two analyses.

    This used to assert that *nothing* was reused, which was the old rule: one
    hash for the whole directory, and any difference threw all of it away. That
    was safe and needlessly expensive — `run_qc_metrics` does not read
    `min_genes`, and recomputing it could not produce a different answer.

    The property that actually matters is unchanged and is what is asserted now:
    nothing computed *from* the old threshold survives. `test_resume_validation`
    covers the same cut end to end for the other two triggers.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, policy=GatePolicy(headless_decision="accept"))

        bundle, config = _bundle(root)
        second = run_workflow(
            project="test", input_bundle=bundle,
            config={**config, "min_genes": 2},  # a different analysis
            runs_dir=str(root / "runs"),
            policy=GatePolicy(headless_decision="accept"),
            resume_run_id=first["run_id"],
        )
    skipped = summarize(second)["skipped"]
    assert "run_qc_metrics" in skipped, "it cannot depend on a threshold it never reads"
    for downstream in ("apply_cell_qc_filter", "detect_doublets", "run_pca",
                       "run_clustering", "annotate_cells"):
        assert downstream not in skipped, f"{downstream} came from the old threshold"


def test_a_revised_step_reruns_instead_of_being_skipped_again():
    """`revise` routes back to the same node; skipping there would loop forever."""
    decisions = iter(["revise", "accept", "accept", "accept", "accept", "accept"])

    def decide(request):
        return {"decision": next(decisions, "accept"), "rationale": "test"}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = _run(root, policy=GatePolicy(headless_decision="accept"))
        second = _run(
            root,
            policy=GatePolicy(interactive=True),
            checkpointer=persistence.make_checkpointer("memory"),
            decide=decide,
            resume_run_id=first["run_id"],
        )
    # Whatever was revised must appear as a real run, not only as a skip.
    ran = [r["step"] for r in second["step_results"] if r["status"] == "ok"]
    assert ran or summarize(second)["status"] == "halted"
    assert second["errors"] == []


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
