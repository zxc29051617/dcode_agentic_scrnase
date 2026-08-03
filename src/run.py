"""CLI entry point: `python -m src.run --help`."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .graph import build_graph
from .judge import get_judge
from .policy import GatePolicy
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
) -> dict[str, Any]:
    """Run one pass of the workflow and return the final state."""
    graph = build_graph(policy=policy or GatePolicy(), judge=get_judge(judge_backend))
    state = new_run_state(
        project=project, config=config or {}, input_bundle=input_bundle, runs_dir=runs_dir
    )
    return graph.invoke(state, config={"recursion_limit": recursion_limit})


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
    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args(argv)

    final = run_workflow(
        project=args.project,
        input_bundle={"paths": args.input},
        config={
            "species": args.species,
            "transcriptome": args.reference,
            "force_cells": args.force_cells,
            "min_umi": args.min_umi,
            "sample_qc_triage": args.sample_qc_triage,
        },
        policy=GatePolicy(
            autocontinue_on_warn=args.allow_warn,
            headless_decision=args.headless_decision,
        ),
        judge_backend=args.judge,
        runs_dir=args.runs_dir,
    )

    print(json.dumps(summarize(final), indent=2, ensure_ascii=False))
    return 1 if final.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
