"""2D coordinates for visual inspection: UMAP, t-SNE, or both.

Same shape as `run_clustering`: Scanpy's own defaults, not values borrowed
from a specific tissue, so this step does not stop for a decision.
`config.method` picks the algorithm — `"umap"` (default), `"tsne"`, or
`"both"` — and either can be re-run with different parameters without
recomputing the other.

## UMAP reads the neighbor graph `run_clustering` already built
`sc.tl.umap` operates on the neighbor graph in `adata.uns["neighbors"]`, not
directly on an embedding — the same graph Leiden partitioned. Recomputing it
here would risk it drifting from the one clustering actually used, so this
step requires it to already exist rather than building its own.

## t-SNE reads `embedding_key` directly, the same field run_clustering reads
`sc.tl.tsne` does not use a neighbor graph; it embeds straight from
`use_rep`. This step reads the same `embedding_key` `run_integration` recorded
(`X_pca_harmony` if batches were corrected, `X_pca` otherwise), so a t-SNE
run and a Leiden run never silently look at different representations of the
data.

## Perplexity is bounded by cell count, checked before the call
`sklearn.manifold.TSNE` requires `perplexity < n_samples` and raises
otherwise — on a small library the default of 30 can exceed the cell count
outright. Clamped here to `min(default, (n_obs - 1) // 3)`, the same rule of
thumb behind scikit-learn's own guidance, with a warning naming both numbers.

Run standalone:
    python skills/run_umap/run_umap.py <adata.h5ad> --run-dir <out> [--method tsne|both]
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

TOOL_NAME = "run_umap"
INPUT_FIELDS = (
    "artifacts.run_clustering",
    "config.method",
    "config.perplexity",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "embedding_summary",
    "warnings",
    "errors",
    "recommended_next_tool",
)

VALID_METHODS = ("umap", "tsne", "both")
DEFAULT_METHOD = "umap"

#: scikit-learn's own default, and the number most tutorials use unchanged.
DEFAULT_PERPLEXITY = 30

#: Below this many cells, t-SNE offers little over a scatter of the raw
#: embedding; the clamp below already keeps it from erroring, this just
#: says so.
MIN_CELLS_FOR_MEANINGFUL_TSNE = 30

#: Scanpy's own default, and what `run_clustering` uses when nothing says
#: otherwise. Only used as a fallback for the diagnostic embedding.
DEFAULT_N_NEIGHBORS = 15

#: The pre-integration embedding, and the neighbour graph behind it. Kept
#: under their own keys so the mainline `neighbors`/`X_umap` that clustering
#: and the report depend on are never overwritten.
UNINTEGRATED_UMAP_KEY = "X_umap_unintegrated"
UNINTEGRATED_NEIGHBORS_KEY = "neighbors_unintegrated"


def _should_embed_unintegrated(payload: dict[str, Any], adata: Any, embedding_key: str) -> bool:
    """Only when there is a correction to show, and something to show it against.

    Three conditions, all necessary: integration actually ran, the uncorrected
    representation is still on the object, and there is more than one batch. A
    before/after picture of a single library compares an embedding to itself.
    """
    artifacts = payload.get("artifacts") or {}
    summary = (artifacts.get("run_integration") or {}).get("integration_summary") or {}
    integrated = summary.get("integrated")
    if integrated is None:
        # Standalone use, with no artifact to read: infer from the embedding.
        integrated = embedding_key != "X_pca"
    if not integrated or "X_pca" not in adata.obsm:
        return False
    batch_key = summary.get("batch_key") or "sample"
    return batch_key in adata.obs and adata.obs[batch_key].nunique() > 1


def _neighbors_used(payload: dict[str, Any]) -> int:
    """Match the neighbour count clustering used, so only the input differs."""
    artifacts = payload.get("artifacts") or {}
    summary = (artifacts.get("run_clustering") or {}).get("clustering_summary") or {}
    return int(summary.get("n_neighbors") or DEFAULT_N_NEIGHBORS)


def _resolve(payload: dict[str, Any]) -> tuple[str | None, str]:
    """Return the AnnData path and the embedding key to read for t-SNE."""
    artifacts = payload.get("artifacts") or {}
    config = payload.get("config") or {}
    override = config.get("embedding_key")

    clustering = artifacts.get("run_clustering") or {}
    if clustering.get("adata_path"):
        summary = clustering.get("clustering_summary") or {}
        return str(clustering["adata_path"]), str(override or summary.get("embedding_key", "X_pca"))
    integration = artifacts.get("run_integration") or {}
    if integration.get("adata_path"):
        summary = integration.get("integration_summary") or {}
        return str(integration["adata_path"]), str(override or summary.get("embedding_key", "X_pca"))
    return config.get("adata_path"), str(override or "X_pca")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source, embedding_key = _resolve(payload)
    if not source:
        return _result(errors=["no AnnData path; run_clustering must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    method = str(config.get("method", DEFAULT_METHOD)).lower()
    if method not in VALID_METHODS:
        return _result(errors=[f"method={method!r} is not one of {VALID_METHODS}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs < 2:
        return _result(errors=[f"{source} has {adata.n_obs} cell(s); needs at least 2"])

    import scanpy as sc

    computed: list[str] = []

    if method in ("umap", "both"):
        if "neighbors" not in adata.uns:
            return _result(errors=[f"{source} has no neighbor graph; run_clustering must run first"])
        try:
            sc.tl.umap(adata, random_state=int(config.get("random_state", 0)))
        except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
            return _result(errors=[f"UMAP failed: {type(exc).__name__}: {exc}"])
        computed.append("umap")

        # ---- the integration diagnostic ------------------------------------
        # Computed here rather than at report time on purpose: a UMAP has a
        # seed, a neighbour count and a package version behind it, so
        # recomputing it later would produce a figure that no longer matches
        # the run it claims to describe. It is a diagnostic, not a proof —
        # it shows whether libraries mix, and cannot by itself distinguish
        # correction from over-correction.
        if _should_embed_unintegrated(payload, adata, embedding_key):
            try:
                sc.pp.neighbors(
                    adata,
                    use_rep="X_pca",
                    n_neighbors=_neighbors_used(payload),
                    key_added=UNINTEGRATED_NEIGHBORS_KEY,
                )
                sc.tl.umap(
                    adata,
                    neighbors_key=UNINTEGRATED_NEIGHBORS_KEY,
                    key_added=UNINTEGRATED_UMAP_KEY,
                    random_state=int(config.get("random_state", 0)),
                )
                computed.append("umap_unintegrated")
            except Exception as exc:  # noqa: BLE001 - losing a diagnostic must not lose the run
                warnings.append(
                    f"the pre-integration embedding could not be computed "
                    f"({type(exc).__name__}: {exc}); the before/after comparison "
                    "will be missing from the report"
                )

    if method in ("tsne", "both"):
        if embedding_key not in adata.obsm:
            return _result(errors=[f"{source} has no obsm['{embedding_key}']; run_integration must run first"])
        perplexity = float(config.get("perplexity", DEFAULT_PERPLEXITY))
        bound = (adata.n_obs - 1) // 3
        if perplexity >= adata.n_obs or perplexity > bound:
            clamped = max(2, min(bound, adata.n_obs - 1))
            warnings.append(
                f"perplexity={perplexity} requested but only {adata.n_obs} cells are present; "
                f"using {clamped} instead"
            )
            perplexity = clamped
        try:
            sc.tl.tsne(
                adata,
                use_rep=embedding_key,
                perplexity=perplexity,
                random_state=int(config.get("random_state", 0)),
            )
        except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
            return _result(errors=[f"t-SNE failed: {type(exc).__name__}: {exc}"])
        computed.append("tsne")
        if adata.n_obs < MIN_CELLS_FOR_MEANINGFUL_TSNE:
            notes.append(
                f"only {adata.n_obs} cells; t-SNE structure is unreliable this small "
                "regardless of the perplexity used"
            )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    embedding_summary = {
        "method": method,
        "computed": computed,
        "embedding_key": embedding_key,
        "random_state": int(config.get("random_state", 0)),
        "umap_key": "X_umap" if "umap" in computed else None,
        "tsne_key": "X_tsne" if "tsne" in computed else None,
        # Present only when integration ran on more than one batch. The report
        # renders a before/after panel from this and says nothing about it
        # otherwise, rather than implying the comparison was possible.
        "unintegrated_umap_key": (
            UNINTEGRATED_UMAP_KEY if "umap_unintegrated" in computed else None
        ),
    }

    return _result(
        adata_path=adata_path,
        embedding_summary=embedding_summary,
        warnings=warnings,
        notes=notes,
        next_tool="find_markers",
        metrics=embedding_summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    embedding_summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "embedding_summary": embedding_summary or {},
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
    parser.add_argument("--method", choices=VALID_METHODS, default=DEFAULT_METHOD)
    parser.add_argument("--embedding-key", default="X_pca")
    parser.add_argument("--perplexity", type=float)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {"method": args.method, "embedding_key": args.embedding_key}
    if args.perplexity is not None:
        config["perplexity"] = args.perplexity

    result = run(
        {
            "artifacts": {"run_clustering": {"adata_path": args.adata_path}},
            "run_dir": args.run_dir,
            "config": config,
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
