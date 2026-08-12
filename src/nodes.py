"""Node factories: deterministic steps, judges, and the human gate.

Every node returns a state delta only. Analysis nodes never judge, judge nodes
never write artifacts, and the gate never rewrites either.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langgraph.types import interrupt

from . import persistence
from .judge import JudgeClient, JudgeResult
from .policy import GatePolicy
from .provenance import AuditLog, record_revision
from .registry import (
    REGISTRY,
    call_skill,
    coerce_overrides,
    revisable_from,
    steps_invalidated_by,
)
from .state import WorkflowState, last_judge, step_output

NodeFn = Callable[[WorkflowState], dict[str, Any]]


def _audit(state: WorkflowState) -> AuditLog:
    return AuditLog(state.get("audit_log_path", "runs/unknown/audit.jsonl"))


def build_payload(state: WorkflowState, step: str) -> dict[str, Any]:
    """Assemble the payload handed to a skill's `run()`."""
    config = state.get("config") or {}
    step_config = (config.get("steps") or {}).get(step, {})
    # Steps that write files (cellranger_count, build_report) need somewhere to
    # put them. The audit log already lives at the run's root, so derive it from
    # there rather than inventing a second convention.
    run_dir = str(Path(state.get("audit_log_path", "runs/unknown/audit.jsonl")).parent)
    return {
        "step": step,
        "run_id": state.get("run_id"),
        # The report titles itself with this. Without it `build_report` fell back
        # to the run id, so every report was headed by a timestamp.
        "project": state.get("project"),
        "run_dir": run_dir,
        "config": {**{k: v for k, v in config.items() if k != "steps"}, **step_config},
        "input_bundle": state.get("input_bundle") or {},
        "sample_metadata": state.get("sample_metadata") or {},
        "study_design": state.get("study_design") or {},
        "artifacts": state.get("artifacts") or {},
    }


def _run_dir(state: WorkflowState) -> Path:
    return Path(state.get("audit_log_path", "runs/unknown/audit.jsonl")).parent


def _skip_record(state: WorkflowState, step: str) -> dict[str, Any] | None:
    """The recorded output to reuse instead of running `step`, if there is one.

    Only a step flagged by a resume qualifies, and only while its flag is still
    set. The flag is consumed on use so that a `revise` decision, which routes
    back to this same node, runs the step for real rather than handing back the
    same artifacts the operator just asked to redo.
    """
    if not (state.get("resumed_steps") or {}).get(step):
        return None
    recorded = (state.get("artifacts") or {}).get(step)
    if not isinstance(recorded, dict):
        return None
    return recorded if persistence.artifacts_present(recorded) else None


def make_step_node(step: str) -> NodeFn:
    """Wrap one deterministic skill as a graph node."""

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)

        reused = _skip_record(state, step)
        if reused is not None:
            record = {"step": step, "status": "skipped", "warnings": [], "errors": []}
            audit.append("step_skipped", step=step, run_id=state.get("run_id"),
                         reason="already completed in this run directory")
            return {
                "current_step": step,
                "step_results": [record],
                # Consume the flag: the next visit to this node is a rerun.
                "resumed_steps": {step: False},
            }

        audit.append("step_start", step=step, run_id=state.get("run_id"))

        result = call_skill(step, build_payload(state, step))
        # Anything a step computed with numpy comes back wrapping a numpy
        # scalar, which state cannot be checkpointed with. Coerced once here
        # rather than trusted to 23 skills and every future one.
        output = persistence.plain_python(result["output"])
        record = {
            "step": step,
            "status": result["status"],
            "warnings": result["warnings"],
            "errors": result["errors"],
        }
        audit.append("step_end", **record, output_keys=sorted(output))
        # Recorded beside the artifacts so a later run can tell this step is
        # done and read what it produced; state itself is never persisted.
        persistence.write_step_output(_run_dir(state), step, output)

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


#: How much of a step's output the judge is shown, where the full output is far
#: larger than a verdict needs. Named per step and per field on purpose: this is
#: a projection of known structure, not a size limit. A byte cap would cut
#: wherever the budget ran out, which could be the evidence the verdict turns
#: on, and the judge would never know something was missing.
#:
#: `find_markers` reports 25 markers for each of 15 clusters, ~47 KB, when a
#: handful per cluster is already enough to say whether the ranking looks
#: sensible. Only the judge's view narrows — the step's own output and
#: markers.csv keep every gene.
JUDGE_OUTPUT_PREVIEWS: dict[str, dict[str, int]] = {
    "find_markers": {"top_markers": 5},
}


def _judge_view(step: str, output: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The step's output as the judge should see it, and what was shortened.

    Returns the note as well as the payload, because a judge shown a subset
    without being told is a judge that can conclude something is absent when it
    was only elided.
    """
    limits = JUDGE_OUTPUT_PREVIEWS.get(step)
    if not limits:
        return output, []

    view = dict(output)
    notes: list[str] = []
    for field, keep in limits.items():
        value = view.get(field)
        if isinstance(value, dict) and value:
            first = next(iter(value.values()))
            if isinstance(first, list) and len(first) > keep:
                view[field] = {k: v[:keep] for k, v in value.items()}
                notes.append(f"{field}: showing the top {keep} of {len(first)} per group")
        elif isinstance(value, list) and len(value) > keep:
            view[field] = value[:keep]
            notes.append(f"{field}: showing the first {keep} of {len(value)}")
    return view, notes


def make_judge_node(
    step: str, judge_tool: str, client: JudgeClient, session_id: str | None = None
) -> NodeFn:
    """Score the most recent result of `step` and append a verdict.

    `session_id` names the entry in the run metadata's `judge_sessions` that
    describes this judge — the model, the prompt hashes, the temperature. It
    travels onto every verdict so the two can be joined by lookup instead of by
    comparing timestamps, which stops being unambiguous the moment a run is
    resumed twice in the same second.
    """

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)
        record = next(
            (r for r in reversed(state.get("step_results") or []) if r["step"] == step),
            {"step": step, "status": "error", "warnings": [], "errors": ["step never ran"]},
        )
        view, shortened = _judge_view(step, step_output(state, step))
        payload = {
            "step": step,
            "status": record["status"],
            "warnings": record["warnings"],
            "errors": record["errors"],
            "output": view,
            "metrics": (state.get("metrics") or {}).get(step, {}),
        }
        if shortened:
            payload["output_is_abridged"] = shortened

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

        # Which model said this, on the event that records what it said.
        # `judge_sessions` in the run metadata gives the configuration per
        # execution; this makes the per-verdict answer a lookup rather than a
        # join against timestamps. `None` is the stub, and says so.
        model = getattr(client, "model_for", lambda _step: None)(step)
        audit.append(
            "judge",
            judge_tool=judge_tool,
            model=model,
            judge_session_id=session_id,
            **verdict.model_dump(),
        )
        return {"judge_results": [verdict.model_dump()]}

    return node


def make_gate_question_node(
    *,
    node_name: str = "human_gate",
    review_skill: str | None = None,
    revise_target: str | None = None,
) -> NodeFn:
    """Assemble the question a gate is about to ask, and leave it in state.

    Asking and answering are separate nodes because `interrupt()` raises out of
    the node it is called in. That node never reaches a `return`, so a delta it
    computed before interrupting does not exist — a graph node's only write
    channel is its return value. `pending_review` was declared for exactly this
    and could therefore only ever be set back to None, and a run suspended at a
    gate reported `status="running"` with nothing pending. Splitting the gate in
    two puts the question through a superstep that completes, so it is written
    and checkpointed *before* the graph suspends.

    What that buys: a paused run says what it is waiting for in its own state,
    rather than only through the `__interrupt__` key that one caller happened to
    unpack. Anything reading the state — the summary, a future API, a test that
    invokes the graph directly — sees the same fact.

    `review_skill` names a skill that assembles the question. The escalation
    gate does not need one — it is asking about a single step, and the last
    verdict is the whole story. The mainline gate does: it is asking about the
    run, and the last verdict describes only the step that happened to be last.

    `revise_target` names where answering `revise` actually lands, when that is
    not the step being judged. The mainline gate is the case: it has just judged
    `cross_check_annotation` but re-enters the mainline at `annotate_cells`, so
    the question it asks and the work it would redo are about different steps.
    The gate that owns the routing is the one that has to say so — the node
    answering it cannot work it out from a verdict.
    """

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)
        verdict = last_judge(state) or {}
        step = verdict.get("step") or state.get("current_step") or ""
        # What `revise` would redo, and therefore what it may be given. An
        # escalation gate re-runs one step and offers exactly that step's
        # parameters; the mainline gate re-runs a tail of the pipeline and
        # offers every parameter in it, because it is asking about all of it.
        target = revise_target or step
        revisable = (
            revisable_from(target) if revise_target
            else (REGISTRY[step].revisable if step in REGISTRY else ())
        )
        request = {
            "gate": node_name,
            "step": step,
            # Named separately from `step` because they differ at the mainline
            # gate, and a person answering needs to know which one they are
            # changing.
            "revise_target": target,
            "revisable": list(revisable),
            "verdict": verdict.get("verdict"),
            "score": verdict.get("score"),
            "reasons": verdict.get("reasons", []),
            "suggested_action": verdict.get("suggested_action"),
            # What the judge would set, if it named anything. It rides on the
            # verdict rather than arriving separately, so it reaches the person
            # at the moment they are being asked to decide.
            "advice": verdict.get("advice") or [],
            # The numbers the decision is actually about. Without them a gate
            # asks a person to choose while showing them only the complaint.
            "evidence": (step_output(state, step) or {}).get("evidence") or {},
        }
        if review_skill:
            outcome = call_skill(review_skill, build_payload(state, review_skill))
            if outcome["status"] == "ok":
                # The run-level picture replaces the last step's evidence, which
                # at this gate is a detail about whichever step ran last.
                request.pop("evidence", None)
                request["review"] = outcome["output"]
            else:
                request["review_error"] = outcome["errors"] or [outcome["status"]]
        audit.append("human_gate_open", **request)
        return {"pending_review": request, "status": "needs_review"}

    return node


def make_human_gate_node(policy: GatePolicy, *, node_name: str = "human_gate") -> NodeFn:
    """Put the pending question to a person and record `accept | revise | stop`.

    Interactive runs block on LangGraph's `interrupt`; headless runs apply the
    policy default so a warn/fail is never silently waved through.

    The question comes from `pending_review`, written by the node before this
    one, rather than being rebuilt here. Rebuilding it would mean the person
    could be shown one thing and the state record another, and it is the state
    record a report is later written from.

    ## What `revise` does

    `revise` may carry `overrides`: values for the parameters the question
    offered. They are checked against that offer, written into `config`, and
    every step from the revise target onward has its resume flag cleared so it
    is recomputed rather than reused. Without them `revise` re-runs a
    deterministic step against an unchanged config, which produces the same
    result and the same question — the loop this exists to break.
    """

    def node(state: WorkflowState) -> dict[str, Any]:
        audit = _audit(state)
        request = state.get("pending_review") or {}
        step = request.get("step") or state.get("current_step") or ""
        target = request.get("revise_target") or step

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

        overrides: dict[str, Any] = {}
        rejected: list[str] = []
        if choice == "revise":
            overrides, rejected = coerce_overrides(
                decision.get("overrides"), request.get("revisable") or ()
            )
            # A revise that has already been answered this many times is not a
            # person converging on a threshold, it is a loop. Stopping is the
            # safe direction and it is recorded as a decision, not as an error.
            seen = sum(
                1 for past in state.get("human_decisions") or []
                if past.get("decision") == "revise" and past.get("revise_target") == target
            )
            if seen >= policy.max_revisions_per_step:
                rejected.append(
                    f"{target} has already been revised {seen} times "
                    f"(max_revisions_per_step={policy.max_revisions_per_step}); stopping"
                )
                choice = "stop"
                overrides = {}

        entry = {
            "gate": request.get("gate") or node_name,
            "step": step,
            "revise_target": target,
            "decision": choice,
            "rationale": decision.get("rationale", ""),
            # What actually changed, and what was asked for and refused. Both
            # travel with the decision: a person who mistyped a parameter needs
            # to see that it did not take effect, and a reader of the run needs
            # to see which numbers stopped being the ones on the command line.
            "overrides": overrides,
            "rejected_overrides": rejected,
            # Who decided, and when. The audit log timestamps its own events,
            # but the decision travelling in state carried neither.
            "operator": decision.get("operator") or ("interactive" if policy.interactive
                                                     else "policy default"),
            "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        audit.append("human_gate_close", **entry)

        # The question has been answered, so it is no longer pending, and the
        # run is no longer waiting on anybody.
        delta: dict[str, Any] = {
            "human_decisions": [entry],
            "pending_review": None,
            "status": "running",
        }
        if overrides:
            # `config` reduces, so this adds keys rather than replacing the run's
            # settings, and every step downstream reads the new value the next
            # time it builds a payload.
            delta["config"] = overrides
            # Clearing the flags is what makes a *resumed* run recompute instead
            # of handing back what it already has. In a run that never resumed
            # they are all unset already and this changes nothing, which is why
            # the bug only ever appeared after `--resume-from`.
            delta["resumed_steps"] = {
                name: False for name in steps_invalidated_by(target)
            }
            # And the run's recorded config hash has to move with it, or a later
            # resume given the *original* command line would match and reuse
            # artifacts these values replaced.
            record_revision(
                state.get("run_metadata_path") or "",
                step=target,
                overrides=overrides,
                config={**(state.get("config") or {}), **overrides},
            )
        if choice == "stop":
            delta["halted"] = True
            delta["halt_reason"] = f"human stopped the run at {step or node_name}"
            delta["status"] = "halted"
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
