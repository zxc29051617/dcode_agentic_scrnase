"""Compute deterministic QC metrics — the first step with real numbers to judge.

Everything before this point checked structure, identity and format. This
measures the biology: UMIs and genes detected per cell, and the mitochondrial /
erythroid fraction that `apply_cell_qc_filter` will threshold on.

**This step only measures.** No cell or gene is removed here, and no verdict
about "good" or "bad" is made — that split (analysis vs. judgment) is the
project's own rule, and threshold decisions belong to `apply_cell_qc_filter` or
the judge, not to a step whose job is to report what is there.

Run standalone:
    python skills/run_qc_metrics/run_qc_metrics.py <adata.h5ad> --run-dir <out> --species human
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import matrix_io  # noqa: E402

TOOL_NAME = "run_qc_metrics"
INPUT_FIELDS = (
    "artifacts.post_load_validate",
    "artifacts.resolve_reference",
    "artifacts.matrix_preflight",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "qc_metrics",
    "per_sample",
    "mito_computed",
    "erythroid_computed",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Scanpy's `percent_top` needs at least this many genes per entry; skip it on
#: tiny fixtures rather than let it raise, since it says nothing this project
#: uses (no step reads percent_top_50 etc.).
PERCENT_TOP: tuple[int, ...] | None = None


def _resolve_adata_path(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    path = (artifacts.get("post_load_validate") or {}).get("adata_path")
    if path:
        return str(path)
    return (payload.get("config") or {}).get("adata_path")


def _species_constants(payload: dict[str, Any]) -> tuple[str | None, list[str]]:
    """`mito_prefix` and `erythroid_genes`, from whichever entry step ran.

    Both `resolve_reference` (FASTQ route) and `matrix_preflight` (matrix route)
    emit the same two fields from `species.constants_for`, so the mainline reads
    one shape whichever way the run came in.
    """
    artifacts = payload.get("artifacts") or {}
    for step in ("resolve_reference", "matrix_preflight"):
        source = artifacts.get(step) or {}
        if source.get("mito_prefix") or source.get("erythroid_genes"):
            return source.get("mito_prefix"), list(source.get("erythroid_genes") or [])
    config = payload.get("config") or {}
    return config.get("mito_prefix"), list(config.get("erythroid_genes") or [])


def _flag_genes(var_names: Any, *, prefix: str | None = None, names: list[str] | None = None) -> Any:
    """Boolean mask over `var_names`: prefix match (case-insensitive) or an exact set."""
    import numpy as np

    if prefix:
        upper = prefix.upper()
        return np.array([str(name).upper().startswith(upper) for name in var_names])
    wanted = set(names or [])
    return np.array([str(name) in wanted for name in var_names])


def _sample_breakdown(adata: Any) -> dict[str, Any]:
    """Per-sample QC medians, so heterogeneity across libraries is visible
    before it gets hidden inside a single pooled threshold."""
    if "sample" not in adata.obs:
        return {}
    import numpy as np

    breakdown = {}
    for sample, group in adata.obs.groupby("sample", observed=True):
        entry = {
            "n_cells": int(len(group)),
            "median_genes_per_cell": int(np.median(group["n_genes_by_counts"])),
            "median_umi_per_cell": int(np.median(group["total_counts"])),
        }
        if "pct_counts_mt" in group:
            entry["median_pct_mito"] = round(float(np.median(group["pct_counts_mt"])), 2)
        breakdown[str(sample)] = entry
    return breakdown


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import scanpy as sc

    warnings: list[str] = []

    source = _resolve_adata_path(payload)
    if not source:
        return _result(errors=["no AnnData path; post_load_validate must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs == 0:
        return _result(errors=[f"{source} contains no cells"])

    mito_prefix, erythroid_genes = _species_constants(payload)

    # --- mitochondrial fraction ---------------------------------------------
    mito_computed = False
    if not mito_prefix:
        warnings.append(
            "no mitochondrial gene prefix known (species not resolved); "
            "pct_counts_mt will not be computed rather than silently reported as 0"
        )
        adata.var["mt"] = False
    else:
        adata.var["mt"] = _flag_genes(adata.var_names, prefix=mito_prefix)
        n_mito = int(adata.var["mt"].sum())
        if n_mito == 0:
            warnings.append(
                f"no genes matched the mitochondrial prefix {mito_prefix!r}; "
                "pct_counts_mt will be 0 for every cell — check the gene naming "
                "convention against config.mito_prefix"
            )
        else:
            mito_computed = True

    # --- erythroid fraction --------------------------------------------------
    erythroid_computed = False
    if not erythroid_genes:
        adata.var["erythroid"] = False
    else:
        adata.var["erythroid"] = _flag_genes(adata.var_names, names=erythroid_genes)
        n_erythroid = int(adata.var["erythroid"].sum())
        if n_erythroid == 0:
            warnings.append(
                f"none of the {len(erythroid_genes)} configured erythroid genes were "
                "found in this matrix; erythroid contamination will not be measured"
            )
        else:
            erythroid_computed = True

    qc_vars = [v for v in ("mt", "erythroid") if adata.var[v].any()]
    sc.pp.calculate_qc_metrics(
        adata, qc_vars=qc_vars, percent_top=PERCENT_TOP, log1p=False, inplace=True
    )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    qc_metrics: dict[str, Any] = {
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "median_genes_per_cell": int(np.median(adata.obs["n_genes_by_counts"])),
        "median_umi_per_cell": int(np.median(adata.obs["total_counts"])),
        "min_genes_per_cell": int(adata.obs["n_genes_by_counts"].min()),
        "max_genes_per_cell": int(adata.obs["n_genes_by_counts"].max()),
        "mito_computed": mito_computed,
        "erythroid_computed": erythroid_computed,
    }
    if mito_computed:
        qc_metrics["median_pct_mito"] = round(float(np.median(adata.obs["pct_counts_mt"])), 2)
        qc_metrics["max_pct_mito"] = round(float(adata.obs["pct_counts_mt"].max()), 2)
    if erythroid_computed:
        qc_metrics["median_pct_erythroid"] = round(
            float(np.median(adata.obs["pct_counts_erythroid"])), 2
        )

    return _result(
        adata_path=adata_path,
        qc_metrics=qc_metrics,
        per_sample=_sample_breakdown(adata),
        mito_computed=mito_computed,
        erythroid_computed=erythroid_computed,
        warnings=warnings,
        next_tool="apply_cell_qc_filter",
        metrics=qc_metrics,
    )


def _result(
    *,
    adata_path: str | None = None,
    qc_metrics: dict[str, Any] | None = None,
    per_sample: dict[str, Any] | None = None,
    mito_computed: bool = False,
    erythroid_computed: bool = False,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "qc_metrics": qc_metrics or {},
        "per_sample": per_sample or {},
        "mito_computed": mito_computed,
        "erythroid_computed": erythroid_computed,
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("adata")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--species")
    args = parser.parse_args(argv)

    import sys as _sys

    sys.path.insert(0, str(_PROJECT_ROOT))
    from src import species as species_table

    constants = species_table.constants_for({"species": args.species}) if args.species else {}
    result = run(
        {
            "artifacts": {
                "post_load_validate": {"adata_path": args.adata},
                "resolve_reference": constants,
            },
            "run_dir": args.run_dir,
            "config": {},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
