"""LangGraph assembly for the MVP graph in `workflows/fastq_count_main_graph.md`.

Two entry routes (FASTQ, count matrix), the count matrix split into raw and
filtered, a judge after every meaningful step, and an explicit human gate on
anything the policy will not wave through.
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .judge import JudgeClient, get_judge
from .nodes import (
    assert_registry_covered,
    make_branch_router,
    make_gate_router,
    make_human_gate_node,
    make_judge_node,
    make_step_node,
)
from .policy import DEFAULT_POLICY, GatePolicy
from .registry import MAINLINE, REGISTRY
from .state import WorkflowState, step_output

HUMAN_GATE = "human_gate"
FINAL_GATE = "human_review_decision"


# --------------------------------------------------------------------------
# Branch decisions
#
# Each reads the upstream step's own output first and falls back to config, so
# the routing can be exercised before the skills are implemented.
# --------------------------------------------------------------------------


def branch_input_type(state: WorkflowState) -> str:
    out = step_output(state, "ingest_validate")
    kind = out.get("input_type") or (state.get("config") or {}).get("input_type")
    return "fastq" if kind == "fastq" else "matrix"


def branch_after_reference(state: WorkflowState) -> str:
    """Optional sample-level triage runs before the input-type split."""
    ran = any(r["step"] == "sample_qc_triage" for r in state.get("step_results") or [])
    if (state.get("config") or {}).get("sample_qc_triage") and not ran:
        return "sample_qc"
    return branch_input_type(state)


def branch_matrix_kind(state: WorkflowState) -> str:
    out = step_output(state, "count_matrix_classify")
    kind = out.get("matrix_class") or (state.get("config") or {}).get("matrix_kind") or "filtered"
    if kind == "raw":
        return "raw"
    if kind == "filtered":
        return "filtered"
    return HUMAN_GATE  # `unknown` is a decision for a person, not a default


def branch_cell_calling(state: WorkflowState) -> str:
    out = step_output(state, "load_raw_counts")
    resolved = out.get("cell_calling_resolved")
    if resolved is None:
        resolved = (state.get("config") or {}).get("cell_calling_resolved", False)
    return "mainline" if resolved else "review"


def build_graph(
    *,
    policy: GatePolicy = DEFAULT_POLICY,
    judge: JudgeClient | None = None,
    checkpointer: Any = None,
):
    """Wire and compile the workflow graph."""
    client = judge or get_judge()
    graph = StateGraph(WorkflowState)

    # Resolver per gated step: where an `accept` at the human gate should land.
    successors: dict[str, Callable[[WorkflowState], str]] = {}
    added: set[str] = set()

    def add_step(name: str) -> str:
        graph.add_node(name, make_step_node(name))
        added.add(name)
        return name

    def add_judge(step: str) -> str:
        tool = REGISTRY[step].judge
        assert tool is not None, f"{step} has no judge in the registry"
        graph.add_node(tool, make_judge_node(step, tool, client))
        graph.add_edge(step, tool)
        return tool

    def linear(step: str, target: str) -> None:
        """step -> judge -> (target | human gate)."""
        add_step(step)
        tool = add_judge(step)
        graph.add_conditional_edges(
            tool,
            make_gate_router(policy),
            {"continue": target, "human_gate": HUMAN_GATE},
        )
        successors[step] = lambda _state, t=target: t

    def branching(step: str, branch: Callable[[WorkflowState], str], path_map: dict[str, str]) -> None:
        """step -> judge -> (branch decision | human gate)."""
        add_step(step)
        tool = add_judge(step)
        graph.add_conditional_edges(
            tool,
            make_branch_router(policy, branch),
            {**path_map, "human_gate": HUMAN_GATE},
        )
        # The gate resumes at a node, so map the branch key back through path_map.
        successors[step] = lambda state, b=branch, pm=path_map: pm.get(b(state), END)

    # ---- intake and routing ------------------------------------------------
    # `resolve_reference` sits between intake and the route split so both the
    # FASTQ and the count-matrix route pass through it: the transcriptome is only
    # needed for counting, but the species constants are needed by both.
    linear("ingest_validate", "resolve_reference")
    branching(
        "resolve_reference",
        branch_after_reference,
        {"sample_qc": "sample_qc_triage", "fastq": "fastq_preflight", "matrix": "count_matrix_classify"},
    )
    branching(
        "sample_qc_triage",
        branch_input_type,
        {"fastq": "fastq_preflight", "matrix": "count_matrix_classify"},
    )

    # ---- FASTQ upstream route ----------------------------------------------
    linear("fastq_preflight", "fastq_qc")
    linear("fastq_qc", "cellranger_count")
    linear("cellranger_count", "count_matrix_classify")

    # ---- count matrix split -------------------------------------------------
    branching(
        "count_matrix_classify",
        branch_matrix_kind,
        {"raw": "load_raw_counts", "filtered": "load_filtered_counts"},
    )
    branching(
        "load_raw_counts",
        branch_cell_calling,
        {"review": "cell_calling_review", "mainline": MAINLINE[0]},
    )
    linear("cell_calling_review", MAINLINE[0])
    linear("load_filtered_counts", MAINLINE[0])

    # ---- Scanpy mainline ----------------------------------------------------
    for current, following in zip(MAINLINE, MAINLINE[1:] + (FINAL_GATE,)):
        linear(current, following)

    # ---- gates and report ---------------------------------------------------
    # `human_review_decision` is the mainline gate (H2); `human_gate` is the
    # warn/fail escalation (H1). Both turn a person's call into accept/revise/stop.
    graph.add_node(FINAL_GATE, make_human_gate_node(policy, node_name=FINAL_GATE))
    graph.add_conditional_edges(
        FINAL_GATE,
        _final_gate_router,
        {"build_report": "build_report", "annotate_cells": "annotate_cells", "end": END},
    )

    add_step("build_report")
    graph.add_edge(add_judge("build_report"), END)
    successors["build_report"] = lambda _state: END

    # ---- escalation gate ----------------------------------------------------
    graph.add_node(HUMAN_GATE, make_human_gate_node(policy, node_name=HUMAN_GATE))
    graph.add_conditional_edges(
        HUMAN_GATE,
        _make_escalation_router(successors),
        {**{name: name for name in added}, "end": END},
    )

    graph.add_edge(START, "ingest_validate")

    assert_registry_covered(added | {FINAL_GATE})
    return graph.compile(checkpointer=checkpointer)


def _final_gate_router(state: WorkflowState) -> str:
    decisions = state.get("human_decisions") or []
    choice = decisions[-1]["decision"] if decisions else "stop"
    return {"accept": "build_report", "revise": "annotate_cells"}.get(choice, "end")


def _make_escalation_router(
    successors: dict[str, Callable[[WorkflowState], str]],
) -> Callable[[WorkflowState], str]:
    """Leave the escalation gate: accept resumes the mainline, revise reruns the step."""

    def router(state: WorkflowState) -> str:
        decisions = state.get("human_decisions") or []
        if not decisions:
            return "end"
        last = decisions[-1]
        step = last.get("step") or state.get("current_step") or ""
        if last["decision"] == "revise" and step:
            return step
        if last["decision"] == "accept":
            resolve = successors.get(step)
            if resolve is None:
                return "end"
            target = resolve(state)
            return "end" if target in {END, HUMAN_GATE} else target
        return "end"

    return router
