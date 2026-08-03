"""Load a pre-cell-calling matrix, and keep the evidence needed to call cells.

A raw matrix is every barcode the sequencer saw — for pbmc_1k_v3 that is 329,735
of them, around 1,200 of which are cells. Which ones is not a question this step
answers; it loads the counts and measures the barcode-rank curve so
`cell_calling_review` (and the person reading it) can decide.

`cell_calling_resolved` is therefore always False here. A raw matrix by
definition has not been through a cell caller, and reporting anything else would
route 300,000 empty droplets straight into the mainline.

AnnData travels as a path: this writes `<run_dir>/load_raw_counts/adata.h5ad`.
See `src/matrix_io.py`.

Run standalone:
    python skills/load_raw_counts/load_raw_counts.py <matrix> --run-dir <out>
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

TOOL_NAME = "load_raw_counts"

#: A real barcode-rank cliff drops counts by orders of magnitude — the pbmc_1k_v3
#: reference set falls 370x. Below this the curve slopes rather than breaks, and
#: no cutoff on it is obviously right.
MIN_CLEAR_CLIFF_RATIO = 10
INPUT_FIELDS = (
    "artifacts.count_matrix_classify",
    "input_bundle",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "source_state",
    "barcode_rank",
    "cell_calling_resolved",
    "warnings",
    "errors",
    "recommended_next_tool",
)


def _resolve_matrix(payload: dict[str, Any]) -> str | None:
    """The matrix `count_matrix_classify` settled on, or a standalone path."""
    artifacts = payload.get("artifacts") or {}
    for step in ("count_matrix_classify", "ingest_validate"):
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
    evidence = matrix_io.barcode_rank_evidence(totals)

    if evidence.get("n_nonzero", 0) == evidence.get("n_barcodes"):
        warnings.append(
            "no barcode has zero counts, which is unusual for a raw matrix — "
            "check that this is really pre-cell-calling data"
        )
    cliff = evidence.get("cliff_rank")
    ratio = evidence.get("cliff_drop_ratio")
    if cliff and ratio is not None and ratio < MIN_CLEAR_CLIFF_RATIO:
        warnings.append(
            f"counts fall only {ratio}x across the cliff at rank {cliff:,}, so cells and "
            f"ambient droplets are not cleanly separated; where cells end is a "
            f"judgement call rather than something this curve settles"
        )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    return _result(
        adata_path=adata_path,
        source_state={**provenance, "cell_calling": "not applied"},
        barcode_rank=evidence,
        warnings=warnings,
        next_tool="cell_calling_review",
        metrics={
            "n_barcodes": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "n_nonzero_barcodes": evidence.get("n_nonzero"),
            "cliff_rank": cliff,
            "cliff_umi": evidence.get("cliff_umi"),
            "cliff_drop_ratio": ratio,
        },
    )


def _result(
    *,
    adata_path: str | None = None,
    source_state: dict[str, Any] | None = None,
    barcode_rank: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "source_state": source_state or {},
        "barcode_rank": barcode_rank or {},
        # Always False: a raw matrix has not been through a cell caller, and
        # claiming otherwise would send every empty droplet into the mainline.
        "cell_calling_resolved": False,
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
