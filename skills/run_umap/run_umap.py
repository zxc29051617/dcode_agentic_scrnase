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

## Perplexity is bounded by cell count, checked before anything is embedded
`sklearn.manifold.TSNE` requires `0 < perplexity < n_samples` and raises
otherwise — on a small library the default of 30 can exceed the cell count
outright. Clamped here to `min(default, (n_obs - 1) // 3)`, the same rule of
thumb behind scikit-learn's own guidance, with a warning naming both numbers,
and the result is asserted to be strictly below `n_obs` before the call.

**Fewer than three cells is refused, not clamped.** The clamp has a floor of 2,
so two cells cannot produce a legal perplexity at all — the old code produced
`perplexity=2` for `n_obs=2` and let sklearn raise. The check happens before any
embedding runs, so `method="both"` on a two-cell object fails without first
computing and storing a UMAP nobody can use.

A perplexity that is not a finite positive number is refused for the same
reason: `float()` accepts `nan` and `inf`, and every comparison against a NaN is
False, so it slipped past each bound in turn.

Run standalone:
    python skills/run_umap/run_umap.py <adata.h5ad> --run-dir <out> [--method tsne|both]
"""

from __future__ import annotations

import argparse
import json
import math
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
    "config.dimensions",
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
VALID_DIMENSIONS = (2, 3)
DEFAULT_DIMENSIONS = (2,)

#: scikit-learn's own default, and the number most tutorials use unchanged.
DEFAULT_PERPLEXITY = 30

#: sklearn requires `0 < perplexity < n_obs`, and the clamp's floor is 2, so two
#: cells cannot produce a legal value at all. Refused up front rather than
#: discovered inside the call.
MIN_CELLS_FOR_TSNE = 3

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
UNINTEGRATED_NEIGHBORS_KEY = "neighbors_unintegrated"


def _requested_dimensions(config: dict[str, Any]) -> tuple[tuple[int, ...], str | None]:
    raw = config.get("dimensions", DEFAULT_DIMENSIONS)
    if raw == "both":
        raw = DEFAULT_DIMENSIONS + (3,)
    elif isinstance(raw, bool):
        return DEFAULT_DIMENSIONS, f"dimensions={raw!r} is not one of {VALID_DIMENSIONS}"
    elif isinstance(raw, int):
        raw = (raw,)
    elif isinstance(raw, str):
        try:
            raw = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
        except ValueError:
            return DEFAULT_DIMENSIONS, f"dimensions={raw!r} is not a list of integers"

    try:
        dimensions = tuple(dict.fromkeys(raw))
    except TypeError:
        return DEFAULT_DIMENSIONS, f"dimensions={raw!r} is not a list of integers"
    if not dimensions:
        return DEFAULT_DIMENSIONS, "dimensions must contain at least one value"
    invalid = tuple(dimension for dimension in dimensions if dimension not in VALID_DIMENSIONS)
    if invalid:
        return DEFAULT_DIMENSIONS, (
            f"dimensions={list(invalid)!r} is not one of {VALID_DIMENSIONS}"
        )
    return dimensions, None


def _embedding_key(method: str, dimensions: int, *, unintegrated: bool = False) -> str:
    suffix = "_3d" if dimensions == 3 else ""
    middle = "_unintegrated" if unintegrated else ""
    return f"X_{method}{middle}{suffix}"


def _computed_name(method: str, dimensions: int, *, unintegrated: bool = False) -> str:
    suffix = "_3d" if dimensions == 3 else ""
    middle = "_unintegrated" if unintegrated else ""
    return f"{method}{middle}{suffix}"


def _requested_perplexity(config: dict[str, Any]) -> tuple[float, str | None]:
    """The configured perplexity, or why it cannot be used.

    Returns `(value, None)` or `(default, problem)`. `float(config.get(...))`
    accepted anything `float()` would, so `nan` and `inf` reached the clamp —
    and every comparison against a NaN is False, so a NaN passed straight
    through every guard and into sklearn unchanged.
    """
    raw = config.get("perplexity", DEFAULT_PERPLEXITY)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_PERPLEXITY), f"perplexity={raw!r} is not a number"
    if not math.isfinite(value):
        return float(DEFAULT_PERPLEXITY), f"perplexity={raw!r} is not a finite number"
    if value <= 0:
        return float(DEFAULT_PERPLEXITY), f"perplexity={raw!r} must be greater than 0"
    return value, None


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


def _run_tsne(
    adata: Any,
    scanpy: Any,
    embedding_key: str,
    perplexity: float,
    random_state: int,
    dimensions: int,
) -> str:
    key = _embedding_key("tsne", dimensions)
    if dimensions == 2:
        scanpy.tl.tsne(
            adata,
            use_rep=embedding_key,
            perplexity=perplexity,
            random_state=random_state,
        )
        return key

    from sklearn.manifold import TSNE

    coordinates = TSNE(
        n_components=dimensions,
        perplexity=perplexity,
        random_state=random_state,
        metric="euclidean",
        early_exaggeration=12,
        learning_rate=1000,
    ).fit_transform(adata.obsm[embedding_key])
    adata.obsm[key] = coordinates
    adata.uns[key] = {
        "params": {
            "n_components": dimensions,
            "perplexity": perplexity,
            "random_state": random_state,
            "metric": "euclidean",
            "use_rep": embedding_key,
        }
    }
    return key


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
    dimensions, dimensions_problem = _requested_dimensions(config)
    if dimensions_problem is not None:
        return _result(errors=[dimensions_problem])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs < 2:
        return _result(errors=[f"{source} has {adata.n_obs} cell(s); needs at least 2"])

    # ---- t-SNE preconditions, checked before anything is embedded -----------
    # Both of these used to be discovered inside the t-SNE call, which on
    # `method="both"` meant a UMAP had already been computed and written into
    # the object before the run failed — a partial result for a request that
    # was never satisfiable.
    if method in ("tsne", "both"):
        if adata.n_obs < MIN_CELLS_FOR_TSNE:
            return _result(errors=[
                f"t-SNE needs at least {MIN_CELLS_FOR_TSNE} cells and {source} has "
                f"{adata.n_obs}: sklearn requires 0 < perplexity < n_obs, which cannot "
                f"be satisfied below {MIN_CELLS_FOR_TSNE}. Use --embedding-method umap "
                f"for an object this small"
            ])
        _, problem = _requested_perplexity(config)
        if problem is not None:
            return _result(errors=[problem])

    import scanpy as sc

    computed: list[str] = []

    if method in ("umap", "both"):
        if "neighbors" not in adata.uns:
            return _result(errors=[f"{source} has no neighbor graph; run_clustering must run first"])
        try:
            for dimension in dimensions:
                key = _embedding_key("umap", dimension)
                sc.tl.umap(
                    adata,
                    n_components=dimension,
                    key_added=None if dimension == 2 else key,
                    random_state=int(config.get("random_state", 0)),
                )
                computed.append(_computed_name("umap", dimension))
        except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
            return _result(errors=[f"UMAP failed: {type(exc).__name__}: {exc}"])

        # ---- the integration diagnostic ------------------------------------
        # Computed here rather than at report time on purpose: an embedding has
        # a seed, a neighbour count and a package version behind it, so
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
                for dimension in dimensions:
                    key = _embedding_key("umap", dimension, unintegrated=True)
                    sc.tl.umap(
                        adata,
                        neighbors_key=UNINTEGRATED_NEIGHBORS_KEY,
                        key_added=key,
                        n_components=dimension,
                        random_state=int(config.get("random_state", 0)),
                    )
                    computed.append(_computed_name("umap", dimension, unintegrated=True))
            except Exception as exc:  # noqa: BLE001 - losing a diagnostic must not lose the run
                warnings.append(
                    f"the pre-integration embedding could not be computed "
                    f"({type(exc).__name__}: {exc}); the before/after comparison "
                    "will be missing from the report"
                )

    if method in ("tsne", "both"):
        if embedding_key not in adata.obsm:
            return _result(errors=[f"{source} has no obsm['{embedding_key}']; run_integration must run first"])
        perplexity, _ = _requested_perplexity(config)
        bound = (adata.n_obs - 1) // 3
        if perplexity >= adata.n_obs or perplexity > bound:
            # `min(bound, n_obs - 1)` can be 0 or 1 on a small object, so the
            # floor of 2 used to be able to exceed n_obs; capping at n_obs - 1
            # after the floor is what keeps the result strictly below n_obs.
            clamped = min(max(2, min(bound, adata.n_obs - 1)), adata.n_obs - 1)
            warnings.append(
                f"perplexity={perplexity} requested but only {adata.n_obs} cells are present; "
                f"using {clamped} instead"
            )
            perplexity = float(clamped)

        # The invariant sklearn enforces, asserted here so a future change to the
        # clamp cannot quietly hand it an illegal value again.
        if not 0 < perplexity < adata.n_obs:
            return _result(errors=[
                f"could not choose a legal t-SNE perplexity for {adata.n_obs} cells "
                f"(got {perplexity}); sklearn requires 0 < perplexity < n_obs"
            ])
        try:
            for dimension in dimensions:
                _run_tsne(
                    adata,
                    sc,
                    embedding_key,
                    perplexity,
                    int(config.get("random_state", 0)),
                    dimension,
                )
                computed.append(_computed_name("tsne", dimension))
        except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
            return _result(errors=[f"t-SNE failed: {type(exc).__name__}: {exc}"])
        if adata.n_obs < MIN_CELLS_FOR_MEANINGFUL_TSNE:
            notes.append(
                f"only {adata.n_obs} cells; t-SNE structure is unreliable this small "
                "regardless of the perplexity used"
            )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    embedding_keys = {
        name: {
            str(dimension): (
                _embedding_key(name, dimension)
                if _computed_name(name, dimension) in computed
                else None
            )
            for dimension in dimensions
        }
        for name in ("umap", "tsne")
    }
    unintegrated_umap_keys = {
        str(dimension): (
            _embedding_key("umap", dimension, unintegrated=True)
            if _computed_name("umap", dimension, unintegrated=True) in computed
            else None
        )
        for dimension in dimensions
    }
    embedding_summary = {
        "method": method,
        "dimensions": list(dimensions),
        "computed": computed,
        "embeddings": [
            {
                "method": name,
                "dimensions": dimension,
                "key": embedding_keys[name].get(str(dimension)),
            }
            for name in embedding_keys
            for dimension in dimensions
            if embedding_keys[name].get(str(dimension)) is not None
        ],
        "embedding_keys": embedding_keys,
        "embedding_key": embedding_key,
        "random_state": int(config.get("random_state", 0)),
        "umap_key": embedding_keys["umap"].get("2"),
        "tsne_key": embedding_keys["tsne"].get("2"),
        "umap_3d_key": embedding_keys["umap"].get("3"),
        "tsne_3d_key": embedding_keys["tsne"].get("3"),
        # Present only when integration ran on more than one batch. The report
        # renders a before/after panel from the 2D key and says nothing about it
        # otherwise, rather than implying the comparison was possible.
        "unintegrated_umap_key": unintegrated_umap_keys.get("2"),
        "unintegrated_umap_3d_key": unintegrated_umap_keys.get("3"),
        "unintegrated_umap_keys": unintegrated_umap_keys,
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
    parser.add_argument(
        "--dimensions", nargs="+", type=int, choices=VALID_DIMENSIONS,
        default=list(DEFAULT_DIMENSIONS),
    )
    parser.add_argument("--embedding-key", default="X_pca")
    parser.add_argument("--perplexity", type=float)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {
        "method": args.method,
        "dimensions": args.dimensions,
        "embedding_key": args.embedding_key,
    }
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
