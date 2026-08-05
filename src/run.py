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
            "random_state": args.random_state,
            "sample_qc_triage": args.sample_qc_triage,
        }.items()
        if value is not None
    }

    final = run_workflow(
        project=args.project,
        input_bundle={"paths": args.input},
        config=config,
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
