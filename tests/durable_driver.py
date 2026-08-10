"""One step of a durable-resume test, run as its own process.

`test_durable_resume.py` spawns this. It exists as a file rather than as a
string inside the test because the thing being tested is that a *different
interpreter* can pick the run up — mocking the process boundary would test
nothing, and the boundary is the whole feature.

Every mode prints one JSON object on stdout and nothing else that matters, so
the parent can read the outcome without parsing logs.

    python tests/durable_driver.py pause    <workdir>
    python tests/durable_driver.py answer   <workdir> <run_id> <decision> [overrides_json]
    python tests/durable_driver.py describe <workdir> <run_id>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import persistence  # noqa: E402
from src.policy import GatePolicy  # noqa: E402
from src.run import continue_workflow, run_workflow  # noqa: E402
from src.state import summarize  # noqa: E402
from tests import fixtures  # noqa: E402

#: No QC thresholds on purpose: `apply_cell_qc_filter` refuses to filter without
#: them and stops, which is a gate reached early enough to keep the test quick
#: and is the gate an operator most often has to answer for real.
CONFIG_WITHOUT_THRESHOLDS: dict[str, object] = {}


def _emit(**fields: object) -> None:
    print("RESULT " + json.dumps(fields, default=str))


def _bundle(workdir: Path) -> tuple[dict, dict]:
    matrix = fixtures.bundle_for(
        {"input_type": "matrix", "matrix_kind": "filtered"}, workdir / "bundle"
    )
    reference = fixtures.make_reference(workdir, "ref", genomes=["GRCh38"])
    return (
        {"paths": [str(matrix)]},
        {"species": "human", "transcriptome": str(reference), **CONFIG_WITHOUT_THRESHOLDS},
    )


def pause(workdir: Path) -> int:
    """Run until the first gate, then exit with the run still suspended."""
    workdir.mkdir(parents=True, exist_ok=True)
    bundle, config = _bundle(workdir)
    final = run_workflow(
        project="durable", input_bundle=bundle, config=config,
        runs_dir=str(workdir / "runs"),
        policy=GatePolicy(interactive=True),
        checkpointer_kind="sqlite",
        decide=None,  # nobody here to answer; that is the point
    )
    run_dir = workdir / "runs" / final["run_id"]
    _emit(
        run_id=final["run_id"],
        status=summarize(final)["status"],
        waiting_at=(final.get("pending_review") or {}).get("step"),
        checkpoint=str(persistence.checkpoint_path(run_dir)),
        checkpoint_exists=persistence.checkpoint_path(run_dir).exists(),
        steps_done=[r["step"] for r in final["step_results"]],
    )
    return 0


def answer(workdir: Path, run_id: str, decision: str, overrides: dict) -> int:
    """Pick the suspended run up and answer it, in this fresh interpreter."""
    asked: list[dict] = []

    def decide(request: dict) -> dict:
        asked.append(request)
        # Answer the first gate as instructed, then accept whatever follows, so
        # the run reaches an end instead of stopping at the next question.
        if len(asked) == 1:
            return {"decision": decision, "rationale": "durable test",
                    "operator": "process-b", "overrides": overrides}
        return {"decision": "accept", "rationale": "", "operator": "process-b"}

    try:
        final = continue_workflow(
            run_id=run_id, runs_dir=str(workdir / "runs"),
            policy=GatePolicy(interactive=True), decide=decide,
        )
    except persistence.ResumeError as exc:
        _emit(error=type(exc).__name__, message=str(exc))
        return 2

    report = summarize(final)
    filter_output = (final.get("artifacts") or {}).get("apply_cell_qc_filter") or {}
    _emit(
        run_id=final.get("run_id"),
        status=report["status"],
        first_question_step=asked[0].get("step") if asked else None,
        first_question_offered=asked[0].get("revisable") if asked else None,
        gates_answered=len(asked),
        decisions=[(d["step"], d["decision"], d.get("overrides")) for d in
                   final.get("human_decisions") or []],
        config_min_genes=(final.get("config") or {}).get("min_genes"),
        filter_state=filter_output.get("filter_state"),
        thresholds=filter_output.get("thresholds"),
        # Every step the *run* has recorded, which after a checkpoint resume
        # includes the ones the first process ran: `step_results` is reduced
        # state and comes back with the checkpoint. What this process did is a
        # question for the audit log, not for state.
        steps_in_state=[r["step"] for r in final["step_results"] if r["status"] != "skipped"],
        errors=report["errors"][:2],
    )
    return 0


def describe(workdir: Path, run_id: str) -> int:
    """Report what a checkpoint says, without answering anything."""
    try:
        checkpointer = persistence.open_saved_checkpointer(workdir / "runs" / run_id)
    except persistence.ResumeError as exc:
        _emit(error=type(exc).__name__, message=str(exc))
        return 2
    persistence.close_checkpointer(checkpointer)
    _emit(ok=True)
    return 0


def main(argv: list[str]) -> int:
    mode, workdir = argv[1], Path(argv[2])
    if mode == "pause":
        return pause(workdir)
    if mode == "answer":
        overrides = json.loads(argv[5]) if len(argv) > 5 else {}
        return answer(workdir, argv[3], argv[4], overrides)
    if mode == "describe":
        return describe(workdir, argv[3])
    raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
