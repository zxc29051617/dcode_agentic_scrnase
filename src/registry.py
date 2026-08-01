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
from typing import Any, Literal

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


#: MVP steps, in the order given by `docs/tool_registry.md`.
REGISTRY: dict[str, StepSpec] = {
    spec.name: spec
    for spec in (
        StepSpec("ingest_validate", "utility", "judge_ingest"),
        StepSpec("resolve_reference", "utility", "judge_reference", branches=True),
        StepSpec("sample_qc_triage", "utility", "judge_sample_qc"),
        StepSpec("fastq_preflight", "upstream", "judge_fastq_preflight"),
        # Structural checks first (milliseconds), sequencing quality second
        # (minutes): a bundle missing an R2 should never reach FastQC.
        StepSpec("fastq_qc", "upstream", "judge_fastq_qc"),
        StepSpec("cellranger_count", "upstream", "judge_cellranger_count"),
        StepSpec("count_matrix_classify", "router", "judge_matrix_classify", branches=True),
        StepSpec("load_raw_counts", "analysis", "judge_raw_counts", branches=True),
        StepSpec("load_filtered_counts", "analysis", "judge_filtered_counts"),
        StepSpec("cell_calling_review", "analysis", "judge_cell_calling"),
        StepSpec("run_qc_metrics", "analysis", "judge_qc"),
        StepSpec("apply_cell_qc_filter", "analysis", "judge_cell_qc_filter"),
        StepSpec("detect_doublets", "analysis", "judge_doublets"),
        StepSpec("normalize_hvg_prepare", "analysis", "judge_preprocess"),
        StepSpec("run_pca", "analysis", "judge_pca"),
        StepSpec("run_integration", "analysis", "judge_integration"),
        StepSpec("run_clustering", "analysis", "judge_clustering"),
        StepSpec("run_umap", "analysis", "judge_umap"),
        StepSpec("find_markers", "analysis", "judge_markers"),
        StepSpec("annotate_cells", "analysis", "judge_annotation"),
        StepSpec("human_review_decision", "gate", None),
        StepSpec("build_report", "utility", "judge_report"),
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
)

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
