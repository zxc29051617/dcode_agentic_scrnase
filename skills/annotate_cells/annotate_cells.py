"""Name each cluster's cell type with CellTypist, and show how confident it was.

## The model is a decision, not a default
CellTypist ships 61 models, each trained on a particular tissue and species.
Handing an immune model a mouse brain does not error — it confidently sorts
neurons into T cells and monocytes. That failure looks exactly like a
successful run, which puts it in the same category as the species mismatch
`resolve_reference` refuses to guess at.

So with no `celltypist_model` in config this step **annotates nothing**. It
reports the candidate models and their descriptions as evidence and stops at
the human gate, the same shape as `apply_cell_qc_filter` reporting what each
threshold would cost. That evidence is also exactly what an advisor model
needs in order to argue for one model over another.

## Expression is rebuilt at 10,000 counts, not reused from X
CellTypist requires log1p expression normalized to 10,000 counts per cell, and
checks it: `classifier.py` warns `invalid expression matrix, expect ALL genes
and log1p normalized expression to 10000 counts per cell. The prediction
result may not be accurate` — and then returns normal-looking predictions
anyway.

`normalize_hvg_prepare` normalizes to median depth, which on the real PBMC
object is 6,780, not 10,000. Passing `X` straight through would trip that
warning and quietly degrade every label. This step therefore rebuilds
expression from `layers["counts"]` at `target_sum=1e4` into a throwaway object
and leaves the mainline `X` untouched — the reason `post_load_validate`
insists on keeping raw counts in a layer.

## Majority voting runs over our clusters, not CellTypist's own
CellTypist predicts per cell, then smooths those predictions by majority vote
within an over-clustering. Left to itself it builds its own graph at
resolution 5; pointed at `obs["leiden"]` it votes within the clusters the rest
of the pipeline already uses. That keeps one label per cluster, directly
comparable to `find_markers`' per-cluster table and to the UMAP the report
shows — the difference between an answer you can defend and one you can only
display.

Run standalone:
    python skills/annotate_cells/annotate_cells.py <adata.h5ad> --run-dir <out> \
        --model Immune_All_Low.pkl
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

TOOL_NAME = "annotate_cells"
INPUT_FIELDS = (
    "artifacts.find_markers",
    "config.celltypist_model",
    "config.cluster_key",
    "config.majority_voting",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "annotation_state",
    "annotation_summary",
    "per_cluster",
    "figure_paths",
    "evidence",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: What CellTypist's own check demands. Anything else trips its warning and
#: silently degrades the labels.
CELLTYPIST_TARGET_SUM = 1e4

#: Below this median confidence a cluster's label is worth a second look — the
#: model produced an answer, but not one to quote without checking the markers.
LOW_CONFIDENCE_MEDIAN = 0.5

#: Consensus below this means the cells in one cluster disagreed about their
#: own identity, which usually means the cluster merges two populations.
LOW_CONSENSUS_FRACTION = 0.7


def _resolve(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    for step in ("find_markers", "run_umap", "run_clustering"):
        path = (artifacts.get(step) or {}).get("adata_path")
        if path:
            return str(path)
    return (payload.get("config") or {}).get("adata_path")


def _model_catalogue() -> dict[str, Any]:
    """Available CellTypist models, for an operator or advisor to choose from."""
    try:
        from celltypist import models
    except Exception as exc:  # noqa: BLE001 - report rather than raise
        return {"error": f"celltypist unavailable: {type(exc).__name__}: {exc}"}

    catalogue: dict[str, Any] = {}
    try:
        catalogue["downloaded"] = list(models.get_all_models())
    except Exception:  # noqa: BLE001 - a missing cache is not fatal
        catalogue["downloaded"] = []
    try:
        described = models.models_description()
        catalogue["available"] = [
            {"model": str(row["model"]), "description": str(row["description"])}
            for _, row in described.iterrows()
        ]
    except Exception as exc:  # noqa: BLE001 - offline is a finding, not a crash
        catalogue["available_error"] = f"{type(exc).__name__}: {exc}"
    return catalogue


def _plot(adata: Any, out_dir: Path, cluster_key: str) -> dict[str, str]:
    """Confidence and labels on every embedding that exists.

    The confidence panel is the point: a label alone cannot be argued with,
    but a label next to the confidence behind it can.
    """
    import matplotlib

    matplotlib.use("Agg")  # no display in a pipeline
    import matplotlib.pyplot as plt
    import scanpy as sc

    figures: dict[str, str] = {}
    for basis, key in (("umap", "X_umap"), ("tsne", "X_tsne")):
        if key not in adata.obsm:
            continue
        colours = [c for c in ("cell_type", cluster_key, "conf_score") if c in adata.obs]
        try:
            axes = sc.pl.embedding(
                adata, basis=key, color=colours, show=False,
                wspace=0.35, ncols=len(colours),
            )
            figure = (axes[0] if isinstance(axes, list) else axes).get_figure()
            path = out_dir / f"annotation_{basis}.png"
            figure.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(figure)
            figures[basis] = str(path)
        except Exception:  # noqa: BLE001 - a missing plot must not lose the labels
            plt.close("all")
    return figures


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source = _resolve(payload)
    if not source:
        return _result(errors=["no AnnData path; find_markers must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    model_name = config.get("celltypist_model")
    if not model_name:
        # No output at all without a model choice, so report the candidates and
        # stop rather than picking one on the operator's behalf.
        return _result(
            annotation_state="needs_review",
            evidence={"models": _model_catalogue()},
            warnings=[
                "no celltypist_model chosen, so nothing was annotated. A model trained on "
                "the wrong tissue or species does not fail, it returns confident wrong "
                "labels — pick one from evidence.models and re-run"
            ],
        )

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if "counts" not in adata.layers:
        return _result(errors=[f"{source} has no layers['counts']; post_load_validate must run first"])

    cluster_key = str(config.get("cluster_key", "leiden"))
    if cluster_key not in adata.obs:
        return _result(errors=[f"{source} has no obs['{cluster_key}']; run_clustering must run first"])

    import anndata
    import numpy as np
    import scanpy as sc

    # CellTypist checks for 10,000-count log1p expression and degrades quietly
    # otherwise; the mainline X is normalized to median depth, so rebuild.
    scratch = anndata.AnnData(
        adata.layers["counts"].copy(), obs=adata.obs.copy(), var=adata.var.copy()
    )
    sc.pp.normalize_total(scratch, target_sum=CELLTYPIST_TARGET_SUM)
    sc.pp.log1p(scratch)

    majority_voting = bool(config.get("majority_voting", True))
    try:
        import celltypist

        result = celltypist.annotate(
            scratch,
            model=str(model_name),
            majority_voting=majority_voting,
            over_clustering=cluster_key if majority_voting else None,
        )
    except Exception as exc:  # noqa: BLE001 - a failed model load is a finding
        return _result(
            errors=[f"CellTypist failed with model {model_name!r}: {type(exc).__name__}: {exc}"],
            evidence={"models": _model_catalogue()},
        )

    annotated = result.to_adata()
    label_key = "majority_voting" if majority_voting and "majority_voting" in annotated.obs else "predicted_labels"
    adata.obs["cell_type"] = annotated.obs[label_key].astype(str).values
    adata.obs["cell_type_per_cell"] = annotated.obs["predicted_labels"].astype(str).values
    adata.obs["conf_score"] = np.asarray(annotated.obs["conf_score"], dtype=float)

    # ---- per cluster: what it was called, how sure, how unanimous -----------
    per_cluster: dict[str, Any] = {}
    low_confidence: list[str] = []
    low_consensus: list[str] = []
    frame = adata.obs
    for cluster in sorted(frame[cluster_key].astype(str).unique(), key=lambda v: (len(v), v)):
        rows = frame[frame[cluster_key].astype(str) == cluster]
        counts = rows["cell_type_per_cell"].value_counts()
        consensus = float(counts.iloc[0] / len(rows)) if len(rows) else 0.0
        median_conf = float(np.median(rows["conf_score"])) if len(rows) else 0.0
        entry = {
            "n_cells": int(len(rows)),
            "cell_type": str(rows["cell_type"].iloc[0]) if len(rows) else None,
            "median_conf_score": round(median_conf, 3),
            "per_cell_consensus": round(consensus, 3),
            "runner_up": str(counts.index[1]) if len(counts) > 1 else None,
        }
        per_cluster[cluster] = entry
        if median_conf < LOW_CONFIDENCE_MEDIAN:
            low_confidence.append(cluster)
        if consensus < LOW_CONSENSUS_FRACTION:
            low_consensus.append(cluster)

    if low_confidence:
        warnings.append(
            f"{len(low_confidence)} cluster(s) carry a median confidence below "
            f"{LOW_CONFIDENCE_MEDIAN} ({low_confidence}); the model produced a label but "
            "not one to quote without checking the markers"
        )
    if low_consensus:
        notes.append(
            f"{len(low_consensus)} cluster(s) had under {LOW_CONSENSUS_FRACTION:.0%} of their "
            f"cells agree on a type ({low_consensus}); they may merge two populations"
        )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    figure_paths = _plot(adata, out_dir, cluster_key)
    if not figure_paths:
        notes.append("no embedding to plot; run_umap did not run, or plotting failed")
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    types = adata.obs["cell_type"].value_counts()
    annotation_summary = {
        "model": str(model_name),
        "majority_voting": majority_voting,
        "over_clustering": cluster_key if majority_voting else None,
        "label_source": label_key,
        "n_cells": int(adata.n_obs),
        "n_cell_types": int(types.shape[0]),
        "cell_type_counts": {str(k): int(v) for k, v in types.items()},
        "median_conf_score": round(float(np.median(adata.obs["conf_score"])), 3),
        "normalized_to": CELLTYPIST_TARGET_SUM,
    }

    return _result(
        adata_path=adata_path,
        annotation_state="annotated",
        annotation_summary=annotation_summary,
        per_cluster=per_cluster,
        figure_paths=figure_paths,
        warnings=warnings,
        notes=notes,
        next_tool="build_report",
        metrics=annotation_summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    annotation_state: str = "not_run",
    annotation_summary: dict[str, Any] | None = None,
    per_cluster: dict[str, Any] | None = None,
    figure_paths: dict[str, str] | None = None,
    evidence: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "annotation_state": annotation_state,
        "annotation_summary": annotation_summary or {},
        "per_cluster": per_cluster or {},
        "figure_paths": figure_paths or {},
        "evidence": evidence or {},
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
    parser.add_argument("--model", dest="celltypist_model")
    parser.add_argument("--cluster-key", default="leiden")
    parser.add_argument("--no-majority-voting", action="store_true")
    parser.add_argument("--list-models", action="store_true", help="print the catalogue and exit")
    args = parser.parse_args(argv)

    if args.list_models:
        print(json.dumps(_model_catalogue(), indent=2, ensure_ascii=False))
        return 0

    config: dict[str, Any] = {"cluster_key": args.cluster_key}
    if args.celltypist_model:
        config["celltypist_model"] = args.celltypist_model
    if args.no_majority_voting:
        config["majority_voting"] = False

    result = run(
        {
            "artifacts": {"find_markers": {"adata_path": args.adata_path}},
            "run_dir": args.run_dir,
            "config": config,
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
