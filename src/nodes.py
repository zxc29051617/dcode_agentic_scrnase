"""Node factories: deterministic steps, judges, and the human gate.

Every node returns a state delta only. Analysis nodes never judge, judge nodes
never write artifacts, and the gate never rewrites either.
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.types import interrupt

from .judge import JudgeClient, JudgeResult
from .policy import GatePolicy
from .provenance import AuditLog
from .registry import REGISTRY, call_skill
from .state import WorkflowState, last_judge, step_output

NodeFn = Callable[[WorkflowState], dict[str, Any]]


def _audit(state: WorkflowState) -> AuditLog:
    return AuditLog(state.get("audit_log_path", "runs/unknown/audit.jsonl"))


def build_payload(state: WorkflowState, step: str) -> dict[str, Any]:
    """Assemble the payload handed to a skill's `run()`."""
    config = state.get("config") or {}
    step_config = (config.get("steps") or {}).get(step, {})
    return {
        "step": step,
        "run_id": state.get("run_id"),
        "config": {**{k: v for k, v in config.items() if k != "steps"}, **step_config},
        "input_bundle": state.get("input_bundle") or {},
        "sample_metadata": state.get("sample_metadata") or {},
        "artifacts": state.get("artifacts") or {},
    }


def make_step_node(step: str) -> NodeFn:
    """Wrap one deterministic skill as a graph node."""

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)
        audit.append("step_start", step=step, run_id=state.get("run_id"))

        result = call_skill(step, build_payload(state, step))
        output = result["output"]
        record = {
            "step": step,
            "status": result["status"],
            "warnings": result["warnings"],
            "errors": result["errors"],
        }
        audit.append("step_end", **record, output_keys=sorted(output))

        delta: dict[str, Any] = {
            "current_step": step,
            "artifacts": {step: output},
            "step_results": [record],
            "warnings": [f"[{step}] {w}" for w in result["warnings"]],
            "errors": [f"[{step}] {e}" for e in result["errors"]],
        }
        if isinstance(output.get("metrics"), dict):
            delta["metrics"] = {step: output["metrics"]}
        return delta

    return node


def make_judge_node(step: str, judge_tool: str, client: JudgeClient) -> NodeFn:
    """Score the most recent result of `step` and append a verdict."""

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)
        record = next(
            (r for r in reversed(state.get("step_results") or []) if r["step"] == step),
            {"step": step, "status": "error", "warnings": [], "errors": ["step never ran"]},
        )
        payload = {
            "step": step,
            "status": record["status"],
            "warnings": record["warnings"],
            "errors": record["errors"],
            "output": step_output(state, step),
            "metrics": (state.get("metrics") or {}).get(step, {}),
        }

        try:
            verdict = client.judge(step, payload)
        except Exception as exc:  # noqa: BLE001 - a broken judge must not pass silently
            verdict = JudgeResult(
                step=step,
                verdict="fail",
                score=0,
                reasons=[f"{judge_tool} could not produce a verdict: {type(exc).__name__}: {exc}"],
                evidence={},
                suggested_action="Check the judge backend before trusting this run",
                needs_human_review=True,
            )

        audit.append("judge", judge_tool=judge_tool, **verdict.model_dump())
        return {"judge_results": [verdict.model_dump()]}

    return node


def make_human_gate_node(policy: GatePolicy, *, node_name: str = "human_gate") -> NodeFn:
    """Stop for a person and record their decision as `accept | revise | stop`.

    Interactive runs block on LangGraph's `interrupt`; headless runs halt so a
    warn/fail is never silently waved through.
    """

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)
        verdict = last_judge(state) or {}
        step = verdict.get("step") or state.get("current_step") or ""
        request = {
            "gate": node_name,
            "step": step,
            "verdict": verdict.get("verdict"),
            "score": verdict.get("score"),
            "reasons": verdict.get("reasons", []),
            "suggested_action": verdict.get("suggested_action"),
        }
        audit.append("human_gate_open", **request)

        if policy.interactive:
            raw = interrupt(request)
            decision = raw if isinstance(raw, dict) else {"decision": str(raw)}
        else:
            decision = {
                "decision": policy.headless_decision,
                "rationale": f"non-interactive run: policy default {policy.headless_decision!r}",
            }

        choice = str(decision.get("decision", "stop")).lower()
        if choice not in {"accept", "revise", "stop"}:
            choice = "stop"

        entry = {
            "gate": node_name,
            "step": step,
            "decision": choice,
            "rationale": decision.get("rationale", ""),
        }
        audit.append("human_gate_close", **entry)

        delta: dict[str, Any] = {"human_decisions": [entry]}
        if choice == "stop":
            delta["halted"] = True
            delta["halt_reason"] = f"human stopped the run at {step or node_name}"
        return delta

    return node


def make_gate_router(policy: GatePolicy) -> Callable[[WorkflowState], str]:
    """`continue` when the policy accepts the last verdict, else `human_gate`."""

    def router(state: WorkflowState) -> str:
        verdict = last_judge(state)
        if verdict is None:
            return "human_gate"
        return policy.route(JudgeResult.model_validate(verdict))

    return router


def make_branch_router(
    policy: GatePolicy, branch: Callable[[WorkflowState], str]
) -> Callable[[WorkflowState], str]:
    """Apply the gate first, then the step's own branch decision."""
    gate = make_gate_router(policy)

    def router(state: WorkflowState) -> str:
        if gate(state) == "human_gate":
            return "human_gate"
        return branch(state)

    return router


def assert_registry_covered(node_names: set[str]) -> None:
    """Fail loudly if a registry step was left out of the graph."""
    missing = sorted(name for name in REGISTRY if name not in node_names)
    if missing:
        raise AssertionError(f"registry steps missing from the graph: {missing}")
