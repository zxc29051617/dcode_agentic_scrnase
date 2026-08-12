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

from src import manifest, matrix_io  # noqa: E402

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

#: The only obs column Harmony may ever correct on. It exists only when a
#: validated `--sample-manifest` declared it, which is the point: a batch is
#: something somebody wrote down, not something inferred from a filename.
TECHNICAL_BATCH_KEY = "technical_batch"

#: `none` and `harmony` only. There is deliberately no `auto`: choosing for the
#: operator is what this step used to do, and what it got wrong.
INTEGRATION_MODES = ("none", "harmony")

#: Kept so the standalone CLI and older callers still resolve the name, but no
#: longer a default anything falls back to.
DEFAULT_BATCH_KEY = TECHNICAL_BATCH_KEY


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


def _requested_mode(config: dict[str, Any]) -> tuple[str | None, str, str | None]:
    """`(mode, where it came from, why it is unusable)`.

    `None` is not a default — it means nobody answered, which is a different
    state from an operator choosing `none` and has to stay distinguishable in
    provenance. An unrecognised value is refused rather than coerced.
    """
    raw = config.get("integration_mode")
    if raw is None:
        return None, "unanswered", None
    mode = str(raw).strip().lower()
    if mode not in INTEGRATION_MODES:
        return None, "unanswered", (
            f"integration_mode={raw!r} is not one of {', '.join(INTEGRATION_MODES)}"
        )
    return mode, "operator", None


def _library_names(adata: Any) -> list[str]:
    """The libraries in the object, by whichever column records them."""
    for key in ("library_id", "sample"):
        if key in adata.obs:
            return sorted(str(v) for v in adata.obs[key].unique())
    return []


def _per_library(adata: Any, column: str) -> dict[str, str | None]:
    """`{library_id: value}` — the design as the object actually carries it."""
    key = "library_id" if "library_id" in adata.obs else "sample"
    mapping: dict[str, str | None] = {}
    if key not in adata.obs or column not in adata.obs:
        return mapping
    frame = adata.obs[[key, column]].astype(str)
    for library, value in zip(frame[key], frame[column]):
        text = value.strip()
        mapping.setdefault(library, None if text in ("", "nan", "None") else text)
    return mapping


def _confounding(adata: Any, batch_key: str) -> dict[str, Any]:
    """Whether the condition can be told apart from the batch, structurally.

    Asked of the object rather than of the manifest, because the object is what
    Harmony is about to be run on. Counts of libraries only; no id reaches the
    result, which is what lets it be reported.
    """
    return manifest.confounding_from_columns(
        _per_library(adata, "condition"),
        _per_library(adata, batch_key),
        biological="condition",
        technical=batch_key,
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

    force = bool(config.get("force_integration", False))
    random_state = int(config.get("random_state", 0))

    mode, mode_source, problem = _requested_mode(config)
    if problem is not None:
        return _result(errors=[problem])

    libraries = _library_names(adata)
    skip = dict(
        payload=payload, adata=adata, integrated=False, batch_key=None,
        random_state=random_state, mode=mode, mode_source=mode_source,
    )

    # ---- nobody has said what the batches are ------------------------------
    # The old default was `batch_key="sample"`, so this branch used to run
    # Harmony on the library name. That name comes from a FASTQ filename, and
    # correcting on it removes whatever the libraries actually differ by —
    # including the disease being studied. Skipping is the only answer that
    # cannot be wrong without saying so.
    if mode is None:
        if len(libraries) < 2:
            notes.append(
                "one library, so there is no between-library difference to correct; "
                "using X_pca as-is"
            )
            return _finish(notes=notes, warnings=warnings, **skip)
        warnings.append(
            f"{len(libraries)} libraries are present ({', '.join(libraries)}) but no "
            f"integration mode was chosen, so X_pca is used uncorrected. A library is "
            f"not automatically a technical batch: libraries usually differ by donor "
            f"and condition too, and correcting on the library would remove those "
            f"along with any batch effect. To integrate, supply --sample-manifest "
            f"with a {TECHNICAL_BATCH_KEY} column and --integration-mode harmony; to "
            f"record that no correction is wanted, pass --integration-mode none"
        )
        return _finish(notes=notes, warnings=warnings, **skip)

    # ---- the operator said not to ------------------------------------------
    if mode == "none":
        notes.append(
            "integration mode 'none' was chosen, so X_pca is used uncorrected"
        )
        return _finish(notes=notes, warnings=warnings, **skip)

    # ---- harmony: only ever on the declared technical batch -----------------
    requested_key = config.get("batch_key")
    if requested_key is not None and str(requested_key) != TECHNICAL_BATCH_KEY:
        return _result(errors=[
            f"batch_key={str(requested_key)!r} was requested, but Harmony corrects only "
            f"on {TECHNICAL_BATCH_KEY!r}. Library, sample, donor and condition are "
            f"differences worth keeping, not batch effects to remove"
        ])

    batch_key = TECHNICAL_BATCH_KEY
    if batch_key not in adata.obs:
        return _result(errors=[
            f"integration mode 'harmony' needs obs['{batch_key}'], which comes from a "
            f"validated --sample-manifest. Without it there is no declared technical "
            f"batch, and nothing else in the object may stand in for one"
        ])

    batch_counts = adata.obs[batch_key].value_counts()
    batch_counts = batch_counts[batch_counts > 0]
    n_batches = int(batch_counts.shape[0])

    if n_batches < 2:
        notes.append(
            f"only one value in obs['{batch_key}'] ({batch_counts.index[0]!r}); there is "
            "one technical batch, so there is nothing to correct against and X_pca is "
            "used as-is"
        )
        return _finish(
            notes=notes, warnings=warnings,
            **{**skip, "batch_key": batch_key, "n_batches": n_batches,
               "batch_counts": batch_counts},
        )

    report = _confounding(adata, batch_key)
    if report.get("fully_confounded"):
        message = (
            f"{report['biological_key']} and {batch_key} are fully confounded "
            f"({report['n_components']} disconnected groups): every batch holds a single "
            f"condition, so the two differences enter the data identically and removing "
            f"the batch removes the condition. Harmony cannot separate what the design "
            f"did not separate. Contingency table (libraries): {report['table']}"
        )
        if not force:
            warnings.append(message + ". Using X_pca as-is; this needs a person to decide")
            return _finish(
                notes=notes, warnings=warnings,
                **{**skip, "batch_key": batch_key, "n_batches": n_batches,
                   "batch_counts": batch_counts, "confounding": report},
            )
        warnings.append(
            message + ". force_integration was set, so it ran anyway and the corrected "
            "embedding no longer carries the condition difference"
        )
    elif not report.get("balanced") and report.get("n_conditions", 0) > 1:
        # Reported, never acted on. There is no defensible cutoff at which an
        # unbalanced-but-estimable design stops being the operator's call.
        warnings.append(
            f"{report['biological_key']} is unevenly spread across {batch_key} but "
            f"remains separable, so integration proceeds. Contingency table "
            f"(libraries): {report['table']}"
        )

    small = batch_counts[batch_counts < MIN_CELLS_PER_BATCH]
    if len(small) and not force:
        warnings.append(
            f"{len(small)} of {n_batches} batches have fewer than {MIN_CELLS_PER_BATCH} cells "
            f"({dict(small)}); Harmony's per-batch fit is not reliable there. Using X_pca "
            "as-is rather than an integration nobody should trust"
        )
        return _finish(
            notes=notes, warnings=warnings,
            **{**skip, "batch_key": batch_key, "n_batches": n_batches,
               "batch_counts": batch_counts, "confounding": report},
        )

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
        random_state=random_state, mode=mode, mode_source=mode_source, confounding=report,
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
    mode: str | None = None,
    mode_source: str = "unanswered",
    confounding: dict[str, Any] | None = None,
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
        # `integration_mode` is what was asked for; `mode_source` says whether
        # anyone asked. A run that skipped because nobody chose and a run that
        # skipped because the operator chose `none` are not the same run.
        "integration_mode": mode,
        "mode_source": mode_source,
        "confounding": confounding or {},
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
    parser.add_argument("--batch-key", default=DEFAULT_BATCH_KEY,
                        help=f"only {TECHNICAL_BATCH_KEY!r} is accepted")
    parser.add_argument("--integration-mode", choices=list(INTEGRATION_MODES), default=None,
                        help="left unset, nothing is corrected and the reason is stated")
    parser.add_argument("--force", action="store_true",
                        help="waive the batch-size and confounding checks")
    parser.add_argument("--max-iter-harmony", type=int)
    args = parser.parse_args(argv)

    config: dict[str, Any] = {"batch_key": args.batch_key}
    if args.integration_mode is not None:
        config["integration_mode"] = args.integration_mode
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
