"""Leiden clustering on whichever embedding `run_integration` said to use.

Same shape as `run_pca`: resolution 1.0 is Scanpy's own default, not a value
borrowed from a specific tissue, so this step does not stop for a decision —
`resolution` is a config knob the operator can turn, not a threshold they must
supply before anything runs. It is also, in practice, the number worth
revisiting most: too low merges biologically distinct populations, too high
splits one population into noise, and there is no universal right answer for a
new tissue.

## Reads `embedding_key`, not a hardcoded obsm name
`run_integration` recorded which embedding downstream steps should use —
`X_pca_harmony` if batches were corrected, `X_pca` if there was nothing to
correct. Reading that field instead of assuming a name means this step is
correct whether or not integration actually ran, without needing to know why.

## flavor="igraph", not the default
`sc.tl.leiden`'s own default (`flavor="leidenalg"`) is scanpy's original
implementation; `flavor="igraph"` is the one scanpy's own docs now recommend —
faster, and deterministic given `n_iterations` and a fixed seed, where the
leidenalg path is not. Requires `directed=False`, since Leiden's modularity
optimization is defined on undirected graphs.

Run standalone:
    python skills/run_clustering/run_clustering.py <adata.h5ad> --run-dir <out>
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

TOOL_NAME = "run_clustering"
INPUT_FIELDS = (
    "artifacts.run_integration",
    "config.resolution",
    "config.n_neighbors",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "clustering_summary",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Scanpy's own default. Not tissue-specific — how finely the graph is cut,
#: not a biological cutoff. The number most worth revisiting on new data.
DEFAULT_RESOLUTION = 1.0

#: Scanpy's own default for the neighbor graph Leiden partitions.
DEFAULT_N_NEIGHBORS = 15

#: A resolution that produces only one cluster did not fail, but it is not
#: what clustering is for. Worth a note rather than passing through silently.
MIN_USEFUL_CLUSTERS = 2


def _resolve_adata_path(payload: dict[str, Any]) -> tuple[str | None, str]:
    """Return the AnnData path and the embedding key to cluster on.

    `config.embedding_key`, when given, always wins — it is how the standalone
    CLI points at a specific embedding without a `run_integration` artifact to
    read it from.
    """
    artifacts = payload.get("artifacts") or {}
    config = payload.get("config") or {}
    override = config.get("embedding_key")

    integration = artifacts.get("run_integration") or {}
    if integration.get("adata_path"):
        summary = integration.get("integration_summary") or {}
        return str(integration["adata_path"]), str(override or summary.get("embedding_key", "X_pca"))
    pca = artifacts.get("run_pca") or {}
    if pca.get("adata_path"):
        return str(pca["adata_path"]), str(override or "X_pca")
    return config.get("adata_path"), str(override or "X_pca")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source, embedding_key = _resolve_adata_path(payload)
    if not source:
        return _result(errors=["no AnnData path; run_integration must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if embedding_key not in adata.obsm:
        return _result(errors=[f"{source} has no obsm['{embedding_key}']; run_integration must run first"])
    if adata.n_obs < 2:
        return _result(errors=[f"{source} has {adata.n_obs} cell(s); clustering needs at least 2"])

    import scanpy as sc

    n_neighbors = int(config.get("n_neighbors", DEFAULT_N_NEIGHBORS))
    if n_neighbors >= adata.n_obs:
        warnings.append(
            f"n_neighbors={n_neighbors} requested but only {adata.n_obs} cells are present; "
            f"using {adata.n_obs - 1} instead"
        )
        n_neighbors = adata.n_obs - 1
    if n_neighbors < 1:
        return _result(errors=[f"too few cells ({adata.n_obs}) to build a neighbor graph"])

    try:
        sc.pp.neighbors(adata, use_rep=embedding_key, n_neighbors=n_neighbors)
    except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
        return _result(errors=[f"neighbor graph failed: {type(exc).__name__}: {exc}"])

    resolution = float(config.get("resolution", DEFAULT_RESOLUTION))
    try:
        sc.tl.leiden(
            adata,
            resolution=resolution,
            flavor="igraph",
            n_iterations=2,
            directed=False,
            random_state=int(config.get("random_state", 0)),
        )
    except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
        return _result(errors=[f"Leiden failed: {type(exc).__name__}: {exc}"])

    sizes = adata.obs["leiden"].value_counts().sort_index()
    n_clusters = int(sizes.shape[0])
    if n_clusters < MIN_USEFUL_CLUSTERS:
        notes.append(
            f"resolution={resolution} produced {n_clusters} cluster(s); raise resolution "
            "if finer structure is expected"
        )
    smallest = int(sizes.min()) if n_clusters else 0
    if n_clusters >= MIN_USEFUL_CLUSTERS and smallest < 10:
        notes.append(f"the smallest cluster has only {smallest} cells; may be noise rather than a population")

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    clustering_summary = {
        "embedding_key": embedding_key,
        "n_neighbors": n_neighbors,
        "resolution": resolution,
        "random_state": int(config.get("random_state", 0)),
        "n_clusters": n_clusters,
        "cluster_sizes": {str(k): int(v) for k, v in sizes.items()},
        "smallest_cluster": smallest,
        "largest_cluster": int(sizes.max()) if n_clusters else 0,
    }

    return _result(
        adata_path=adata_path,
        clustering_summary=clustering_summary,
        warnings=warnings,
        notes=notes,
        next_tool="run_umap",
        metrics=clustering_summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    clustering_summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "clustering_summary": clustering_summary or {},
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
    parser.add_argument("--embedding-key", default="X_pca")
    parser.add_argument("--resolution", type=float)
    parser.add_argument("--n-neighbors", type=int)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {"embedding_key": args.embedding_key}
    if args.resolution is not None:
        config["resolution"] = args.resolution
    if args.n_neighbors is not None:
        config["n_neighbors"] = args.n_neighbors

    result = run(
        {
            "artifacts": {"run_pca": {"adata_path": args.adata_path}},
            "run_dir": args.run_dir,
            "config": config,
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
