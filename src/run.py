"""CLI entry point: `python -m src.run --help`."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any, Callable

from langgraph.types import Command

from . import persistence
from .graph import build_graph
from .judge import get_judge
from .policy import GatePolicy
from .provenance import config_digest
from .state import new_run_state, summarize

#: One superstep per node; the mainline plus judges is well over LangGraph's default of 25.
DEFAULT_RECURSION_LIMIT = 150


def run_workflow(
    *,
    project: str = "demo",
    config: dict[str, Any] | None = None,
    input_bundle: dict[str, Any] | None = None,
    policy: GatePolicy | None = None,
    judge_backend: str | None = None,
    runs_dir: str = "runs",
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    checkpointer: Any = None,
    decide: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    """Run one pass of the workflow and return the final state.

    The three new arguments all default to what the workflow did before them,
    so a caller that passes none of them gets the previous behaviour exactly.

    `checkpointer` is what lets a gate pause; `decide` is called with the
    pending question and returns the answer, driving the `Command(resume=...)`
    loop. `resume_run_id` reuses a run directory and skips the steps it already
    holds finished artifacts for.
    """
    resolved = dict(config or {})
    graph = build_graph(
        policy=policy or GatePolicy(), judge=get_judge(judge_backend), checkpointer=checkpointer
    )
    state = new_run_state(
        project=project, config=resolved, input_bundle=input_bundle,
        runs_dir=runs_dir, run_id=resume_run_id,
    )

    if resume_run_id:
        run_dir = Path(runs_dir) / resume_run_id
        done = persistence.resumable_steps(run_dir, config_digest(resolved))
        state["artifacts"] = dict(done)
        state["resumed_steps"] = {step: True for step in done}

    invoke_config = persistence.thread_config(
        state["run_id"], recursion_limit=recursion_limit, checkpointer=checkpointer
    )
    final = graph.invoke(state, config=invoke_config)

    # A paused graph returns with the question in `__interrupt__` rather than
    # raising. The gate has already written that question into `pending_review`
    # and set `status` to `needs_review`, so a run that stopped to ask something
    # says so in its own state — this loop only has to answer it, not describe
    # it. `__interrupt__` stays the signal to *resume*, which is the one thing
    # it can say that state cannot.
    while "__interrupt__" in final:
        if decide is None:
            return final
        request = getattr(final["__interrupt__"][0], "value", {}) or {}
        final = graph.invoke(Command(resume=decide(request)), config=invoke_config)

    # Reaching here means the graph ran to an end node. Only this caller knows
    # that; a node cannot tell whether it is the last one.
    if not final.get("halted"):
        final = {**final, "status": "failed" if final.get("errors") else "completed"}
    return final


def ask_for_overrides(request: dict[str, Any]) -> dict[str, Any]:
    """Collect the parameters a `revise` is going to change, one prompt each.

    Only the names the gate offered are asked for, and a blank answer keeps the
    current value — so `revise` with no change is still available, and still
    means "run it again", for the case where that is genuinely what is wanted.

    Nothing is validated here. `coerce_overrides` in the gate node is the one
    place that decides what a value means, so that the terminal and any other
    front end cannot drift into accepting different things.
    """
    offered = request.get("revisable") or []
    if not offered:
        return {}
    target = request.get("revise_target") or request.get("step")
    print(f"   revising {target} — blank keeps the current value", file=sys.stderr)
    typed: dict[str, Any] = {}
    for name in offered:
        value = input(f"   {name} = ").strip()
        if value:
            typed[name] = value
    return typed


def ask_on_terminal(request: dict[str, Any]) -> dict[str, Any]:
    """Put a paused gate to whoever is at the keyboard.

    The evidence is shown, not just the complaint: the point of stopping is
    that a person looks at the numbers, and a gate that prints only "warn" has
    asked them to decide with nothing to decide on.
    """
    print(f"\n── {request.get('gate')} · {request.get('step')} "
          f"[{request.get('verdict')}] ──", file=sys.stderr)
    for reason in request.get("reasons") or []:
        print(f"   · {reason}", file=sys.stderr)
    if request.get("suggested_action"):
        print(f"   suggested: {request['suggested_action']}", file=sys.stderr)
    for entry in request.get("advice") or []:
        print(f"   suggests {entry.get('parameter')} = {entry.get('suggested_value')!r} "
              f"[{entry.get('confidence')}] — {entry.get('rationale', '')}"[:400],
              file=sys.stderr)
    if request.get("evidence"):
        print("   evidence: "
              + json.dumps(request["evidence"], ensure_ascii=False, default=str)[:600],
              file=sys.stderr)

    if request.get("revisable"):
        print(f"   revise can set: {', '.join(request['revisable'])}", file=sys.stderr)

    while True:
        answer = input("   accept / revise / stop > ").strip().lower()
        if answer in {"accept", "revise", "stop"}:
            overrides = ask_for_overrides(request) if answer == "revise" else {}
            rationale = input("   why (optional) > ").strip()
            return {
                "decision": answer,
                "rationale": rationale,
                "operator": getpass.getuser(),
                "overrides": overrides,
            }
        print("   please answer accept, revise or stop", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scRNA-seq agentic workflow.")
    parser.add_argument("--project", default="demo")
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        metavar="PATH",
        help="bundle directories or files; ingest_validate detects the route from these",
    )
    parser.add_argument(
        "--species",
        default="human",
        help="human, mouse, 小鼠, ... — resolves the reference and the QC constants",
    )
    parser.add_argument(
        "--reference",
        metavar="PATH",
        help="explicit transcriptome path; wins over --species",
    )
    cells = parser.add_mutually_exclusive_group()
    cells.add_argument(
        "--force-cells",
        type=int,
        metavar="N",
        help="keep the top N barcodes by UMI instead of Cell Ranger's cell call",
    )
    cells.add_argument(
        "--min-umi",
        type=int,
        metavar="X",
        help="keep barcodes with at least X UMI instead of Cell Ranger's cell call",
    )
    # Cell QC thresholds. No defaults on purpose: published "standard" values
    # come from specific tissues and protocols, and applying one silently to a
    # different one is how good cells get thrown away unnoticed. Omit them and
    # apply_cell_qc_filter reports what each candidate would cost, then stops.
    qc = parser.add_argument_group("cell QC thresholds (omit to see the evidence first)")
    qc.add_argument("--min-genes", type=float, metavar="N",
                    help="drop cells with fewer than N genes detected")
    qc.add_argument("--min-counts", type=float, metavar="N",
                    help="drop cells with fewer than N UMIs")
    qc.add_argument("--max-pct-mito", type=float, metavar="PCT",
                    help="drop cells above PCT%% mitochondrial reads")
    qc.add_argument("--max-pct-erythroid", type=float, metavar="PCT",
                    help="drop cells above PCT%% haemoglobin reads")

    # Doublets. The rate is derived per library from 10x's loading table, so the
    # override exists for a known-unusual loading rather than as a routine knob.
    # Removal stays opt-in: the call is a probability, and the annotated object
    # is a complete result on its own.
    dbl = parser.add_argument_group("doublets")
    dbl.add_argument("--expected-doublet-rate", type=float, metavar="RATE",
                     help="override the rate derived from the recovered cell count")
    dbl.add_argument("--doublet-threshold", type=float, metavar="SCORE",
                     help="override Scrublet's automatic score threshold")
    dbl.add_argument("--remove-doublets", action="store_true",
                     help="drop called doublets instead of only annotating them")

    # Downstream analysis knobs. Each has a documented default except the
    # CellTypist model, which is deliberately unset: a model trained on the
    # wrong tissue returns confident wrong labels rather than failing, so
    # annotate_cells reports the candidates and stops instead of guessing.
    ana = parser.add_argument_group("clustering, embedding and annotation")
    ana.add_argument("--resolution", type=float, metavar="R",
                     help="Leiden resolution (default 1.0)")
    ana.add_argument("--embedding-method", choices=["umap", "tsne", "both"], dest="method",
                     help="which 2D embedding(s) to compute (default umap)")
    ana.add_argument("--celltypist-model", metavar="NAME",
                     help="e.g. Immune_All_Low.pkl; omit to list the candidates and stop")
    # Scored against every tissue rather than one, 14 of 15 PBMC clusters change
    # their top hit, so this is left unset rather than defaulted, exactly as the
    # CellTypist model is.
    ana.add_argument("--scmayomap-tissue", metavar="TISSUE",
                     help="tissue for the marker-database cross-check, e.g. blood; "
                          "omit to list the candidates and stop")
    # One seed for every stochastic step, recorded in run_metadata.json so a
    # report can state it rather than leaving it implicit.
    ana.add_argument("--random-state", type=int, metavar="N",
                     help="seed for PCA, Harmony, Leiden, UMAP, t-SNE and Scrublet (default 0)")

    parser.add_argument("--sample-qc-triage", action="store_true")
    parser.add_argument("--judge", choices=["stub", "local"], default="stub")
    parser.add_argument(
        "--allow-warn", action="store_true", help="let `warn` continue instead of stopping"
    )
    parser.add_argument(
        "--headless-decision",
        choices=["stop", "accept"],
        default="stop",
        help="what a non-interactive run assumes at a human gate",
    )
    # Pausing and resuming. Both default off, so a command that names neither
    # runs exactly as it did before they existed.
    resume = parser.add_argument_group("pausing and resuming")
    resume.add_argument(
        "--interactive", action="store_true",
        help="stop at each gate and ask on the terminal, instead of applying "
             "--headless-decision",
    )
    resume.add_argument(
        "--resume-from", metavar="RUN_ID",
        help="reuse an existing runs/<RUN_ID> directory and skip the steps whose "
             "artifacts are still there; refuses if the config has changed",
    )

    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args(argv)

    # A flag the operator never typed must not reach a skill as an explicit
    # `None`: several read their defaults with `config.get(key, DEFAULT)`, which
    # returns the None rather than the default and then fails converting it.
    # Absent means absent.
    config = {
        key: value
        for key, value in {
            "species": args.species,
            "transcriptome": args.reference,
            "force_cells": args.force_cells,
            "min_umi": args.min_umi,
            "min_genes": args.min_genes,
            "min_counts": args.min_counts,
            "max_pct_mito": args.max_pct_mito,
            "max_pct_erythroid": args.max_pct_erythroid,
            "expected_doublet_rate": args.expected_doublet_rate,
            "doublet_threshold": args.doublet_threshold,
            "remove_doublets": args.remove_doublets,
            "resolution": args.resolution,
            "method": args.method,
            "celltypist_model": args.celltypist_model,
            "scmayomap_tissue": args.scmayomap_tissue,
            "random_state": args.random_state,
            "sample_qc_triage": args.sample_qc_triage,
        }.items()
        if value is not None
    }

    final = run_workflow(
        project=args.project,
        input_bundle={"paths": args.input},
        config=config,
        checkpointer=persistence.make_checkpointer("memory" if args.interactive else "none"),
        decide=ask_on_terminal if args.interactive else None,
        resume_run_id=args.resume_from,
        policy=GatePolicy(
            autocontinue_on_warn=args.allow_warn,
            headless_decision=args.headless_decision,
            interactive=args.interactive,
        ),
        judge_backend=args.judge,
        runs_dir=args.runs_dir,
    )

    print(json.dumps(summarize(final), indent=2, ensure_ascii=False))
    return 1 if final.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
