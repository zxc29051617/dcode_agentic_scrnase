"""What the run will probably do, said in a way that cannot become a second router.

A preview has to show something. The temptation is to work out the route — is
this FASTQ or a matrix? — and print the list of steps that will run. That is
`src/graph.py`'s job, and a copy of it here would be a second routing
implementation that drifts, which the architecture forbids outright.

So this module does two things and refuses the third:

1. It reads step order from `src.registry`, which is the executable source of
   truth, never from a list written out here.
2. It reports the route as a *provisional* reading of the filesystem, labelled
   as such, with the decision explicitly attributed to `ingest_validate`.
3. It does not decide anything the graph decides. `branch_matrix_kind`,
   `branch_cell_calling`, `branch_after_qc_filter` and every gate stay entirely
   in the executor. A plan that promised the exact step list would be wrong the
   first time a run hit a gate, which is most runs.

When the registry cannot be imported — the controller may run in a venv without
the scientific package — the plan says so rather than inventing an order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Filenames that mean "Cell Ranger has already counted this", used only to
#: guess which entry route a preview should describe. `ingest_validate` makes
#: the real decision, from the filesystem, on every run.
_MATRIX_MARKERS = ("filtered_feature_bc_matrix.h5", "raw_feature_bc_matrix.h5",
                   "matrix.mtx", "matrix.mtx.gz")
_FASTQ_SUFFIXES = (".fastq", ".fastq.gz", ".fq", ".fq.gz")


def _looks_like(path: Path | None) -> str:
    """A provisional reading of what this input is. Never authoritative."""
    if path is None:
        return "unknown"
    if path.is_file():
        name = path.name.lower()
        if name.endswith(".h5ad"):
            return "h5ad"
        if name.endswith(".h5"):
            return "matrix"
        if any(name.endswith(s) for s in _FASTQ_SUFFIXES):
            return "fastq"
        return "unknown"
    try:
        children = list(path.iterdir())
    except OSError:
        return "unknown"
    names = {child.name.lower() for child in children}
    if any(marker in names for marker in _MATRIX_MARKERS):
        return "matrix"
    if any(child.is_dir() and child.name.lower() in
           {"filtered_feature_bc_matrix", "raw_feature_bc_matrix", "outs"} for child in children):
        return "matrix"
    if any(any(child.name.lower().endswith(s) for s in _FASTQ_SUFFIXES) for child in children):
        return "fastq"
    return "unknown"


def _registry_steps() -> tuple[list[str], list[str], str | None]:
    """`(all_steps, mainline, error)` read from the executor's own registry."""
    try:
        from .scientific import ensure_importable
        ensure_importable()
        from src.registry import MAINLINE, REGISTRY  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return [], [], f"the step registry is not importable here ({type(exc).__name__})"
    return list(REGISTRY), list(MAINLINE), None


#: Steps only the FASTQ entry route visits, and steps only the matrix route
#: visits. Attributed to the route rather than listed as "the steps that will
#: run", because which set applies is `ingest_validate`'s answer.
_FASTQ_ONLY = ("resolve_reference", "fastq_preflight", "fastq_qc", "cellranger_count")
_MATRIX_ONLY = ("matrix_preflight",)


def execution_plan(
    *,
    input_path: Path | None,
    analysis: dict[str, Any],
    study_design_ref: str | None,
) -> dict[str, Any]:
    """A description of what confirming this request would set in motion."""
    all_steps, mainline, registry_error = _registry_steps()
    likely = _looks_like(input_path)

    if registry_error:
        return {
            "route": likely,
            "route_is_provisional": True,
            "route_decided_by": "ingest_validate",
            "steps": [],
            "note": registry_error,
            "gates": [],
            "estimated_gates": None,
        }

    excluded = set(_MATRIX_ONLY if likely == "fastq" else _FASTQ_ONLY if likely == "matrix" else ())
    steps = [s for s in all_steps if s not in excluded]

    # Where this run is *likely* to stop and ask, which is the part of a plan a
    # person most needs before confirming. Each is a step that reports evidence
    # and declines to pick a number when the corresponding setting is absent.
    gates: list[dict[str, str]] = []
    if not any(analysis.get(k) is not None for k in ("min_genes", "min_counts", "max_pct_mito")):
        gates.append({
            "step": "apply_cell_qc_filter",
            "why": "no cell QC thresholds were given, so it will report what each cut would "
                   "cost and stop for a person to choose",
        })
    if analysis.get("celltypist_model") is None:
        gates.append({
            "step": "annotate_cells",
            "why": "no CellTypist model was chosen, so it will list the candidates and stop",
        })
    if analysis.get("scmayomap_tissue") is None:
        gates.append({
            "step": "cross_check_annotation",
            "why": "no marker-database tissue was chosen, so it will list the candidates and stop",
        })
    if analysis.get("integration_mode") is None and study_design_ref:
        gates.append({
            "step": "run_integration",
            "why": "a study design is supplied but no integration mode, so the run will say so "
                   "at the gate rather than assume a library is a technical batch",
        })
    gates.append({
        "step": "human_review_decision",
        "why": "the mainline review gate, which every run that produces a report passes through",
    })

    return {
        "route": likely,
        "route_is_provisional": True,
        "route_decided_by": "ingest_validate",
        "steps": steps,
        "mainline": mainline,
        "excluded_by_route": sorted(excluded),
        "gates": gates,
        "estimated_gates": len(gates),
        "note": (
            "The route shown is read from the filesystem for display only. ingest_validate "
            "decides it at run time and its answer wins. Judge verdicts can open further "
            "gates that no plan can predict."
        ),
    }
