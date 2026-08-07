"""Fit PCA on the prepared AnnData object.

Same shape as `normalize_hvg_prepare`: a documented default (50 components,
Scanpy's and Seurat's own), not a value borrowed from a specific tissue or
protocol, so this step does not stop for a decision.

## Fits on HVGs, embeds and loads on all genes
`mask_var="highly_variable"` restricts which genes drive the fit — the same
reason `normalize_hvg_prepare` flags HVGs instead of subsetting the matrix. The
resulting embedding (`obsm["X_pca"]`) still has a coordinate for every cell, and
the loadings (`varm["PCs"]`) still have a row for every gene, that gene's
contribution is just zero for genes that were not used to fit it.

## The component count is bounded by the data, not just requested
PCA cannot return more components than `min(n_obs, n_vars_used) - 1`, and
`arpack` (the default solver here) raises rather than silently truncating. The
same instinct as `detect_doublets`' `_components_for`: derive the bound from
the matrix in front of it and clamp before the call, not after a crash.

Run standalone:
    python skills/run_pca/run_pca.py <adata.h5ad> --run-dir <out>
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

TOOL_NAME = "run_pca"
INPUT_FIELDS = (
    "artifacts.normalize_hvg_prepare",
    "config.n_comps",
    "config.use_highly_variable",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "pca_summary",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Scanpy's own default and the number Seurat also settles on. Not a
#: tissue-specific choice — how many axes the embedding keeps, not a
#: biological cutoff.
DEFAULT_N_COMPS = 50

#: How many of the leading variance-ratio values to report. Enough for a judge
#: to see the elbow; the full array belongs in the AnnData, not the payload.
N_VARIANCE_RATIOS_REPORTED = 20


def _resolve_adata_path(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    for step in ("normalize_hvg_prepare", "detect_doublets", "apply_cell_qc_filter"):
        path = (artifacts.get(step) or {}).get("adata_path")
        if path:
            return str(path)
    return (payload.get("config") or {}).get("adata_path")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source = _resolve_adata_path(payload)
    if not source:
        return _result(errors=["no AnnData path; normalize_hvg_prepare must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs < 2:
        return _result(errors=[f"{source} has {adata.n_obs} cell(s); PCA needs at least 2"])
    if adata.n_vars < 2:
        return _result(errors=[f"{source} has {adata.n_vars} gene(s); PCA needs at least 2"])

    import numpy as np
    import scanpy as sc

    # ---- which genes drive the fit ------------------------------------------
    use_hvg = bool(config.get("use_highly_variable", True))
    mask_var: str | None = None
    if use_hvg and "highly_variable" in adata.var:
        n_hvg = int(adata.var["highly_variable"].sum())
        if n_hvg >= 2:
            mask_var = "highly_variable"
        else:
            warnings.append(
                f"only {n_hvg} highly_variable genes are flagged; fitting on all "
                f"{adata.n_vars} genes instead"
            )
    elif use_hvg:
        warnings.append(
            "no highly_variable flag on this object (normalize_hvg_prepare did "
            f"not run, or ran with HVG selection off); fitting on all {adata.n_vars} genes"
        )
    n_genes_used = int(adata.var["highly_variable"].sum()) if mask_var else int(adata.n_vars)

    # ---- component count, bounded before the call ---------------------------
    n_comps = int(config.get("n_comps", DEFAULT_N_COMPS))
    rank_bound = min(int(adata.n_obs), n_genes_used) - 1
    if n_comps > rank_bound:
        warnings.append(
            f"n_comps={n_comps} requested but only {adata.n_obs} cells and "
            f"{n_genes_used} genes are available (rank bound {rank_bound}); "
            f"using {rank_bound} instead"
        )
        n_comps = rank_bound
    if n_comps < 1:
        return _result(errors=[f"no components possible: rank bound is {rank_bound}"])

    try:
        sc.pp.pca(
            adata,
            n_comps=n_comps,
            mask_var=mask_var,
            random_state=int(config.get("random_state", 0)),
        )
    except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
        return _result(errors=[f"PCA failed: {type(exc).__name__}: {exc}"])

    variance_ratio = np.asarray(adata.uns["pca"]["variance_ratio"], dtype=float)
    cumulative = float(np.cumsum(variance_ratio)[-1])
    if cumulative < 0.20:
        notes.append(
            f"the {n_comps} components kept explain only {cumulative:.0%} of variance; "
            "the embedding may be noisy for clustering"
        )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    pca_summary = {
        "n_comps": n_comps,
        "n_comps_requested": int(config.get("n_comps", DEFAULT_N_COMPS)),
        "n_genes_used": n_genes_used,
        "random_state": int(config.get("random_state", 0)),
        "used_highly_variable": mask_var is not None,
        # `round()` on a numpy scalar returns a numpy scalar; state has to hold
        # built-ins to be checkpointable.
        "variance_ratio": [round(float(v), 4) for v in variance_ratio[:N_VARIANCE_RATIOS_REPORTED]],
        "cumulative_variance_explained": round(cumulative, 4),
    }

    return _result(
        adata_path=adata_path,
        pca_summary=pca_summary,
        warnings=warnings,
        notes=notes,
        next_tool="run_integration",
        metrics=pca_summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    pca_summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "pca_summary": pca_summary or {},
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "notes": notes or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=TOOL_NAME)
    parser.add_argument("adata_path")
    parser.add_argument("--run-dir", default="runs/manual")
    parser.add_argument("--n-comps", type=int)
    parser.add_argument("--no-hvg", action="store_true", help="fit on all genes, not just HVGs")
    args = parser.parse_args(argv)

    config: dict[str, Any] = {}
    if args.n_comps is not None:
        config["n_comps"] = args.n_comps
    if args.no_hvg:
        config["use_highly_variable"] = False

    result = run(
        {
            "artifacts": {"normalize_hvg_prepare": {"adata_path": args.adata_path}},
            "run_dir": args.run_dir,
            "config": config,
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
