"""Workflow state for the scRNA-seq LangGraph orchestrator.

The state is the only thing shared between deterministic analysis nodes, local
judge nodes, and the human gate. Analysis nodes write outputs and metrics;
judge nodes only append verdicts; the human gate only appends decisions.
"""

from __future__ import annotations

import json
import operator
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

Verdict = Literal["pass", "warn", "fail"]
StepStatus = Literal["ok", "scaffold", "error", "skipped"]

#: Where the run as a whole stands. Derivable from verdicts and decisions, but
#: only by rules a reader has to already know — worth a field of its own so the
#: report and any caller can ask directly.
RunStatus = Literal["running", "needs_review", "halted", "failed", "completed"]


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer for per-step dicts keyed by step name."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class WorkflowState(TypedDict, total=False):
    """Graph state, mirroring `docs/langgraph_scRNA_workflow.md` section 7."""

    run_id: str
    project: str
    config: dict[str, Any]
    sample_metadata: dict[str, Any]
    input_bundle: dict[str, Any]
    audit_log_path: str
    run_metadata_path: str

    current_step: str
    artifacts: Annotated[dict[str, Any], merge_dicts]
    metrics: Annotated[dict[str, Any], merge_dicts]
    step_results: Annotated[list[dict[str, Any]], operator.add]
    judge_results: Annotated[list[dict[str, Any]], operator.add]
    human_decisions: Annotated[list[dict[str, Any]], operator.add]
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]

    halted: bool
    halt_reason: str

    #: Where the run stands as a whole. Defaults to `running`, so a caller that
    #: never looks at it sees exactly the behaviour it saw before.
    status: RunStatus

    #: The question a paused gate is waiting on, written by the gate before it
    #: suspends and cleared when it is answered. `interrupt()` alone leaves
    #: nothing here — it raises out of its node, so that node never returns a
    #: delta — which is why asking and answering are two nodes. Without it a
    #: paused run was indistinguishable from one that finished to anything that
    #: did not unpack `__interrupt__` itself.
    pending_review: dict[str, Any] | None

    #: `{step: True}` for steps a previous run already completed, seeded when
    #: resuming. A step consumes its own flag by setting it False, so a later
    #: `revise` on that same step runs for real instead of skipping again.
    resumed_steps: Annotated[dict[str, bool], merge_dicts]


def new_run_state(
    *,
    project: str = "untitled",
    config: dict[str, Any] | None = None,
    input_bundle: dict[str, Any] | None = None,
    sample_metadata: dict[str, Any] | None = None,
    runs_dir: str | Path = "runs",
    run_id: str | None = None,
) -> WorkflowState:
    """Build a fresh state, and record what the environment was at run start.

    The metadata is written here rather than gathered when a report is built:
    those are different moments, and a report regenerated later would otherwise
    describe an environment that never produced these results.

    Passing `run_id` reuses an existing run directory, which is how a resumed
    run finds the artifacts it is going to skip. Its metadata is left exactly
    as it was: rewriting it would replace the git commit, package versions and
    timestamp of the run that actually produced those artifacts with today's.
    """
    from .provenance import capture_run_metadata

    resuming = run_id is not None
    run_id = run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(runs_dir) / run_id
    audit_path = run_dir / "audit.jsonl"
    metadata_path = run_dir / "run_metadata.json"

    resolved_config = dict(config or {})
    run_dir.mkdir(parents=True, exist_ok=True)
    if not (resuming and metadata_path.exists()):
        metadata = capture_run_metadata(run_id=run_id, config=resolved_config)
        metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return WorkflowState(
        run_id=run_id,
        project=project,
        config=resolved_config,
        sample_metadata=dict(sample_metadata or {}),
        input_bundle=dict(input_bundle or {}),
        audit_log_path=str(audit_path),
        run_metadata_path=str(metadata_path),
        current_step="",
        artifacts={},
        metrics={},
        step_results=[],
        judge_results=[],
        human_decisions=[],
        warnings=[],
        errors=[],
        halted=False,
        status="running",
        pending_review=None,
        resumed_steps={},
    )


def step_output(state: WorkflowState, step: str) -> dict[str, Any]:
    """Return the recorded output of `step`, or an empty dict."""
    return (state.get("artifacts") or {}).get(step) or {}


def last_judge(state: WorkflowState) -> dict[str, Any] | None:
    """Return the most recent judge result, or None if nothing was judged yet."""
    results = state.get("judge_results") or []
    return results[-1] if results else None


def summarize(state: WorkflowState) -> dict[str, Any]:
    """Compact end-of-run summary: what ran, what was faked, what stopped."""
    results = state.get("step_results") or []
    by_status: dict[str, list[str]] = {}
    status_of: dict[str, str] = {}
    for record in results:
        by_status.setdefault(record["status"], []).append(record["step"])
        status_of[record["step"]] = record["status"]

    def label(judge: dict[str, Any]) -> str:
        # A verdict on a step that never ran is not a result; say so inline.
        suffix = " (scaffold)" if status_of.get(judge["step"]) == "scaffold" else ""
        return f"{judge['verdict']}{suffix}"

    return {
        "run_id": state.get("run_id"),
        "project": state.get("project"),
        "steps_run": len(results),
        "implemented": by_status.get("ok", []),
        "scaffolds": by_status.get("scaffold", []),
        # `crashed` is a broken skill; `errors` is a working skill reporting a bad input.
        "crashed": by_status.get("error", []),
        "errors": list(state.get("errors") or []),
        "warnings": list(state.get("warnings") or []),
        "verdicts": {j["step"]: label(j) for j in state.get("judge_results") or []},
        "halted": bool(state.get("halted")),
        "halt_reason": state.get("halt_reason"),
        # A run waiting at a gate is not a finished one, and used to summarize
        # identically to a clean completion.
        "status": state.get("status") or "running",
        "pending_review": state.get("pending_review"),
        "skipped": by_status.get("skipped", []),
        "audit_log_path": state.get("audit_log_path"),
        "run_metadata_path": state.get("run_metadata_path"),
    }
