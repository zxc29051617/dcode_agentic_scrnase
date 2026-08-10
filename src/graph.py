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
    make_gate_question_node,
    make_gate_router,
    make_human_gate_node,
    make_judge_node,
    make_step_node,
)
from .policy import DEFAULT_POLICY, GatePolicy
from .registry import MAINLINE, REGISTRY
from .state import WorkflowState, step_output

#: Each gate is two nodes: one writes the question into state, the next puts it
#: to a person. `interrupt()` raises out of its own node, so a single node can
#: never both ask and record the asking — see `make_gate_question_node`. Every
#: edge into a gate still names the question node, so the split is invisible to
#: everything upstream of it.
HUMAN_GATE = "human_gate"
HUMAN_GATE_ANSWER = "human_gate_answer"
FINAL_GATE = "human_review_decision"
FINAL_GATE_ANSWER = "human_review_decision_answer"


# --------------------------------------------------------------------------
# Branch decisions
#
# Each reads the decision the upstream step actually made. No config fallbacks
# remain on this path: every step that feeds a branch is implemented.
# --------------------------------------------------------------------------


def branch_input_type(state: WorkflowState) -> str:
    out = step_output(state, "ingest_validate")
    kind = out.get("input_type") or (state.get("config") or {}).get("input_type")
    return "fastq" if kind == "fastq" else "matrix"


def branch_after_ingest(state: WorkflowState) -> str:
    """Optional sample-level triage runs before the input-type split."""
    ran = any(r["step"] == "sample_qc_triage" for r in state.get("step_results") or [])
    if (state.get("config") or {}).get("sample_qc_triage") and not ran:
        return "sample_qc"
    return branch_input_type(state)


def branch_matrix_kind(state: WorkflowState) -> str:
    out = step_output(state, "count_matrix_classify")
    kind = out.get("matrix_class")
    if kind == "raw":
        return "raw"
    if kind == "filtered":
        return "filtered"
    return HUMAN_GATE  # `unknown` is a decision for a person, not a default


def branch_cell_calling(state: WorkflowState) -> str:
    """A raw matrix always goes to review — nothing has called cells on it yet."""
    out = step_output(state, "load_raw_counts")
    return "mainline" if out.get("cell_calling_resolved") else "review"


def branch_merge(state: WorkflowState) -> str:
    """Both routes converge on `standardize_count_data`."""
    return "standardize"


def branch_after_qc_filter(state: WorkflowState) -> str:
    """Unfiltered cells cannot enter the rest of the mainline, even on `accept`.

    QC thresholds are the operator's call, and cutting is destructive — a cell
    removed here is gone from every plot, marker test and cluster downstream.
    Until thresholds are given there is no filtered object to hand on.
    """
    out = step_output(state, "apply_cell_qc_filter")
    return "mainline" if out.get("filter_state") == "applied" else HUMAN_GATE


def branch_after_cell_calling(state: WorkflowState) -> str:
    """An unresolved cell count cannot enter the mainline, even on `accept`.

    How many cells to keep is the operator's call. Until one is made there is no
    subset matrix to hand downstream, so this routes to the gate rather than
    letting the mainline run on every barcode in the raw matrix.
    """
    out = step_output(state, "cell_calling_review")
    return "standardize" if out.get("cell_calling_state") == "resolved" else HUMAN_GATE


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
    # Each route gets its own entry check rather than sharing one: the FASTQ side
    # must resolve a 32 GB transcriptome, the matrix side must read the matrix.
    # Both answer "what species is this?" — with different evidence.
    branching(
        "ingest_validate",
        branch_after_ingest,
        {
            "sample_qc": "sample_qc_triage",
            "fastq": "resolve_reference",
            "matrix": "matrix_preflight",
        },
    )
    branching(
        "sample_qc_triage",
        branch_input_type,
        {"fastq": "resolve_reference", "matrix": "matrix_preflight"},
    )

    # ---- FASTQ upstream route ----------------------------------------------
    linear("resolve_reference", "fastq_preflight")
    linear("fastq_preflight", "fastq_qc")
    linear("fastq_qc", "cellranger_count")
    linear("cellranger_count", "count_matrix_classify")

    # ---- matrix entry route -------------------------------------------------
    linear("matrix_preflight", "count_matrix_classify")

    # ---- count matrix split -------------------------------------------------
    branching(
        "count_matrix_classify",
        branch_matrix_kind,
        {"raw": "load_raw_counts", "filtered": "load_filtered_counts"},
    )
    branching(
        "load_raw_counts",
        branch_cell_calling,
        {"review": "cell_calling_review", "mainline": "merge_samples"},
    )
    branching(
        "cell_calling_review",
        branch_after_cell_calling,
        {"standardize": "merge_samples"},
    )
    linear("load_filtered_counts", "merge_samples")

    # ---- per-sample work ends, one object begins ----------------------------
    # Every step above runs once per library. `merge_samples` concatenates them
    # with a `sample` label; `post_load_validate` then promises the mainline one
    # shape whichever route and however many samples produced it.
    linear("merge_samples", "post_load_validate")
    linear("post_load_validate", MAINLINE[0])

    # ---- Scanpy mainline ----------------------------------------------------
    # `apply_cell_qc_filter` branches like `cell_calling_review` does: cutting
    # cells is destructive and the thresholds are the operator's, so an
    # unfiltered object must not reach the rest of the mainline by being
    # accepted at the gate.
    for current, following in zip(MAINLINE, MAINLINE[1:] + (FINAL_GATE,)):
        if current == "apply_cell_qc_filter":
            branching(current, branch_after_qc_filter, {"mainline": following})
        else:
            linear(current, following)

    # ---- gates and report ---------------------------------------------------
    # `human_review_decision` is the mainline gate (H2); `human_gate` is the
    # warn/fail escalation (H1). Both turn a person's call into accept/revise/stop.
    # The mainline gate asks about the whole run, so it builds its question
    # from `human_review_decision` rather than from the last step's verdict.
    graph.add_node(
        FINAL_GATE,
        make_gate_question_node(node_name=FINAL_GATE, review_skill=FINAL_GATE),
    )
    graph.add_node(FINAL_GATE_ANSWER, make_human_gate_node(policy, node_name=FINAL_GATE))
    graph.add_edge(FINAL_GATE, FINAL_GATE_ANSWER)
    graph.add_conditional_edges(
        FINAL_GATE_ANSWER,
        _final_gate_router,
        {"build_report": "build_report", "annotate_cells": "annotate_cells", "end": END},
    )

    add_step("build_report")
    graph.add_edge(add_judge("build_report"), END)
    successors["build_report"] = lambda _state: END

    # ---- escalation gate ----------------------------------------------------
    # `added` covers every step node, but the last mainline step's successor can
    # resolve to FINAL_GATE itself (it is a node, not a step, so add_step never
    # ran for it) — omit it here and an `accept` on that step KeyErrors instead
    # of reaching the mainline gate.
    graph.add_node(HUMAN_GATE, make_gate_question_node(node_name=HUMAN_GATE))
    graph.add_node(HUMAN_GATE_ANSWER, make_human_gate_node(policy, node_name=HUMAN_GATE))
    graph.add_edge(HUMAN_GATE, HUMAN_GATE_ANSWER)
    graph.add_conditional_edges(
        HUMAN_GATE_ANSWER,
        _make_escalation_router(successors),
        {**{name: name for name in added}, FINAL_GATE: FINAL_GATE, "end": END},
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
