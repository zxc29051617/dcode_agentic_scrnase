"""Step registry and skill dispatch.

The orchestrator knows *what to call next* and nothing about how a step works —
see `docs/tool_registry.md`. Implementations live in `skills/<name>/<name>.py`
and expose `run(payload) -> dict`; the ones that still raise
`NotImplementedError` are reported as `scaffold` so the graph stays walkable
while they are filled in one at a time.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Sequence

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

StepKind = Literal["utility", "router", "upstream", "analysis", "gate"]


@dataclass(frozen=True)
class StepSpec:
    """One meaningful pipeline step and the judge that scores it."""

    name: str
    kind: StepKind
    judge: str | None = None
    branches: bool = False
    """True when `graph.py` owns the outgoing edges instead of a linear successor."""

    revisable: tuple[str, ...] = ()
    """Config keys a person may set at this step's gate, and nothing else.

    An allowlist rather than "any key", because a gate answer is the one place
    a value reaches `config` after the run started. Left empty, `revise` on a
    step means only "run it again", which for a deterministic step run against
    an unchanged config is the same result and the same gate — the loop this
    field exists to break.

    Only the steps where a person actually has a choice to make have one. Every
    name here is already a documented CLI flag, so a value set at a gate and a
    value set on the command line take the same path through the same code.
    """

    config_keys: tuple[str, ...] = ()
    """Every config key whose value can change what this step produces.

    Used to answer the only question a per-step resume has to get right: given
    that the config moved, which is the *earliest* step that can no longer be
    trusted? Everything from there on is recomputed and everything before it is
    reused, so this list being short is a performance gain and this list being
    *wrong* is a correctness bug — in one direction only. A key listed on a step
    that does not really read it costs a needless rerun. A key missing from a
    step that does read it hands back a result computed from a value the
    operator has since changed.

    So it is deliberately a superset: it merges what each skill declares in its
    own `INPUT_FIELDS` with every `config.get(...)` its source actually makes,
    and `tests/test_resume_validation.py` re-derives the second half from the
    source on every run rather than trusting this to stay in step by hand.

    A key that appears on no step at all is not assumed harmless — it
    invalidates from the first step, because an unrecognised knob is one whose
    blast radius nobody has established.
    """


#: How to read a revisable value that arrived as text, which is what a terminal
#: — and any HTTP form after it — is going to hand over. A key with no entry
#: here cannot be revised at all, so this doubles as the master list: adding a
#: name to some `StepSpec.revisable` without adding it here is caught by
#: `tests/test_revision.py` rather than at a gate on a real run.
REVISABLE_PARAMETERS: dict[str, Any] = {
    "min_genes": float,
    "min_counts": float,
    "max_pct_mito": float,
    "force_cells": int,
    "min_umi": int,
    "celltypist_model": str,
    "scmayomap_tissue": str,
}


#: MVP steps, in the order given by `docs/tool_registry.md`.
REGISTRY: dict[str, StepSpec] = {
    spec.name: spec
    for spec in (
        StepSpec("ingest_validate", "utility", "judge_ingest", branches=True,
                 config_keys=("qc_metrics_csv", "sample_qc_triage")),
        StepSpec("sample_qc_triage", "utility", "judge_sample_qc", branches=True,
                 config_keys=("exclude_samples", "qc_metrics_csv", "sample_column",
                              "sample_thresholds")),
        # One entry check per route, each asking the species question with the
        # evidence that route actually has: the FASTQ side reads the reference,
        # the matrix side reads the matrix. Both emit the same QC constants.
        StepSpec("resolve_reference", "utility", "judge_reference",
                 config_keys=("reference_root", "species", "transcriptome")),
        StepSpec("matrix_preflight", "utility", "judge_matrix_preflight",
                 config_keys=("species",)),
        StepSpec("fastq_preflight", "upstream", "judge_fastq_preflight",
                 config_keys=("reference", "samplesheet")),
        # Structural checks first (milliseconds), sequencing quality second
        # (minutes): a bundle missing an R2 should never reach FastQC.
        StepSpec("fastq_qc", "upstream", "judge_fastq_qc",
                 config_keys=("fastqc_binary", "fastqc_threads", "min_q30",
                              "multiqc_binary", "skip_fastq_qc")),
        StepSpec("cellranger_count", "upstream", "judge_cellranger_count",
                 config_keys=("binary", "cellranger", "chemistry", "expected_cells",
                              "force_cells", "min_umi", "transcriptome")),
        StepSpec("count_matrix_classify", "router", "judge_matrix_classify", branches=True),
        StepSpec("load_raw_counts", "analysis", "judge_raw_counts", branches=True),
        StepSpec("load_filtered_counts", "analysis", "judge_filtered_counts"),
        # How many cells to keep is the operator's call, so it is also the
        # operator's call to make again after seeing the barcode-rank curve.
        StepSpec("cell_calling_review", "analysis", "judge_cell_calling", branches=True,
                 revisable=("force_cells", "min_umi"),
                 config_keys=("adata_path", "force_cells", "min_umi")),
        # Per-sample work ends here. Everything before this point runs once per
        # library; everything after works on one labelled object, which is what
        # `run_integration` later corrects the batch effect of.
        StepSpec("merge_samples", "analysis", "judge_merge",
                 config_keys=("adata_paths",)),
        # Where the two routes meet. Three steps can produce the matrix, so one
        # node promises the mainline a single shape instead of letting every
        # consumer grow per-route special cases.
        StepSpec("post_load_validate", "analysis", "judge_post_load",
                 config_keys=("adata_path", "species")),
        StepSpec("run_qc_metrics", "analysis", "judge_qc",
                 config_keys=("erythroid_genes", "mito_prefix")),
        # The step that stops for thresholds is the step whose gate has to be
        # able to take them. `max_pct_erythroid` is a fourth threshold this step
        # accepts from the CLI and is deliberately not revisable yet: the four
        # revisable steps were chosen as a first set, and widening the allowlist
        # is a one-line change once there is a reason to. It is still a
        # `config_key`, because changing it on the command line does invalidate
        # this step's result whether or not a gate may set it.
        StepSpec("apply_cell_qc_filter", "analysis", "judge_cell_qc_filter", branches=True,
                 revisable=("min_genes", "min_counts", "max_pct_mito"),
                 config_keys=("adata_path", "max_pct_erythroid", "max_pct_mito",
                              "min_counts", "min_genes")),
        StepSpec("detect_doublets", "analysis", "judge_doublets",
                 config_keys=("doublet_threshold", "expected_doublet_rate",
                              "random_state", "remove_doublets")),
        StepSpec("normalize_hvg_prepare", "analysis", "judge_preprocess",
                 config_keys=("hvg_flavor", "min_cells_per_gene", "n_top_genes",
                              "normalize_target_sum")),
        StepSpec("run_pca", "analysis", "judge_pca",
                 config_keys=("n_comps", "random_state", "use_highly_variable")),
        StepSpec("run_integration", "analysis", "judge_integration",
                 config_keys=("batch_key", "force_integration", "max_iter_harmony",
                              "random_state")),
        StepSpec("run_clustering", "analysis", "judge_clustering",
                 config_keys=("adata_path", "embedding_key", "n_neighbors",
                              "random_state", "resolution")),
        StepSpec("run_umap", "analysis", "judge_umap",
                 config_keys=("adata_path", "embedding_key", "method", "perplexity",
                              "random_state")),
        StepSpec("find_markers", "analysis", "judge_markers",
                 config_keys=("cluster_key", "marker_method", "n_genes_reported")),
        # Both of these stop rather than guess, and both list their candidates
        # as evidence. The gate is where a person reads that list, so it is
        # where the answer belongs.
        StepSpec("annotate_cells", "analysis", "judge_annotation",
                 revisable=("celltypist_model",),
                 config_keys=("celltypist_model", "cluster_key", "majority_voting")),
        # A second opinion on the same clusters from an unrelated method: a
        # marker database scored against `find_markers`, never touching the
        # matrix CellTypist learned from. It changes no label — it reports where
        # the two methods part company, and where either is running on thin
        # evidence, for the gate that follows.
        StepSpec("cross_check_annotation", "analysis", "judge_cross_check",
                 revisable=("scmayomap_tissue",),
                 config_keys=("scmayomap_tissue",)),
        StepSpec("human_review_decision", "gate", None),
        StepSpec("build_report", "utility", "judge_report",
                 config_keys=("inline_figures",)),
    )
}

#: Scanpy mainline, run in order once the input has been loaded into AnnData.
MAINLINE: tuple[str, ...] = (
    "run_qc_metrics",
    "apply_cell_qc_filter",
    "detect_doublets",
    "normalize_hvg_prepare",
    "run_pca",
    "run_integration",
    "run_clustering",
    "run_umap",
    "find_markers",
    "annotate_cells",
    "cross_check_annotation",
)

def coerce_overrides(
    raw: dict[str, Any] | None, allowed: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    """Split an operator's proposed values into what is allowed and what is not.

    Returns `(accepted, rejected)`, where `rejected` holds one sentence per
    refusal, meant to be shown to the person who typed it. Nothing is silently
    dropped: a value that does not arrive in `config` and does not come back as
    a complaint is a person believing they changed something they did not.

    Two kinds of refusal, and they are different mistakes. A key this gate does
    not offer is a scope error — `celltypist_model` is a real parameter, it is
    simply not the QC gate's to set, and letting it through would mean any gate
    could reach into any step's config. A value that will not convert is a typo.

    `allowed` is passed in rather than looked up from `step`, because the gate
    that owns the question is the one that knows what answering it will re-run:
    the escalation gate re-runs one step, the mainline gate re-enters the
    mainline at `annotate_cells` and re-runs everything after it.
    """
    accepted: dict[str, Any] = {}
    rejected: list[str] = []

    for key, value in (raw or {}).items():
        if key not in allowed:
            offered = ", ".join(allowed) if allowed else "nothing"
            rejected.append(f"{key} is not offered at this gate (it offers: {offered})")
            continue
        # Belt and braces: `REVISABLE_PARAMETERS` is the master list, and a name
        # in `revisable` that is missing from it is a wiring mistake, not an
        # operator mistake. Refuse rather than guess at a type.
        convert = REVISABLE_PARAMETERS.get(key)
        if convert is None:
            rejected.append(f"{key} has no declared type and cannot be read safely")
            continue
        try:
            accepted[key] = convert(value)
        except (TypeError, ValueError):
            rejected.append(f"{key}={value!r} is not a valid {convert.__name__}")

    return accepted, rejected


def steps_invalidated_by(step: str) -> tuple[str, ...]:
    """`step` and everything the registry runs after it.

    Changing a parameter changes what every later step should have produced, so
    a resumed run must not hand back the results it already has for them.
    `REGISTRY` is declared in pipeline order and is a valid topological order for
    both routes, so position in it is the dependency answer. Naming a step that
    this particular route never visits costs nothing — it has no result to reuse
    either way — and that is the safe direction to be wrong in.
    """
    names = list(REGISTRY)
    if step not in names:
        return tuple(names)
    return tuple(names[names.index(step):])


def earliest_step_reading(keys: Sequence[str]) -> tuple[str | None, dict[str, str]]:
    """The first step in pipeline order that any of `keys` can change.

    Returns `(step, {key: step})` — the cut, and which step each key was
    attributed to, so a caller can say *why* it is re-running from there rather
    than only that it is.

    An unrecognised key is attributed to the first step in the registry. That is
    the fail-closed reading: a knob nobody has mapped is a knob whose effect on
    the pipeline is unknown, and treating unknown as harmless is how a resumed
    run keeps a result the operator has already invalidated.
    """
    order = list(REGISTRY)
    owner: dict[str, str] = {}
    cut: int | None = None
    for key in keys:
        readers = [i for i, name in enumerate(order) if key in REGISTRY[name].config_keys]
        index = min(readers) if readers else 0
        owner[key] = order[index]
        cut = index if cut is None else min(cut, index)
    return (order[cut] if cut is not None else None), owner


def revisable_from(step: str) -> tuple[str, ...]:
    """Every parameter a person may set, given that `step` onward will re-run."""
    seen: dict[str, None] = {}
    for name in steps_invalidated_by(step):
        for key in REGISTRY[name].revisable:
            seen[key] = None
    return tuple(seen)


_module_cache: dict[str, ModuleType | None] = {}


def load_skill(name: str) -> ModuleType | None:
    """Import `skills/<name>/<name>.py`, or return None if it is absent."""
    if name in _module_cache:
        return _module_cache[name]

    path = SKILLS_DIR / name / f"{name}.py"
    if not path.exists():
        _module_cache[name] = None
        return None

    qualified = f"dcode_scrna_skills.{name}"
    spec = importlib.util.spec_from_file_location(qualified, path)
    if spec is None or spec.loader is None:
        _module_cache[name] = None
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    _module_cache[name] = module
    return module


def call_skill(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run a skill and normalize the outcome.

    Returns `{"status", "output", "warnings", "errors"}` where status is:
      - `ok`       — the skill ran and returned a payload
      - `scaffold` — the skill exists but is still `NotImplementedError`
      - `error`    — the skill is missing, crashed, or returned a bad shape
    """
    module = load_skill(name)
    if module is None:
        return {
            "status": "error",
            "output": {},
            "warnings": [],
            "errors": [f"no skill module found at skills/{name}/{name}.py"],
        }

    runner = getattr(module, "run", None)
    if not callable(runner):
        return {
            "status": "error",
            "output": {},
            "warnings": [],
            "errors": [f"skills/{name}/{name}.py does not define a callable run()"],
        }

    try:
        output = runner(payload)
    except NotImplementedError:
        return {
            "status": "scaffold",
            "output": {},
            "warnings": [f"{name} is a scaffold; no analysis was performed"],
            "errors": [],
        }
    except Exception as exc:  # noqa: BLE001 - the graph must record, not crash
        return {
            "status": "error",
            "output": {},
            "warnings": [],
            "errors": [f"{name} raised {type(exc).__name__}: {exc}"],
        }

    if not isinstance(output, dict):
        return {
            "status": "error",
            "output": {},
            "warnings": [],
            "errors": [f"{name}.run() returned {type(output).__name__}, expected dict"],
        }

    return {
        "status": "ok",
        "output": output,
        "warnings": list(output.get("warnings") or []),
        "errors": list(output.get("errors") or []),
    }
