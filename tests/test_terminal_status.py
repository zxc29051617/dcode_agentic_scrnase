"""How a run says it ended, and whether that is true.

`completed` used to mean "the graph reached an end node without halting", which
is a fact about the graph and not about the analysis. It was true of the worst
outcome the pipeline can produce: nobody chose the QC thresholds, the gate
offered `accept`, `accept` could not carry an unfiltered object into the
mainline, and the route ended — no clustering, no markers, no report, and
`status: completed` with exit code 0.

So completion is defined by the artefact instead. `build_report` is the last
node on every route that finishes; a run with no entry for it did not get there.

Also here: the report's own verdict now reaches a gate like every other step's,
which is what makes `revise` at that gate rebuild the report.

Run with `python tests/test_terminal_status.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src import run as run_module  # noqa: E402
from src.judge import JudgeResult, StubJudge  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.run import EXIT_CODES, run_workflow  # noqa: E402
from src.run import main as cli_main  # noqa: E402  (this module defines its own `main`)
from src.state import summarize  # noqa: E402
from tests import fixtures  # noqa: E402

#: Enough to let the mainline run. Omit them and `apply_cell_qc_filter` refuses,
#: which is the case half of these tests are about.
THRESHOLDS = {"min_genes": 1, "max_pct_mito": 100}


class WarnAtReport(StubJudge):
    """A verdict on the report. The old wiring recorded it and then ignored it."""

    def judge(self, step, payload):
        if step != "build_report":
            return super().judge(step, payload)
        return JudgeResult(
            step=step, verdict="warn", score=40,
            reasons=["the report is missing four of its main figures"],
            evidence={}, needs_human_review=True,
        )


@contextmanager
def judged_by(client):
    """Make `run_workflow` use this judge; it takes a backend name, not an object."""
    original = run_module.get_judge
    run_module.get_judge = lambda _backend=None, _model=None: client
    try:
        yield
    finally:
        run_module.get_judge = original


def _setup(root: Path, kind: str = "filtered") -> tuple[dict, dict]:
    matrix = fixtures.bundle_for({"input_type": "matrix", "matrix_kind": kind}, root / "bundle")
    reference = fixtures.make_reference(root, "ref", genomes=["GRCh38"])
    return ({"paths": [str(matrix)]},
            {"species": "human", "transcriptome": str(reference)})


def _run(root: Path, config: dict, *, kind: str = "filtered", policy=None, **kwargs):
    bundle, base = _setup(root, kind)
    return run_workflow(
        project="terminal", input_bundle=bundle, config={**base, **config},
        runs_dir=str(root / "runs"),
        policy=policy or GatePolicy(headless_decision="accept"),
        **kwargs,
    )


def _steps(final) -> list[str]:
    return [r["step"] for r in final["step_results"]]


# --- a run with no report did not complete ---------------------------------------------


def test_unchosen_qc_thresholds_cannot_be_accepted_into_a_completed_run():
    """The headline case: `accept` cannot manufacture a filtered object."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), {})
        report = summarize(final)

    assert final["artifacts"]["apply_cell_qc_filter"]["filter_state"] == "needs_review"
    assert "build_report" not in _steps(final)
    assert report["status"] == "halted", "a run with no report has not completed"
    assert report["halted"] is True
    assert "apply_cell_qc_filter" in report["halt_reason"]
    assert "filter_state" in report["halt_reason"]


def test_an_unchosen_cell_count_cannot_be_accepted_into_a_completed_run():
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), THRESHOLDS, kind="raw")
        report = summarize(final)

    assert final["artifacts"]["cell_calling_review"]["cell_calling_state"] == "needs_review"
    assert "build_report" not in _steps(final)
    assert report["status"] == "halted"
    assert "cell_calling_review" in report["halt_reason"]


def test_an_ambiguous_matrix_cannot_be_accepted_into_a_completed_run():
    """No unresolved `*_state` here — the route simply never reaches the report."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), THRESHOLDS, kind="unknown")
        report = summarize(final)

    assert "build_report" not in _steps(final)
    assert report["status"] == "halted"
    assert "before build_report" in report["halt_reason"]


def test_a_run_that_reaches_the_report_still_completes():
    """The change must not turn working runs into halted ones."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), THRESHOLDS)
        report = summarize(final)

    assert "build_report" in _steps(final)
    assert report["status"] == "completed"
    assert report["halted"] is False
    assert report["errors"] == []


def test_a_human_stopping_the_run_keeps_their_own_reason():
    """`halted` predates this and says who stopped it, not the generic message."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), THRESHOLDS, policy=GatePolicy(headless_decision="stop"))
        report = summarize(final)

    assert report["status"] == "halted"
    assert report["halt_reason"].startswith("human stopped the run at")


def test_the_halt_reason_says_what_to_do_about_it():
    """It is not broken, it is waiting for a number nobody supplied."""
    with tempfile.TemporaryDirectory() as tmp:
        final = _run(Path(tmp), {})
    reason = summarize(final)["halt_reason"]
    assert "stopped without a report" in reason
    assert "revise" in reason


# --- what the shell is told -----------------------------------------------------------


def _cli(root: Path, config: dict, *extra: str) -> int:
    bundle, base = _setup(root)
    argv = ["--input", bundle["paths"][0], "--species", "human",
            "--reference", base["transcriptome"], "--runs-dir", str(root / "runs"), *extra]
    for key, value in config.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return cli_main(argv)


def test_a_halted_run_does_not_exit_zero():
    """A script asking "did I get a report" was told yes."""
    with tempfile.TemporaryDirectory() as tmp:
        code = _cli(Path(tmp), {}, "--headless-decision", "accept")
    assert code != 0
    assert code == EXIT_CODES["halted"]


def test_a_run_stopped_by_a_person_does_not_exit_zero():
    with tempfile.TemporaryDirectory() as tmp:
        code = _cli(Path(tmp), {}, "--headless-decision", "stop")
    assert code == EXIT_CODES["halted"]


def test_a_completed_run_exits_zero():
    with tempfile.TemporaryDirectory() as tmp:
        code = _cli(Path(tmp), THRESHOLDS, "--headless-decision", "accept")
    assert code == 0


def test_the_exit_codes_are_distinct_so_a_caller_can_tell_them_apart():
    assert EXIT_CODES["completed"] == 0
    assert EXIT_CODES["needs_review"] == 0, "a paused run is waiting, not broken"
    assert len({EXIT_CODES[s] for s in ("completed", "failed", "halted", "running")}) == 4


# --- the report is judged like everything else ------------------------------------------
#
# These run interactively with a decider rather than on a headless policy,
# because the fixture is thin enough that `normalize_hvg_prepare` genuinely
# warns — a headless `stop` never gets near the report, and a headless `accept`
# cannot distinguish "the report gate opened and was accepted" from "the report
# gate never opened at all", which is exactly the bug under test.


def _answer_report_with(*answers: str, otherwise: str = "accept"):
    """Accept every gate except the report's, where the test supplies the answers."""
    scripted = iter(answers)
    seen: list[str] = []

    def decide(request: dict) -> dict:
        step = request.get("step")
        seen.append(step)
        if step == "build_report":
            return {"decision": next(scripted, otherwise), "rationale": "test",
                    "operator": "tester"}
        return {"decision": "accept", "rationale": "", "operator": "tester"}

    decide.seen = seen  # type: ignore[attr-defined]
    return decide


def _interactive(root: Path, decide, **policy_kwargs):
    return _run(
        root, THRESHOLDS,
        policy=GatePolicy(interactive=True, **policy_kwargs),
        checkpointer=persistence.make_checkpointer("memory"),
        decide=decide,
    )


def test_a_warning_on_the_report_reaches_a_human_gate():
    """It used to edge straight to END: the one judge whose fail was advisory."""
    decide = _answer_report_with("stop")
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(WarnAtReport()):
            final = _interactive(Path(tmp), decide)
        report = summarize(final)

    assert "build_report" in _steps(final)
    assert "build_report" in decide.seen, "the report's verdict never reached anybody"
    assert report["status"] == "halted"
    assert report["halt_reason"] == "human stopped the run at build_report"


def test_accepting_a_warned_report_finishes_the_run_without_rebuilding_it():
    decide = _answer_report_with("accept")
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(WarnAtReport()):
            final = _interactive(Path(tmp), decide)
        report = summarize(final)

    assert "build_report" in decide.seen, "the gate has to have opened at all"
    assert report["status"] == "completed"
    assert _steps(final).count("build_report") == 1, "accepting does not rebuild it"


def test_revising_the_report_builds_it_again():
    """The point of gating it: `revise` there has to redo the report."""
    decide = _answer_report_with("revise", "accept")
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(WarnAtReport()):
            final = _interactive(Path(tmp), decide)

    assert _steps(final).count("build_report") == 2, "revise has to rebuild the report"
    decisions = [d["decision"] for d in final["human_decisions"] if d["step"] == "build_report"]
    assert decisions == ["revise", "accept"]
    assert summarize(final)["status"] == "completed"


def test_a_report_gate_cannot_loop_forever():
    """The revision cap covers this gate too, and it fails toward stopping."""
    decide = _answer_report_with(*(["revise"] * 20), otherwise="revise")
    with tempfile.TemporaryDirectory() as tmp:
        with judged_by(WarnAtReport()):
            final = _interactive(Path(tmp), decide, max_revisions_per_step=2)

    report = summarize(final)
    assert report["status"] == "halted"
    assert _steps(final).count("build_report") == 3, "two revisions, then it stops"
    assert "max_revisions_per_step" in " ".join(
        final["human_decisions"][-1]["rejected_overrides"]
    )


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
