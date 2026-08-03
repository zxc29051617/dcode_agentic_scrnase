"""Load a post-cell-calling matrix into AnnData for the Scanpy mainline.

The short route. Cell calling has already happened — by Cell Ranger, or by
`cell_calling_review` on this run — so there is nothing to decide here, only to
read and to record where the counts came from.

AnnData travels as a path: this writes `<run_dir>/load_filtered_counts/adata.h5ad`
and passes the location on. See `src/matrix_io.py`.

Run standalone:
    python skills/load_filtered_counts/load_filtered_counts.py <matrix> --run-dir <out>
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

TOOL_NAME = "load_filtered_counts"
INPUT_FIELDS = (
    "artifacts.count_matrix_classify",
    "input_bundle",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "source_state",
    "n_cells",
    "n_genes",
    "warnings",
    "errors",
    "recommended_next_tool",
)

def _resolve_matrix(payload: dict[str, Any]) -> str | None:
    """The matrix `count_matrix_classify` settled on, or a standalone path."""
    artifacts = payload.get("artifacts") or {}
    for step in ("count_matrix_classify", "cellranger_count", "ingest_validate"):
        path = (artifacts.get(step) or {}).get("matrix_path")
        if path:
            return str(path)
    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return str(bundle)
    raw = bundle.get("paths") or bundle.get("path") or []
    paths = [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]
    return paths[0] if paths else None


def run(payload: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    source = _resolve_matrix(payload)
    if not source:
        return _result(errors=["no matrix path; count_matrix_classify must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"matrix path does not exist: {source}"])

    try:
        adata, provenance = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])

    if adata.n_obs == 0:
        return _result(errors=[f"{source} contains no barcodes"])

    totals = matrix_io.total_counts(adata)
    n_empty = int((totals == 0).sum())
    if n_empty:
        warnings.append(
            f"{n_empty:,} of {adata.n_obs:,} barcodes have zero counts; a filtered "
            "matrix should contain none, so this may not be post-cell-calling data"
        )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    import numpy as np

    return _result(
        adata_path=adata_path,
        source_state={**provenance, "cell_calling": "already applied upstream"},
        n_cells=int(adata.n_obs),
        n_genes=int(adata.n_vars),
        warnings=warnings,
        next_tool="run_qc_metrics",
        metrics={
            "n_cells": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "median_umi_per_cell": int(np.median(totals)),
            "median_genes_per_cell": int(np.median(matrix_io.genes_per_barcode(adata))),
            "n_empty_barcodes": n_empty,
        },
    )


def _result(
    *,
    adata_path: str | None = None,
    source_state: dict[str, Any] | None = None,
    n_cells: int | None = None,
    n_genes: int | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "source_state": source_state or {},
        "n_cells": n_cells,
        "n_genes": n_genes,
        "cell_calling_resolved": True,
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("matrix")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    result = run(
        {
            "artifacts": {"count_matrix_classify": {"matrix_path": args.matrix}},
            "run_dir": args.run_dir,
            "config": {},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
