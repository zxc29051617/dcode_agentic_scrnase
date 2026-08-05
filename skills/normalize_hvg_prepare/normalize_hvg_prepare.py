"""Normalize, log-transform, and flag highly variable genes for PCA.

Unlike `apply_cell_qc_filter`, this step has a defensible default and does not
stop for a decision — the same shape as `detect_doublets`. Cutting cells is a
tissue-specific judgment call with no universal right answer; normalizing to
median depth and picking ~2,000 HVGs is closer to `detect_doublets`' 10x
loading formula: a documented standard, not a value borrowed from someone
else's protocol.

## Genes are filtered here, not earlier
`apply_cell_qc_filter` deliberately filters cells only. A gene detected in a
handful of cells contributes nothing to HVG selection but does distort the
mean-variance fit those methods rely on, so it is dropped here — `min_cells`
below, using scanpy's own default.

## HVG selection reads counts, not the normalized matrix
`flavor="seurat_v3"` fits variance directly on raw counts (`layers["counts"]`),
which scanpy recommends over the classic `"seurat"` flavor for UMI data — the
old flavor was designed for the log-dispersion behaviour of non-UMI protocols
and is a poorer fit for the sparse, integer-count matrices 10x produces.

## HVG selection is per sample, same instinct as `detect_doublets`
A gene that looks variable only because two libraries differ in depth or
chemistry is a batch artefact, not biology. When a `sample` column is present,
`batch_key="sample"` asks Scanpy to score variability within each library and
combine the votes, rather than across all libraries pooled together.

## Nothing is subsetted
HVGs are flagged in `var["highly_variable"]`, not used to drop genes. `run_pca`
reads the flag to choose which genes drive the embedding; `find_markers` and
`annotate_cells` still need the full gene set, and subsetting here would take
that choice away from them before they exist.

## No scaling, no regression
Older tutorials add `sc.pp.scale` and `sc.pp.regress_out` here. Current Scanpy
guidance drops both for log-normalized UMI data: they change downstream
clustering little while adding parameters nobody sets deliberately. If a later
sample shows a real need (a strong cell-cycle signal, say), that is a config
addition to make with evidence in front of it, not a default to carry now.

Run standalone:
    python skills/normalize_hvg_prepare/normalize_hvg_prepare.py <adata.h5ad> --run-dir <out>
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

TOOL_NAME = "normalize_hvg_prepare"
INPUT_FIELDS = (
    "artifacts.detect_doublets",
    "config.n_top_genes",
    "config.min_cells_per_gene",
    "config.normalize_target_sum",
    "config.hvg_flavor",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "hvg_summary",
    "prep_summary",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: scanpy's own default for `sc.pp.highly_variable_genes` and widely used
#: elsewhere (Seurat's default is also 2,000). Not a tissue-specific value —
#: this is how many genes the method looks at, not a biological cutoff.
DEFAULT_N_TOP_GENES = 2_000

#: scanpy's own default for `sc.pp.filter_genes`. A gene seen in fewer cells
#: than this cannot support a variance estimate worth using for HVG selection.
DEFAULT_MIN_CELLS_PER_GENE = 3

#: Recommended for UMI data (10x): fits variance on raw counts rather than the
#: log-dispersion the classic "seurat" flavor was designed around.
DEFAULT_HVG_FLAVOR = "seurat_v3"

#: `seurat_v3` fits a loess curve (via scikit-misc) across genes binned by mean
#: expression. Below this many genes the fit is degenerate rather than merely
#: noisy: on real data this floor is never close (thousands of genes survive
#: filtering), but a small custom panel — or, in testing, a synthetic fixture —
#: can land under it. There it does not always fail cleanly: the same
#: near-collinear input raised a catchable ValueError in isolation but crashed
#: the interpreter outright inside the full test run, so this is a floor
#: checked *before* the fit, not a case left for the `except` below to catch.
MIN_GENES_FOR_SEURAT_V3 = 50


def _resolve_adata_path(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    for step in ("detect_doublets", "apply_cell_qc_filter", "run_qc_metrics"):
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
        return _result(errors=["no AnnData path; detect_doublets must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs == 0:
        return _result(errors=[f"{source} contains no cells"])
    if "counts" not in adata.layers:
        return _result(errors=[f"{source} has no layers['counts']; post_load_validate must run first"])

    import numpy as np
    import scanpy as sc

    n_genes_in = int(adata.n_vars)

    # ---- gene filtering -----------------------------------------------------
    min_cells = int(config.get("min_cells_per_gene", DEFAULT_MIN_CELLS_PER_GENE))
    sc.pp.filter_genes(adata, min_cells=min_cells)
    n_dropped_genes = n_genes_in - adata.n_vars
    if adata.n_vars == 0:
        return _result(errors=[f"min_cells_per_gene={min_cells} left no genes"])

    # ---- HVG selection, per sample when we can tell samples apart -----------
    n_top_genes = int(config.get("n_top_genes", DEFAULT_N_TOP_GENES))
    if n_top_genes >= adata.n_vars:
        warnings.append(
            f"n_top_genes={n_top_genes} requested but only {adata.n_vars} genes remain "
            f"after filtering; using {adata.n_vars - 1} instead"
        )
        n_top_genes = adata.n_vars - 1

    flavor = str(config.get("hvg_flavor", DEFAULT_HVG_FLAVOR))
    if flavor == "seurat_v3" and adata.n_vars < MIN_GENES_FOR_SEURAT_V3:
        warnings.append(
            f"only {adata.n_vars} genes remain after filtering, below the "
            f"{MIN_GENES_FOR_SEURAT_V3} seurat_v3's loess fit needs to be stable; "
            "falling back to flavor='seurat'"
        )
        flavor = "seurat"
    batch_key = "sample" if "sample" in adata.obs and adata.obs["sample"].nunique() > 1 else None

    # ---- normalize + log1p on X first; counts stay untouched in the layer ---
    # seurat_v3 fits variance on raw counts and must run before this. The other
    # flavors fit on log-normalized dispersion and must run after — X carries
    # whichever this step's flavor needs by the time highly_variable_genes runs.
    target_sum = config.get("normalize_target_sum")
    sc.pp.normalize_total(adata, target_sum=float(target_sum) if target_sum else None)
    sc.pp.log1p(adata)
    actual_target = float(np.median(np.asarray(adata.layers["counts"].sum(axis=1)).ravel()))

    hvg_kwargs: dict[str, Any] = {"n_top_genes": n_top_genes, "flavor": flavor}
    if flavor == "seurat_v3":
        hvg_kwargs["layer"] = "counts"
    if batch_key:
        hvg_kwargs["batch_key"] = batch_key

    try:
        sc.pp.highly_variable_genes(adata, **hvg_kwargs)
    except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
        return _result(errors=[f"highly_variable_genes failed: {type(exc).__name__}: {exc}"])

    n_hvg = int(adata.var["highly_variable"].sum())
    if n_hvg == 0:
        return _result(errors=["highly_variable_genes selected no genes"])

    if batch_key is None and "sample" in adata.obs and adata.obs["sample"].nunique() > 1:
        # Should not happen given the branch above, but a silent pooled fit on
        # multiple libraries is exactly the failure mode this step exists to avoid.
        warnings.append("multiple samples present but HVGs were selected without batch_key")

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    hvg_summary = {
        "n_genes_in": n_genes_in,
        "n_genes_after_filter": int(adata.n_vars),
        "n_genes_dropped": n_dropped_genes,
        "min_cells_per_gene": min_cells,
        "n_top_genes_requested": int(config.get("n_top_genes", DEFAULT_N_TOP_GENES)),
        "n_hvg": n_hvg,
        "hvg_flavor": flavor,
        "hvg_flavor_requested": str(config.get("hvg_flavor", DEFAULT_HVG_FLAVOR)),
        "batch_key": batch_key,
    }
    prep_summary = {
        "normalize_target_sum": actual_target if not target_sum else float(target_sum),
        "normalize_target_sum_source": "config" if target_sum else "median (scanpy default)",
        "log1p": True,
        "scaled": False,
        "subsetted_to_hvg": False,
    }
    if batch_key is None and "sample" in adata.obs:
        notes.append("single sample present; HVG selection ran without batch_key")

    return _result(
        adata_path=adata_path,
        hvg_summary=hvg_summary,
        prep_summary=prep_summary,
        warnings=warnings,
        notes=notes,
        next_tool="run_pca",
        metrics={**hvg_summary, **prep_summary},
    )


def _result(
    *,
    adata_path: str | None = None,
    hvg_summary: dict[str, Any] | None = None,
    prep_summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "hvg_summary": hvg_summary or {},
        "prep_summary": prep_summary or {},
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
    parser.add_argument("--n-top-genes", type=int)
    parser.add_argument("--min-cells-per-gene", type=int)
    parser.add_argument("--normalize-target-sum", type=float)
    parser.add_argument("--hvg-flavor")
    args = parser.parse_args(argv)

    config: dict[str, Any] = {}
    if args.n_top_genes is not None:
        config["n_top_genes"] = args.n_top_genes
    if args.min_cells_per_gene is not None:
        config["min_cells_per_gene"] = args.min_cells_per_gene
    if args.normalize_target_sum is not None:
        config["normalize_target_sum"] = args.normalize_target_sum
    if args.hvg_flavor is not None:
        config["hvg_flavor"] = args.hvg_flavor

    result = run(
        {
            "artifacts": {"detect_doublets": {"adata_path": args.adata_path}},
            "run_dir": args.run_dir,
            "config": config,
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
