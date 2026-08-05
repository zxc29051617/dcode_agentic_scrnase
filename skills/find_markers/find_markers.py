"""Rank genes that distinguish each cluster from the rest.

The first downstream step that reads expression (`X`) rather than an
embedding. Everything from `run_pca` to `run_umap` worked on coordinates; this
reads the log-normalized matrix `normalize_hvg_prepare` produced.

## Every gene is tested, not just the HVGs
`normalize_hvg_prepare` flagged highly variable genes without subsetting the
matrix, and this is the step that collects on that decision. A canonical
marker is not always in the top 2,000 most variable genes — a gene expressed
crisply in one small cluster and nowhere else can have modest variance across
the whole object. Restricting the test to HVGs would silently hide exactly the
genes `annotate_cells` needs most.

## `method="wilcoxon"`, not scanpy's default
`sc.tl.rank_genes_groups` defaults to a t-test; scanpy's own clustering
tutorial uses and recommends Wilcoxon for this, as a rank test makes no
normality assumption about log-normalized counts.

Both tests treat cells as independent replicates, which overstates
significance when the real unit of replication is the sample — an accepted
limitation of per-cluster marker ranking, and a reason to read the effect
sizes (`logfoldchanges`, `pct_nz_group`) alongside the p-values rather than
sorting on significance alone.

## A one-cell cluster would take every other cluster down with it
scanpy raises `Could not calculate statistics for groups ... only contain one
sample` — and that failure aborts the whole call, so a single stray cluster of
one cell means no markers for any of the other fourteen. Clusters below
`MIN_CELLS_PER_CLUSTER` are therefore excluded from `groups=` before the call,
with a warning naming them, rather than being caught afterwards.

## The full table goes to disk, a summary goes to the state
Fifteen clusters over twenty thousand genes is a third of a million rows. The
returned dict carries the top `n_genes_reported` per cluster; the complete
ranking is written next to the AnnData as CSV. The same rule the AnnData
itself follows: large results travel as paths, summaries travel in state.

Run standalone:
    python skills/find_markers/find_markers.py <adata.h5ad> --run-dir <out>
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

TOOL_NAME = "find_markers"
INPUT_FIELDS = (
    "artifacts.run_umap",
    "config.cluster_key",
    "config.marker_method",
    "config.n_genes_reported",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "marker_table_path",
    "top_markers",
    "marker_summary",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: What scanpy's own clustering tutorial uses. A rank test makes no normality
#: assumption about log-normalized counts, unlike the `t-test` default.
DEFAULT_METHOD = "wilcoxon"

#: scanpy raises outright for a group of one cell, and the failure aborts the
#: whole call rather than that one group. This is the technical floor, not a
#: quality bar — `run_clustering` already flags clusters under 10 cells.
MIN_CELLS_PER_CLUSTER = 2

#: How many ranked genes per cluster travel back in the state. The full table
#: is written to disk regardless.
DEFAULT_N_GENES_REPORTED = 25

#: Adjusted-p threshold used only to count how many genes clear it, as one
#: number a judge can read. Nothing is filtered out of the written table.
SIGNIFICANCE_ALPHA = 0.05


def _resolve(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    for step in ("run_umap", "run_clustering"):
        path = (artifacts.get(step) or {}).get("adata_path")
        if path:
            return str(path)
    return (payload.get("config") or {}).get("adata_path")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source = _resolve(payload)
    if not source:
        return _result(errors=["no AnnData path; run_umap must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])

    cluster_key = str(config.get("cluster_key", "leiden"))
    if cluster_key not in adata.obs:
        return _result(errors=[f"{source} has no obs['{cluster_key}']; run_clustering must run first"])
    if adata.n_vars < 1:
        return _result(errors=[f"{source} has no genes to rank"])

    import scanpy as sc

    sizes = adata.obs[cluster_key].value_counts()
    testable = sorted(str(name) for name, count in sizes.items() if count >= MIN_CELLS_PER_CLUSTER)
    excluded = {str(name): int(count) for name, count in sizes.items() if count < MIN_CELLS_PER_CLUSTER}

    if excluded:
        warnings.append(
            f"{len(excluded)} cluster(s) have fewer than {MIN_CELLS_PER_CLUSTER} cells "
            f"({excluded}); excluded from the comparison, because scanpy aborts the whole "
            "ranking rather than just that group"
        )
    if len(testable) < 2:
        return _result(
            errors=[
                f"only {len(testable)} cluster(s) with at least {MIN_CELLS_PER_CLUSTER} cells; "
                "ranking a cluster against the rest needs at least two"
            ],
            warnings=warnings,
        )

    method = str(config.get("marker_method", DEFAULT_METHOD))
    try:
        sc.tl.rank_genes_groups(
            adata,
            groupby=cluster_key,
            groups=testable,
            method=method,
            use_raw=False,  # `.raw` is never set here; X is the log-normalized matrix
            pts=True,       # expression fractions, which annotate_cells needs
        )
    except Exception as exc:  # noqa: BLE001 - a failed test is a finding, not a crash
        return _result(errors=[f"{method} ranking failed: {type(exc).__name__}: {exc}"], warnings=warnings)

    try:
        table = sc.get.rank_genes_groups_df(adata, group=None)
    except Exception as exc:  # noqa: BLE001
        return _result(errors=[f"could not read the ranking back: {type(exc).__name__}: {exc}"], warnings=warnings)

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = out_dir / "markers.csv"
    table.to_csv(table_path, index=False)
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    n_reported = int(config.get("n_genes_reported", DEFAULT_N_GENES_REPORTED))
    top_markers: dict[str, list[dict[str, Any]]] = {}
    n_significant: dict[str, int] = {}
    for group in testable:
        rows = table[table["group"] == group]
        n_significant[group] = int((rows["pvals_adj"] < SIGNIFICANCE_ALPHA).sum())
        top_markers[group] = [
            {
                "gene": str(row["names"]),
                "logfoldchange": round(float(row["logfoldchanges"]), 3),
                "pval_adj": float(row["pvals_adj"]),
                "pct_in_cluster": round(float(row["pct_nz_group"]), 3),
                "pct_in_rest": round(float(row["pct_nz_reference"]), 3),
            }
            for _, row in rows.head(n_reported).iterrows()
        ]

    barren = [group for group, count in n_significant.items() if count == 0]
    if barren:
        notes.append(
            f"{len(barren)} cluster(s) have no gene below adjusted p {SIGNIFICANCE_ALPHA} "
            f"({barren}); they may be splits of a single population rather than distinct ones"
        )

    marker_summary = {
        "cluster_key": cluster_key,
        "method": method,
        "n_clusters_tested": len(testable),
        "clusters_excluded": excluded,
        "n_genes_tested": int(adata.n_vars),
        "n_significant_per_cluster": n_significant,
        "alpha": SIGNIFICANCE_ALPHA,
        "n_genes_reported": n_reported,
    }

    return _result(
        adata_path=adata_path,
        marker_table_path=str(table_path),
        top_markers=top_markers,
        marker_summary=marker_summary,
        warnings=warnings,
        notes=notes,
        next_tool="annotate_cells",
        metrics=marker_summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    marker_table_path: str | None = None,
    top_markers: dict[str, Any] | None = None,
    marker_summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "marker_table_path": marker_table_path,
        "top_markers": top_markers or {},
        "marker_summary": marker_summary or {},
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
    parser.add_argument("--cluster-key", default="leiden")
    parser.add_argument("--method", dest="marker_method")
    parser.add_argument("--n-genes-reported", type=int)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {"cluster_key": args.cluster_key}
    if args.marker_method is not None:
        config["marker_method"] = args.marker_method
    if args.n_genes_reported is not None:
        config["n_genes_reported"] = args.n_genes_reported

    result = run(
        {
            "artifacts": {"run_umap": {"adata_path": args.adata_path}},
            "run_dir": args.run_dir,
            "config": config,
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
