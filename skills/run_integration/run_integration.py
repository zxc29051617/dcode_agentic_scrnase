"""Batch-correct the PCA embedding with Harmony, only when there is a batch to correct.

The first mainline step that decides *whether* to run, not just what
parameters to run with. `apply_cell_qc_filter` and `cell_calling_review` stop
because the threshold is a judgment call nobody but the operator can make;
this step has the opposite shape — whether a single library needs correcting
against itself is not a judgment call, it is a fact readable from the object.

## One sample: nothing to integrate
Harmony corrects an embedding so cells cluster by biology instead of by which
library they came from. With one library there is no "which library" to
correct for — running it anyway would not error, but it would spend time
adjusting an embedding against noise and call the result "integrated" when
nothing was. This step recognizes that and skips, leaving `X_pca` as the
embedding downstream steps should use.

## Multiple samples: Harmony on X_pca, not on raw expression
`sc.external.pp.harmony_integrate` adjusts the *embedding* (`X_pca` ->
`X_pca_harmony`), not the expression matrix itself — the corrected values
never touch `X`, so nothing later that reads expression (`find_markers`,
differential testing) is silently working on batch-corrected numbers it did
not ask for. Only steps that consume an embedding (`run_clustering`,
`run_umap`) are meant to read `X_pca_harmony`.

## A batch too small to correct is skipped, not crashed
Harmony fits a clustering-like procedure per batch internally; a batch with a
handful of cells does not carry enough signal for that fit to be meaningful,
and degenerate input has already been the cause of a hard crash rather than a
clean exception once in this pipeline (see `normalize_hvg_prepare`'s
`MIN_GENES_FOR_SEURAT_V3`). The same caution applies here: batch sizes are
checked before calling Harmony, not caught after.

Run standalone:
    python skills/run_integration/run_integration.py <adata.h5ad> --run-dir <out>
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

TOOL_NAME = "run_integration"
INPUT_FIELDS = (
    "artifacts.run_pca",
    "config.batch_key",
    "config.force_integration",
    "config.max_iter_harmony",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "integration_summary",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Harmony fits a per-batch clustering step internally. Below this many cells a
#: batch cannot support that fit meaningfully — the same instinct as
#: `detect_doublets`'s `MIN_CELLS_FOR_SCRUBLET`: an honest skip beats a number
#: (or, worse, a crash) nobody should trust.
MIN_CELLS_PER_BATCH = 20

DEFAULT_BATCH_KEY = "sample"


def _run_harmony(
    embedding: Any, obs_batch: Any, batch_key: str, *, max_iter_harmony: int, random_state: int
) -> Any:
    """Call `harmonypy` directly rather than `scanpy.external.pp.harmony_integrate`.

    That wrapper does `harmony_out.Z_corr.T` unconditionally, which assumes
    `Z_corr` comes back shaped `(n_pcs, n_obs)`. It does in harmonypy 0.0.10,
    which is what the wrapper was written against — but every release from
    0.1.0 onward, including the current 2.0.0, returns `(n_obs, n_pcs)`
    instead. The transpose then produces `(n_pcs, n_obs)`, and assigning that
    into `obsm` fails a shape check that has nothing to do with the data.
    Rather than pin an increasingly old dependency to match an assumption
    baked into scanpy's wrapper, this checks which axis actually matches
    `n_obs` and orients accordingly — correct against whichever version
    `harmonypy` happens to be.
    """
    import numpy as np
    import harmonypy

    n_obs = embedding.shape[0]
    result = harmonypy.run_harmony(
        embedding, obs_batch, [batch_key],
        max_iter_harmony=max_iter_harmony, random_state=random_state,
    )
    corrected = np.asarray(result.Z_corr)
    if corrected.shape[0] == n_obs:
        return corrected
    if corrected.shape[1] == n_obs:
        return corrected.T
    raise ValueError(
        f"harmonypy returned Z_corr with shape {corrected.shape}; neither axis "
        f"matches {n_obs} cells"
    )


def _resolve_adata_path(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    for step in ("run_pca", "normalize_hvg_prepare"):
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
        return _result(errors=["no AnnData path; run_pca must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if "X_pca" not in adata.obsm:
        return _result(errors=[f"{source} has no obsm['X_pca']; run_pca must run first"])

    batch_key = str(config.get("batch_key", DEFAULT_BATCH_KEY))
    force = bool(config.get("force_integration", False))
    random_state = int(config.get("random_state", 0))

    if batch_key not in adata.obs:
        if force:
            return _result(errors=[f"force_integration requested but obs['{batch_key}'] is absent"])
        notes.append(f"no obs['{batch_key}']; nothing to integrate against, using X_pca as-is")
        return _finish(payload, adata, integrated=False, batch_key=None, notes=notes, warnings=warnings, random_state=random_state)

    batch_counts = adata.obs[batch_key].value_counts()
    n_batches = int(batch_counts.shape[0])

    if n_batches < 2 and not force:
        notes.append(
            f"only one value in obs['{batch_key}'] ({batch_counts.index[0]!r}); "
            "nothing to integrate against, using X_pca as-is"
        )
        return _finish(payload, adata, integrated=False, batch_key=batch_key, notes=notes, warnings=warnings, random_state=random_state)

    small = batch_counts[batch_counts < MIN_CELLS_PER_BATCH]
    if len(small) and not force:
        warnings.append(
            f"{len(small)} of {n_batches} batches have fewer than {MIN_CELLS_PER_BATCH} cells "
            f"({dict(small)}); Harmony's per-batch fit is not reliable there. Using X_pca "
            "as-is rather than an integration nobody should trust"
        )
        return _finish(payload, adata, integrated=False, batch_key=batch_key, notes=notes, warnings=warnings, random_state=random_state)

    try:
        adata.obsm["X_pca_harmony"] = _run_harmony(
            adata.obsm["X_pca"],
            adata.obs[[batch_key]],
            batch_key,
            max_iter_harmony=int(config.get("max_iter_harmony", 10)),
            random_state=random_state,
        )
    except Exception as exc:  # noqa: BLE001 - a failed fit is a finding, not a crash
        return _result(errors=[f"Harmony failed: {type(exc).__name__}: {exc}"])

    return _finish(
        payload, adata, integrated=True, batch_key=batch_key,
        n_batches=n_batches, batch_counts=batch_counts, notes=notes, warnings=warnings,
        random_state=random_state,
    )


def _finish(
    payload: dict[str, Any],
    adata: Any,
    *,
    integrated: bool,
    batch_key: str | None,
    random_state: int,
    n_batches: int | None = None,
    batch_counts: Any = None,
    notes: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    integration_summary = {
        "integrated": integrated,
        "batch_key": batch_key,
        "n_batches": (
            n_batches if n_batches is not None
            else (int(adata.obs[batch_key].nunique()) if batch_key else None)
        ),
        "batch_sizes": {str(k): int(v) for k, v in batch_counts.items()} if batch_counts is not None else {},
        "embedding_key": "X_pca_harmony" if integrated else "X_pca",
        "random_state": random_state,
        "method": "harmony" if integrated else None,
    }

    return _result(
        adata_path=adata_path,
        integration_summary=integration_summary,
        warnings=warnings,
        notes=notes,
        next_tool="run_clustering",
        metrics=integration_summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    integration_summary: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "integration_summary": integration_summary or {},
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
    parser.add_argument("--batch-key", default=DEFAULT_BATCH_KEY)
    parser.add_argument("--force", action="store_true", help="run Harmony even with one batch")
    parser.add_argument("--max-iter-harmony", type=int)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {"batch_key": args.batch_key}
    if args.force:
        config["force_integration"] = True
    if args.max_iter_harmony is not None:
        config["max_iter_harmony"] = args.max_iter_harmony

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
