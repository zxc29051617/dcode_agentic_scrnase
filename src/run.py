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
from .envfile import load as load_env_file
from .judge import BACKEND_ALIASES, describe_judge, get_judge
from .policy import GatePolicy
from .provenance import AuditLog, record_judge_session
from .registry import REGISTRY
from . import manifest
from .state import new_run_state, summarize, unresolved_choices

#: One superstep per node; the mainline plus judges is well over LangGraph's default of 25.
DEFAULT_RECURSION_LIMIT = 150


def run_workflow(
    *,
    project: str = "demo",
    config: dict[str, Any] | None = None,
    input_bundle: dict[str, Any] | None = None,
    study_design: dict[str, Any] | None = None,
    policy: GatePolicy | None = None,
    judge_backend: str | None = None,
    judge_model: str | None = None,
    runs_dir: str = "runs",
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    checkpointer: Any = None,
    checkpointer_kind: str | None = None,
    decide: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    resume_run_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one pass of the workflow and return the final state.

    A caller that passes none of the optional arguments gets the behaviour the
    workflow had before any of them existed.

    `checkpointer` is what lets a gate pause; `decide` is called with the
    pending question and returns the answer, driving the `Command(resume=...)`
    loop. `resume_run_id` reuses a run directory and skips the steps it already
    holds finished artifacts for.

    `run_id` names a *fresh* run instead of minting a name for it, which is a
    different request from `resume_run_id` and must not be confused with it: no
    resume plan is drawn up, nothing is reused, and the run is new in every way
    except that its identity was decided before it started. A caller outside
    this process needs that — a worker records which scientific run a queued job
    became before handing control to the graph, so that a crash between the two
    leaves a directory some job still claims rather than an orphan and a retry
    that starts a second one. Passing both is a caller contradicting itself, and
    is refused rather than silently resolved.

    `checkpointer_kind` builds one instead of taking one, which the `sqlite`
    kind needs: its database goes in the run directory, and the run directory is
    not known until the run id is. A checkpointer built here is closed here; one
    passed in belongs to the caller and is left alone.

    ## Two resumes, and they are not the same thing

    This function's `resume_run_id` is the *artifact* resume: start the graph at
    the beginning and skip the steps whose results on disk are still valid. It
    answers "what work can be reused".

    `continue_workflow` is the *checkpoint* resume: pick up a graph that is
    suspended mid-run and answer the question it stopped on. It answers "where
    was this run when it stopped".

    They are kept separate because they can disagree and the disagreement is
    informative: a checkpoint says a step completed, the artifact check says its
    output is gone. Conflating them into one "resume" would have to pick a
    winner silently.
    """
    if resume_run_id and run_id:
        raise ValueError(
            "pass resume_run_id or run_id, not both: one reuses a finished run's "
            "artifacts, the other names a run that has not happened yet"
        )
    resolved = dict(config or {})
    state = new_run_state(
        project=project, config=resolved, input_bundle=input_bundle,
        study_design=study_design, runs_dir=runs_dir, run_id=resume_run_id or run_id,
    )
    owned = None
    if checkpointer is None and checkpointer_kind:
        owned = persistence.make_checkpointer(
            checkpointer_kind, run_dir=Path(runs_dir) / state["run_id"]
        )
        checkpointer = owned
    # Built once and both used and described, rather than described from the
    # environment: `--judge` beats `SCRNA_JUDGE_BACKEND` and a per-step entry
    # beats `SCRNA_JUDGE_MODEL`, so only the object knows what won.
    judge_client = get_judge(judge_backend, judge_model)
    judge_session = _record_judge_session(
        state["run_metadata_path"],
        judge_client,
        mode="artifact_resume" if resume_run_id else "new",
    )
    graph = build_graph(
        policy=policy or GatePolicy(), judge=judge_client, checkpointer=checkpointer,
        judge_session_id=judge_session,
    )

    if resume_run_id:
        run_dir = Path(runs_dir) / resume_run_id
        plan = persistence.plan_resume(
            run_dir, config=resolved, input_bundle=input_bundle or {}
        )
        state["artifacts"] = dict(plan.reusable)
        state["resumed_steps"] = {step: True for step in plan.reusable}
        # Which steps were kept and why is a decision worth auditing: a resume
        # that reuses eighteen steps and one that reuses none look the same from
        # outside, and only one of them describes a single analysis.
        AuditLog(state["audit_log_path"]).append(
            "resume_plan",
            run_id=state["run_id"],
            reused=sorted(plan.reusable),
            rerun_from=plan.rerun_from,
            reasons=plan.reasons,
        )

    invoke_config = persistence.thread_config(
        state["run_id"], recursion_limit=recursion_limit, checkpointer=checkpointer
    )
    try:
        final = graph.invoke(state, config=invoke_config)
        final = _answer_until_done(graph, final, invoke_config, decide)
    finally:
        persistence.close_checkpointer(owned)
    return final


#: The steps a judge is asked about. `human_review_decision` is a gate, not a
#: scored step, and is the one registry entry with no judge.
JUDGED_STEPS: tuple[str, ...] = tuple(
    name for name, spec in REGISTRY.items() if spec.judge
)


def _record_judge_session(metadata_path: str | None, client: Any, *, mode: str) -> str | None:
    """Append what is about to do the judging, and return the id verdicts cite.

    Recorded when the client is built rather than after the run, because a run
    that crashes or is stopped at a gate still had a judge, and the question
    "what scored this" has to be answerable for a run that did not finish.
    """
    if not metadata_path:
        return None
    return record_judge_session(
        metadata_path, mode=mode, session=describe_judge(client, JUDGED_STEPS)
    )


def _answer_until_done(
    graph: Any,
    final: dict[str, Any],
    invoke_config: dict[str, Any],
    decide: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Answer whatever the graph stopped to ask, until it stops asking.

    A paused graph returns with the question in `__interrupt__` rather than
    raising. The gate has already written that question into `pending_review`
    and set `status` to `needs_review`, so a run that stopped to ask something
    says so in its own state — this loop only has to answer it, not describe it.
    `__interrupt__` stays the signal to *resume*, which is the one thing it can
    say that state cannot.
    """
    while "__interrupt__" in final:
        if decide is None:
            # Nobody is available to answer. The run stays suspended, and with a
            # durable checkpointer it can be picked up later by
            # `continue_workflow` from another process.
            return final
        request = getattr(final["__interrupt__"][0], "value", {}) or {}
        try:
            answer = decide(request)
        except EOFError:
            # There is no one at the terminal after all — the session was closed,
            # or stdin was never a terminal. That is the same situation as having
            # no way to ask, not a failure of the run: the gate stays suspended
            # and a durable checkpoint keeps it answerable from another process.
            return final
        final = graph.invoke(Command(resume=answer), config=invoke_config)

    return _finish(final)


def _finish(final: dict[str, Any]) -> dict[str, Any]:
    """Say how the run ended. Only this caller knows that it did.

    ## A run that produced no report did not complete

    `completed` used to mean "the graph reached an end node without halting",
    which is a fact about the graph rather than about the analysis. It was true
    of a run that stopped dead because nobody chose the QC thresholds: the gate
    offered `accept`, `accept` could not carry an unfiltered object into the
    mainline, so the route ended — with no clustering, no markers, no report,
    and `status: completed`.

    So completion is defined by the artefact instead. `build_report` is the last
    node on every route that finishes; a run with no entry for it did not get
    there, whatever else it managed, and that is a halt.

    A report that ran and *failed* is a different outcome and keeps its own
    name: the run got to the end and the end is broken.
    """
    if final.get("halted"):
        return final

    by_step = {r["step"]: r["status"] for r in final.get("step_results") or []}
    if "build_report" not in by_step:
        return {**final, "halted": True, "status": "halted",
                "halt_reason": _why_no_report(final)}

    return {**final, "status": "failed" if final.get("errors") else "completed"}


def _why_no_report(final: dict[str, Any]) -> str:
    """The most specific reason available for ending without a report.

    An unresolved choice is named outright, because it is both the usual cause
    and the one a person can act on: the run is not broken, it is waiting for a
    number nobody supplied.
    """
    open_choices = unresolved_choices(final.get("artifacts"))
    if open_choices:
        waiting = "; ".join(f"{step} ({key} is {value})" for step, key, value in open_choices)
        return (f"stopped without a report: {waiting}. "
                f"Supply the value and resume, or answer `revise` at the gate")
    last = final.get("current_step") or "the first step"
    return f"stopped without a report: the run ended at {last} before build_report"


def continue_workflow(
    *,
    run_id: str,
    runs_dir: str = "runs",
    policy: GatePolicy | None = None,
    judge_backend: str | None = None,
    judge_model: str | None = None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    decide: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pick up a run suspended at a gate, in a process that did not start it.

    This is the checkpoint resume, and it is deliberately not the artifact one.
    Nothing here builds a state, reads `output.json`, or plans what to reuse:
    the graph's own checkpoint already holds where it was and everything it had,
    and `Command(resume=...)` continues from precisely there. The steps that
    already ran do not run again, so the audit log and the artifacts are not
    written twice.

    What it will not do is start over. Every way of failing to find the run
    raises `ResumeError` — a missing run directory, a missing database, a thread
    id no checkpoint matches, a run that is not actually waiting. Each of those
    could have been "call `invoke` with a fresh state and see what happens",
    which produces a second analysis under the first one's run id and writes it
    into the first one's directory.
    """
    run_dir = Path(runs_dir) / run_id
    checkpointer = persistence.open_saved_checkpointer(run_dir)
    try:
        # A continued run builds its own judge, and every step after the gate is
        # scored by it — so this process can contribute verdicts under a
        # different model than the one that produced the earlier ones, and the
        # provenance has to say so. Recorded even when the answer turns out to
        # be `stop` and nothing is scored: the entry states which judge was
        # live, and the audit log's `judge` events say what it actually scored.
        judge_client = get_judge(judge_backend, judge_model)
        judge_session = _record_judge_session(
            str(run_dir / "run_metadata.json"), judge_client, mode="checkpoint_continue"
        )
        graph = build_graph(
            policy=policy or GatePolicy(interactive=True),
            judge=judge_client,
            checkpointer=checkpointer,
            judge_session_id=judge_session,
        )
        invoke_config = persistence.thread_config(
            run_id, recursion_limit=recursion_limit, checkpointer=checkpointer
        )
        snapshot = graph.get_state(invoke_config)
        request = persistence.pending_question(
            snapshot, thread_id=run_id, run_dir=run_dir
        )

        audit = AuditLog(snapshot.values.get("audit_log_path") or (run_dir / "audit.jsonl"))
        audit.append(
            "checkpoint_resumed",
            run_id=run_id,
            thread_id=run_id,
            waiting_at=request.get("step"),
            gate=request.get("gate"),
            checkpoint=str(persistence.checkpoint_path(run_dir)),
        )

        if decide is None:
            raise persistence.ResumeError(
                f"thread {run_id!r} is waiting at the {request.get('gate')} gate on "
                f"{request.get('step')!r}, but no way to answer was given"
            )
        final = graph.invoke(Command(resume=decide(request)), config=invoke_config)
        return _answer_until_done(graph, final, invoke_config, decide)
    finally:
        persistence.close_checkpointer(checkpointer)


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


def build_parser() -> argparse.ArgumentParser:
    """The command line, as a value, so it can be inspected without running one.

    Extracted so tests can ask what the CLI accepts. Several of the things
    this module documents were wrong precisely because nothing checked the
    parser against the prose: `--judge openai-compatible` was in the guide and
    rejected by argparse, and `--judge`'s default made `SCRNA_JUDGE_BACKEND`
    unreachable.
    """
    parser = argparse.ArgumentParser(description="Run the scRNA-seq agentic workflow.")
    parser.add_argument("--project", default="demo")
    parser.add_argument(
        "--input",
        nargs="+",
        metavar="PATH",
        help="bundle directories or files; ingest_validate detects the route from these. "
             "Required unless --continue-from names a run that already has them",
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
                     help="which embedding method(s) to compute (default umap)")
    ana.add_argument(
        "--embedding-dimensions", nargs="+", type=int, choices=[2, 3], dest="dimensions",
        help="embedding dimensions to compute, 2, 3, or both (default 2)",
    )
    ana.add_argument(
        "--embedding-max-cells", type=int, metavar="N",
        help="maximum cells in each browser embedding display artifact (default is dimension-specific)",
    )
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

    design = parser.add_argument_group("study design (who each library came from)")
    design.add_argument(
        "--sample-manifest", metavar="CSV",
        help="one row per sequencing library, with library_id, sample_id, donor_id, "
             "condition and technical_batch. Required before Harmony can run: it is "
             "what tells the pipeline which differences are technical and removable",
    )
    design.add_argument(
        "--integration-mode", choices=["none", "harmony"], default=None,
        help="whether to batch-correct. Left unset, nothing is corrected and the run "
             "says so at the gate — a library is not assumed to be a technical batch. "
             "'none' records that no correction is wanted; 'harmony' corrects on the "
             "manifest's technical_batch and nothing else",
    )

    parser.add_argument("--sample-qc-triage", action="store_true")
    # `default=None`, not `"stub"`. With a default the CLI always passed an
    # explicit value, so `SCRNA_JUDGE_BACKEND` could never be reached from the
    # command line — the variable was documented, exported by people, and dead.
    # `get_judge` owns the fallback so there is one place that decides.
    judge_group = parser.add_argument_group("the judge")
    judge_group.add_argument(
        "--judge",
        choices=sorted(BACKEND_ALIASES),
        default=None,
        help="which judge to score steps with. `ollama` and `openai-compatible` are "
             "aliases for `local`. Overrides SCRNA_JUDGE_BACKEND; default stub",
    )
    judge_group.add_argument(
        "--judge-model",
        metavar="NAME",
        help="model for `--judge local`, e.g. gpt-oss:120b. Overrides "
             "SCRNA_JUDGE_MODEL. Ignored by the stub, which calls no model",
    )
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
        help="artifact resume: re-run runs/<RUN_ID> from the start, reusing every "
             "step whose recorded result is still valid. Re-runs from the first "
             "step the config or the input data invalidated",
    )
    resume.add_argument(
        "--continue-from", metavar="RUN_ID",
        help="checkpoint resume: pick up runs/<RUN_ID> where it stopped at a gate "
             "and answer the question it is waiting on. Needs a run started with "
             "--interactive, which is what writes the checkpoint database. "
             "Nothing already done is repeated",
    )

    parser.add_argument("--runs-dir", default="runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Before anything reads an environment variable. `.env` fills only what is
    # not already set, so an export still wins and so does the command line.
    # Only the names of the variables it introduced are ever printed — the
    # values are why the file is gitignored.
    env_path, env_keys = load_env_file()

    parser = build_parser()
    args = parser.parse_args(argv)

    if env_path is not None and env_keys:
        print(f"loaded {len(env_keys)} setting(s) from {env_path}: "
              f"{', '.join(sorted(env_keys))}", file=sys.stderr)

    # Continuing needs no input: the checkpoint holds everything the run had.
    if args.continue_from:
        if args.input:
            parser.error("--continue-from takes no --input; the checkpoint has it")
        if args.sample_manifest:
            # The checkpoint carries the design this run started with, and the
            # steps before the gate were already computed under it. Reading a
            # possibly-edited CSV now would leave one run describing itself two
            # ways, so the only honest way to apply an edited manifest is the
            # resume that recomputes what depended on it.
            parser.error(
                "--continue-from uses the manifest this run started with, which is "
                "kept at runs/<run_id>/manifest/normalized.csv. To apply an edited "
                "manifest, use --resume-from so the steps that read it are re-run"
            )
        return _continue_main(args)
    if not args.input:
        parser.error("--input is required (or --continue-from to pick up a paused run)")

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
            "dimensions": args.dimensions,
            "embedding_max_cells": args.embedding_max_cells,
            "celltypist_model": args.celltypist_model,
            "scmayomap_tissue": args.scmayomap_tissue,
            "random_state": args.random_state,
            "sample_qc_triage": args.sample_qc_triage,
            "integration_mode": args.integration_mode,
        }.items()
        if value is not None
    }

    # The manifest is validated here, before the graph is built, because every
    # failure it can report is one the operator has to fix in a file — there is
    # nothing to be gained from discovering it eleven steps in.
    study_design: dict[str, Any] = {}
    if args.sample_manifest:
        parsed, problems = manifest.load_manifest(args.sample_manifest)
        if problems:
            for problem in problems:
                print(f"sample manifest: {problem}", file=sys.stderr)
            return 1
        study_design = manifest.design_state(parsed)
        # The digest travels in config so a changed design invalidates the steps
        # that read it, the same way a changed threshold does. The rows do not:
        # config is written to run_metadata.json, which is meant to be shareable.
        config["manifest_sha256"] = parsed.sha256

    final = run_workflow(
        project=args.project,
        input_bundle={"paths": args.input},
        config=config,
        study_design=study_design,
        # An interactive run is one that can stop and wait, so its checkpoint
        # goes in the run directory rather than in memory: the process holding
        # it may be closed before anybody answers, and `--continue-from` picks
        # it up from there.
        checkpointer_kind="sqlite" if args.interactive else "none",
        decide=ask_on_terminal if args.interactive else None,
        resume_run_id=args.resume_from,
        policy=GatePolicy(
            autocontinue_on_warn=args.allow_warn,
            headless_decision=args.headless_decision,
            interactive=args.interactive,
        ),
        judge_backend=args.judge,
        judge_model=args.judge_model,
        runs_dir=args.runs_dir,
    )

    report = summarize(final)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] == "needs_review":
        # The run is suspended, not broken. Say how to pick it up, because the
        # command needs the run id and the run id was generated in here.
        pending = report.get("pending_review") or {}
        print(
            f"\nwaiting at the {pending.get('gate')} gate on {pending.get('step')!r}. "
            f"Continue with:\n"
            f"  python -m src.run --continue-from {final['run_id']} --interactive",
            file=sys.stderr,
        )
    return _exit_code(report)


#: What the shell is told. A caller scripting this needs one question answered —
#: did I get a report — and the previous code answered a different one, returning
#: 0 for a run that halted at the first gate with nothing produced.
#:
#: `needs_review` is deliberately 0: an interactive run that stopped to ask
#: something has not failed, it is waiting, and `--continue-from` picks it up. A
#: headless run never reaches that state.
EXIT_CODES: dict[str, int] = {
    "completed": 0,
    "needs_review": 0,
    "failed": 1,
    "halted": 2,
    "running": 3,
}


def _exit_code(report: dict[str, Any]) -> int:
    status = str(report.get("status") or "running")
    if status == "completed" and report.get("errors"):
        # A report was produced and something still went wrong on the way.
        return EXIT_CODES["failed"]
    return EXIT_CODES.get(status, 3)


def _continue_main(args: argparse.Namespace) -> int:
    """`--continue-from`: answer the gate a previous process stopped at."""
    try:
        final = continue_workflow(
            run_id=args.continue_from,
            runs_dir=args.runs_dir,
            decide=ask_on_terminal if args.interactive else _policy_answer(args),
            policy=GatePolicy(
                autocontinue_on_warn=args.allow_warn,
                headless_decision=args.headless_decision,
                interactive=args.interactive,
            ),
            judge_backend=args.judge,
            judge_model=args.judge_model,
        )
    except persistence.ResumeError as exc:
        # Loud and specific. The alternative every one of these replaces is
        # starting the graph from the beginning, which looks like it worked.
        print(f"cannot continue {args.continue_from}: {exc}", file=sys.stderr)
        return 4

    report = summarize(final)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return _exit_code(report)


def _policy_answer(args: argparse.Namespace) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """What a non-interactive `--continue-from` answers with."""

    def decide(_request: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision": args.headless_decision,
            "rationale": f"--continue-from with --headless-decision {args.headless_decision!r}",
        }

    return decide


if __name__ == "__main__":
    raise SystemExit(main())
