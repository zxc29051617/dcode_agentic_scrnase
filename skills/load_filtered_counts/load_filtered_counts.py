"""Load post-cell-calling matrices into AnnData for the Scanpy mainline.

The short route. Cell calling has already happened — by Cell Ranger, or by
`cell_calling_review` on this run — so there is nothing to decide here, only to
read and to record where the counts came from.

One sample or twenty, the shape is the same: `{sample: path}` in,
`{sample: adata path}` out. `merge_samples` collapses them afterwards, so
nothing here branches on how many there are.

AnnData travels as a path, not an object. See `src/matrix_io.py`.

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
INPUT_FIELDS = ("artifacts.count_matrix_classify", "input_bundle", "run_dir")
OUTPUT_FIELDS = (
    "adata_paths",
    "per_sample",
    "source_state",
    "n_cells",
    "n_genes",
    "warnings",
    "errors",
    "recommended_next_tool",
)


def resolve_matrices(payload: dict[str, Any]) -> dict[str, str]:
    """`{sample: path}` for every matrix `count_matrix_classify` settled on."""
    artifacts = payload.get("artifacts") or {}
    for step in ("count_matrix_classify", "cellranger_count", "ingest_validate"):
        source = artifacts.get(step) or {}
        paths = source.get("matrix_paths")
        if paths:
            return {str(k): str(v) for k, v in paths.items()}
        path = source.get("matrix_path")
        if path:
            return {"sample1": str(path)}

    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return {"sample1": str(bundle)}
    raw = bundle.get("paths") or bundle.get("path") or []
    listed = [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]
    return {"sample1": listed[0]} if listed else {}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    warnings: list[str] = []
    incoming = resolve_matrices(payload)
    if not incoming:
        return _result(errors=["no matrix path; count_matrix_classify must run first"])

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_paths: dict[str, str] = {}
    per_sample: dict[str, Any] = {}
    provenances: dict[str, Any] = {}

    for name, source in sorted(incoming.items()):
        if not Path(source).expanduser().exists():
            return _result(errors=[f"matrix path does not exist ({name}): {source}"])
        try:
            adata, provenance = matrix_io.load_matrix(source)
        except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
            return _result(
                errors=[f"cannot load {name} from {source}: {type(exc).__name__}: {exc}"]
            )
        if adata.n_obs == 0:
            return _result(errors=[f"{name} ({source}) contains no barcodes"])

        totals = matrix_io.total_counts(adata)
        n_empty = int((totals == 0).sum())
        if n_empty:
            # Reported, never repaired: a filtered matrix should contain none,
            # so this means the upstream classification or the file is wrong,
            # and quietly fixing it would hide a routing bug.
            warnings.append(
                f"{name}: {n_empty:,} of {adata.n_obs:,} barcodes have zero counts; "
                "a filtered matrix should contain none, so this may not be "
                "post-cell-calling data"
            )

        adata_paths[name] = matrix_io.write_h5ad(adata, out_dir / f"{name}.h5ad")
        provenances[name] = provenance
        per_sample[name] = {
            "n_cells": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "median_umi_per_cell": int(np.median(totals)),
            "median_genes_per_cell": int(np.median(matrix_io.genes_per_barcode(adata))),
            "n_empty_barcodes": n_empty,
            "source": source,
        }

    first = next(iter(sorted(adata_paths)))
    return _result(
        adata_paths=adata_paths,
        adata_path=adata_paths[first],
        per_sample=per_sample,
        source_state={"cell_calling": "already applied upstream", "samples": provenances},
        n_cells=sum(v["n_cells"] for v in per_sample.values()),
        n_genes=per_sample[first]["n_genes"],
        warnings=warnings,
        next_tool="merge_samples",
        metrics={
            "n_samples": len(per_sample),
            "n_cells": sum(v["n_cells"] for v in per_sample.values()),
            "n_genes": per_sample[first]["n_genes"],
            "cells_per_sample": {n: v["n_cells"] for n, v in per_sample.items()},
        },
    )


def _result(
    *,
    adata_paths: dict[str, str] | None = None,
    adata_path: str | None = None,
    per_sample: dict[str, Any] | None = None,
    source_state: dict[str, Any] | None = None,
    n_cells: int | None = None,
    n_genes: int | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_paths": adata_paths or {},
        # The first sample, kept so a single-sample consumer can still read one
        # path. `merge_samples` uses `adata_paths` and is the real consumer.
        "adata_path": adata_path,
        "per_sample": per_sample or {},
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
