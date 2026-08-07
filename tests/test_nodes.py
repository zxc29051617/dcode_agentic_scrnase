"""Tests for `src/nodes.py`, the three node kinds the graph is built from.

Everything here was previously covered only through the graph, which exercises
the nodes but cannot say much about them: a state delta is asserted by its
downstream effect rather than directly, and the failure paths — a judge that
raises, a step that returns something unserialisable — are hard to provoke from
the outside.

What matters about these nodes is mostly what they are *not allowed* to write.
Each returns a delta, and the delta is where the pipeline's guarantees live: a
judge that could write `artifacts` would be a model editing analysis results,
whatever the prompt said.

Run with `python tests/test_nodes.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import nodes  # noqa: E402
from src.judge import JudgeResult, StubJudge  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.provenance import AuditLog  # noqa: E402
from src.state import new_run_state  # noqa: E402


def _state(root: Path, **overrides):
    state = new_run_state(project="t", config={"species": "human"}, runs_dir=root)
    state.update(overrides)
    return state


def _events(state) -> list[dict]:
    return AuditLog(state["audit_log_path"]).read()


# --- step nodes: what they write ------------------------------------------------


def test_a_step_writes_its_output_under_its_own_name():
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp))
        delta = nodes.make_step_node("ingest_validate")(state)
    assert set(delta["artifacts"]) == {"ingest_validate"}
    assert delta["current_step"] == "ingest_validate"
    assert delta["step_results"][0]["step"] == "ingest_validate"


def test_step_warnings_and_errors_are_tagged_with_the_step():
    """Unprefixed messages in a shared list cannot be traced back."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), input_bundle={"paths": ["/does/not/exist"]})
        delta = nodes.make_step_node("ingest_validate")(state)
    for message in delta["errors"] + delta["warnings"]:
        assert message.startswith("[ingest_validate]")


def test_a_step_records_its_output_for_a_later_resume():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = _state(root)
        nodes.make_step_node("ingest_validate")(state)
        run_dir = Path(state["audit_log_path"]).parent
        assert (run_dir / "ingest_validate" / "output.json").exists()


def test_a_step_delta_is_serialisable():
    """numpy scalars subclass float and pass json.dumps; msgpack refuses them."""
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp))
        delta = nodes.make_step_node("ingest_validate")(state)

    def numpy_values(obj):
        if isinstance(obj, dict):
            return [f for v in obj.values() for f in numpy_values(v)]
        if isinstance(obj, (list, tuple)):
            return [f for v in obj for f in numpy_values(v)]
        return [obj] if isinstance(obj, (np.generic, np.ndarray)) else []

    assert numpy_values(delta) == []
    json.dumps(delta)


def test_both_ends_of_a_step_reach_the_audit_log():
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp))
        nodes.make_step_node("ingest_validate")(state)
        events = [r["event"] for r in _events(state)]
    assert "step_start" in events and "step_end" in events


# --- step nodes: skipping a resumed step -------------------------------------------


def test_a_resumed_step_is_skipped_and_consumes_its_flag():
    """The flag has to be spent, or a later `revise` would skip too."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "prior.h5ad"
        artifact.write_bytes(b"x")
        state = _state(
            root,
            artifacts={"run_pca": {"adata_path": str(artifact)}},
            resumed_steps={"run_pca": True},
        )
        delta = nodes.make_step_node("run_pca")(state)
    assert delta["step_results"][0]["status"] == "skipped"
    assert delta["resumed_steps"] == {"run_pca": False}
    assert "artifacts" not in delta, "a skipped step must not rewrite what it reused"


def test_a_resumed_step_whose_artifact_vanished_runs_anyway():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = _state(
            root,
            artifacts={"ingest_validate": {"adata_path": str(root / "gone.h5ad")}},
            resumed_steps={"ingest_validate": True},
        )
        delta = nodes.make_step_node("ingest_validate")(state)
    assert delta["step_results"][0]["status"] != "skipped"


# --- judge nodes: what they may not write --------------------------------------------


def test_a_judge_can_write_nothing_but_its_verdict():
    """Not a prompt instruction — the delta has no other key to write to."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), step_results=[
            {"step": "run_pca", "status": "ok", "warnings": [], "errors": []}
        ])
        delta = nodes.make_judge_node("run_pca", "judge_pca", StubJudge())(state)
    assert set(delta) == {"judge_results"}


def _markers_output(genes_per_cluster=25, clusters=15):
    return {
        "marker_table_path": "runs/x/find_markers/markers.csv",
        "top_markers": {
            str(c): [{"gene": f"G{i}", "logfoldchange": 1.0} for i in range(genes_per_cluster)]
            for c in range(clusters)
        },
        "marker_summary": {"n_clusters_tested": clusters},
    }


def test_the_judge_sees_fewer_markers_per_cluster_but_every_cluster():
    """Narrowing must not drop a group — that would hide a cluster entirely."""
    full = _markers_output()
    view, notes = nodes._judge_view("find_markers", full)
    assert len(view["top_markers"]) == 15, "every cluster still has to be there"
    assert all(len(g) == 5 for g in view["top_markers"].values())
    assert notes == ["top_markers: showing the top 5 of 25 per group"]


def test_narrowing_leaves_the_step_output_alone():
    """Only the judge's view narrows; the artifact keeps every gene."""
    full = _markers_output()
    nodes._judge_view("find_markers", full)
    assert len(full["top_markers"]["0"]) == 25
    assert full["marker_table_path"].endswith("markers.csv")


def test_a_step_with_no_preview_rule_is_passed_through_untouched():
    output = {"pca_summary": {"n_comps": 50}}
    view, notes = nodes._judge_view("run_pca", output)
    assert view == output and notes == []


def test_an_output_already_small_enough_is_not_marked_abridged():
    """Saying 'abridged' when nothing was cut would be its own kind of lie."""
    view, notes = nodes._judge_view("find_markers", _markers_output(genes_per_cluster=3))
    assert notes == []
    assert len(view["top_markers"]["0"]) == 3


def test_the_judge_is_told_when_it_is_seeing_a_subset():
    """Otherwise it can read an elision as an absence and judge on that."""
    seen = {}

    class Recording(StubJudge):
        def judge(self, step, payload):
            seen.update(payload)
            return super().judge(step, payload)

    with tempfile.TemporaryDirectory() as tmp:
        state = _state(
            Path(tmp),
            step_results=[{"step": "find_markers", "status": "ok", "warnings": [], "errors": []}],
            artifacts={"find_markers": _markers_output()},
        )
        nodes.make_judge_node("find_markers", "judge_markers", Recording())(state)
    assert seen["output_is_abridged"] == ["top_markers: showing the top 5 of 25 per group"]
    assert len(seen["output"]["top_markers"]["0"]) == 5


def test_advice_reaches_the_gate_but_not_the_artifacts():
    """The suggestion is for a person. Nothing may carry it into the results."""
    advising = JudgeResult(
        step="apply_cell_qc_filter", verdict="warn", score=50,
        reasons=["median is 5.4"], evidence={}, needs_human_review=True,
        advice=[{"parameter": "max_pct_mito", "suggested_value": 15,
                 "rationale": "a 5% cut removes 54.6%", "confidence": "medium"}],
    )

    class Advising:
        def judge(self, step, payload):
            return advising

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = _state(root, step_results=[
            {"step": "apply_cell_qc_filter", "status": "ok", "warnings": [], "errors": []}
        ])
        delta = nodes.make_judge_node("apply_cell_qc_filter", "judge_x", Advising())(state)
        assert set(delta) == {"judge_results"}, "advice must not open a second write path"
        assert delta["judge_results"][0]["advice"][0]["suggested_value"] == 15

        # And it is in front of the person at the moment they are asked.
        state["judge_results"] = delta["judge_results"]
        nodes.make_human_gate_node(GatePolicy(headless_decision="stop"))(state)
        opened = next(r for r in _events(state) if r["event"] == "human_gate_open")
    assert opened["advice"][0]["parameter"] == "max_pct_mito"


def test_a_judge_that_raises_becomes_a_failing_verdict_not_a_crash():
    class Exploding:
        def judge(self, step, payload):
            raise RuntimeError("endpoint unreachable")

    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), step_results=[
            {"step": "run_pca", "status": "ok", "warnings": [], "errors": []}
        ])
        delta = nodes.make_judge_node("run_pca", "judge_pca", Exploding())(state)
    verdict = delta["judge_results"][0]
    assert verdict["verdict"] == "fail"
    assert verdict["needs_human_review"] is True
    assert any("endpoint unreachable" in reason for reason in verdict["reasons"])


def test_judging_a_step_that_never_ran_fails_rather_than_passing():
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp), step_results=[])
        delta = nodes.make_judge_node("run_pca", "judge_pca", StubJudge())(state)
    assert delta["judge_results"][0]["verdict"] == "fail"


def test_the_judge_sees_the_step_output_it_is_scoring():
    seen = {}

    class Recording(StubJudge):
        def judge(self, step, payload):
            seen.update(payload)
            return super().judge(step, payload)

    with tempfile.TemporaryDirectory() as tmp:
        state = _state(
            Path(tmp),
            step_results=[{"step": "run_pca", "status": "ok", "warnings": [], "errors": []}],
            artifacts={"run_pca": {"pca_summary": {"n_comps": 50}}},
            metrics={"run_pca": {"n_comps": 50}},
        )
        nodes.make_judge_node("run_pca", "judge_pca", StubJudge())(state)
        nodes.make_judge_node("run_pca", "judge_pca", Recording())(state)
    assert seen["output"]["pca_summary"]["n_comps"] == 50
    assert seen["metrics"] == {"n_comps": 50}


# --- gate nodes ------------------------------------------------------------------------


def _judged(root: Path, verdict: str = "warn"):
    return _state(root, judge_results=[JudgeResult(
        step="run_pca", verdict=verdict, score=50, reasons=["something"],
        evidence={}, needs_human_review=True,
    ).model_dump()])


def test_a_headless_gate_applies_the_policy_and_records_who_decided():
    with tempfile.TemporaryDirectory() as tmp:
        state = _judged(Path(tmp))
        delta = nodes.make_human_gate_node(GatePolicy(headless_decision="stop"))(state)
    entry = delta["human_decisions"][0]
    assert entry["decision"] == "stop"
    assert entry["operator"] == "policy default"
    assert entry["decided_at"]
    assert delta["halted"] is True and delta["status"] == "halted"


def test_accepting_does_not_halt():
    with tempfile.TemporaryDirectory() as tmp:
        state = _judged(Path(tmp))
        delta = nodes.make_human_gate_node(GatePolicy(headless_decision="accept"))(state)
    assert "halted" not in delta
    assert delta["pending_review"] is None


def test_an_unrecognised_decision_becomes_stop_rather_than_continuing():
    """Anything the gate cannot parse must be the safe answer, not the loose one."""
    class Weird(GatePolicy):
        pass

    with tempfile.TemporaryDirectory() as tmp:
        state = _judged(Path(tmp))
        delta = nodes.make_human_gate_node(Weird(headless_decision="banana"))(state)
    assert delta["human_decisions"][0]["decision"] == "stop"


def test_the_gate_shows_the_evidence_behind_the_verdict():
    with tempfile.TemporaryDirectory() as tmp:
        state = _judged(Path(tmp))
        state["artifacts"] = {"run_pca": {"evidence": {"variance": 0.6}}}
        nodes.make_human_gate_node(GatePolicy(headless_decision="stop"))(state)
        opened = next(r for r in _events(state) if r["event"] == "human_gate_open")
    assert opened["evidence"] == {"variance": 0.6}


def test_a_review_skill_replaces_the_last_step_evidence_at_the_mainline_gate():
    with tempfile.TemporaryDirectory() as tmp:
        state = _judged(Path(tmp))
        state["artifacts"] = {
            "run_pca": {"evidence": {"variance": 0.6}},
            "run_clustering": {"clustering_summary": {"n_clusters": 12}},
        }
        nodes.make_human_gate_node(
            GatePolicy(headless_decision="stop"),
            node_name="human_review_decision",
            review_skill="human_review_decision",
        )(state)
        opened = next(r for r in _events(state) if r["event"] == "human_gate_open")
    assert "review" in opened and opened["review"]["findings"]["clusters"] == 12
    assert "evidence" not in opened


def test_both_ends_of_a_gate_reach_the_audit_log():
    with tempfile.TemporaryDirectory() as tmp:
        state = _judged(Path(tmp))
        nodes.make_human_gate_node(GatePolicy(headless_decision="stop"))(state)
        events = [r["event"] for r in _events(state)]
    assert "human_gate_open" in events and "human_gate_close" in events


# --- the payload a step is handed -------------------------------------------------------


def test_the_payload_carries_a_run_dir_derived_from_the_audit_log():
    """Steps that write files need one, and two conventions would diverge."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp))
        payload = nodes.build_payload(state, "run_pca")
    assert payload["run_dir"] == str(Path(state["audit_log_path"]).parent)
    assert payload["step"] == "run_pca"


def test_per_step_config_overrides_the_shared_config():
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(Path(tmp))
        state["config"] = {"n_comps": 50, "steps": {"run_pca": {"n_comps": 10}}}
        payload = nodes.build_payload(state, "run_pca")
    assert payload["config"]["n_comps"] == 10
    assert "steps" not in payload["config"], "the step map is not itself config"


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
