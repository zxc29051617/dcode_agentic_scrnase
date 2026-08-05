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
StepStatus = Literal["ok", "scaffold", "error"]


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


def new_run_state(
    *,
    project: str = "untitled",
    config: dict[str, Any] | None = None,
    input_bundle: dict[str, Any] | None = None,
    sample_metadata: dict[str, Any] | None = None,
    runs_dir: str | Path = "runs",
) -> WorkflowState:
    """Build a fresh state, and record what the environment was at run start.

    The metadata is written here rather than gathered when a report is built:
    those are different moments, and a report regenerated later would otherwise
    describe an environment that never produced these results.
    """
    from .provenance import capture_run_metadata

    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(runs_dir) / run_id
    audit_path = run_dir / "audit.jsonl"
    metadata_path = run_dir / "run_metadata.json"

    resolved_config = dict(config or {})
    metadata = capture_run_metadata(run_id=run_id, config=resolved_config)
    run_dir.mkdir(parents=True, exist_ok=True)
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
        "audit_log_path": state.get("audit_log_path"),
        "run_metadata_path": state.get("run_metadata_path"),
    }
