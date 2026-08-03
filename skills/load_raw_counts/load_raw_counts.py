"""Load pre-cell-calling matrices, and measure the curve cell calling needs.

A raw matrix is every barcode the sequencer saw — for pbmc_1k_v3 that is 329,735
of them, around 1,200 of which are cells. Which ones is not a question this step
answers; it loads the counts and measures each sample's barcode-rank curve so
`cell_calling_review` (and the person reading it) can decide.

`cell_calling_resolved` is therefore always False. A raw matrix by definition has
not been through a cell caller, and reporting anything else would route 300,000
empty droplets straight into the mainline.

One sample or twenty: `{sample: path}` in, `{sample: adata path}` out, and a
barcode-rank curve per sample — they differ, so one cutoff for all of them is a
choice someone has to make deliberately.

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
INPUT_FIELDS = ("artifacts.count_matrix_classify", "input_bundle", "run_dir")
OUTPUT_FIELDS = (
    "adata_paths",
    "per_sample",
    "barcode_rank",
    "cell_calling_resolved",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: A real barcode-rank cliff drops counts by orders of magnitude — pbmc_1k_v3
#: falls 370x. Below this the curve slopes rather than breaks, and no cutoff on
#: it is obviously right.
MIN_CLEAR_CLIFF_RATIO = 10


def run(payload: dict[str, Any]) -> dict[str, Any]:
    # The mapping resolver is shared with the filtered loader; they answer the
    # same question about where the matrices are.
    from importlib import import_module

    sys.path.insert(0, str(_PROJECT_ROOT / "skills" / "load_filtered_counts"))
    resolve_matrices = import_module("load_filtered_counts").resolve_matrices

    warnings: list[str] = []
    incoming = resolve_matrices(payload)
    if not incoming:
        return _result(errors=["no matrix path; count_matrix_classify must run first"])

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_paths: dict[str, str] = {}
    per_sample: dict[str, Any] = {}

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

        evidence = matrix_io.barcode_rank_evidence(matrix_io.total_counts(adata))

        if evidence.get("n_nonzero", 0) == evidence.get("n_barcodes"):
            warnings.append(
                f"{name}: no barcode has zero counts, which is unusual for a raw "
                "matrix — check that this is really pre-cell-calling data"
            )
        ratio = evidence.get("cliff_drop_ratio")
        cliff = evidence.get("cliff_rank")
        if cliff and ratio is not None and ratio < MIN_CLEAR_CLIFF_RATIO:
            warnings.append(
                f"{name}: counts fall only {ratio}x across the cliff at rank "
                f"{cliff:,}, so cells and ambient droplets are not cleanly "
                "separated; where cells end is a judgement call rather than "
                "something this curve settles"
            )

        adata_paths[name] = matrix_io.write_h5ad(adata, out_dir / f"{name}.h5ad")
        per_sample[name] = {
            "n_barcodes": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "barcode_rank": evidence,
            "source_state": {**provenance, "cell_calling": "not applied"},
            "source": source,
        }

    first = next(iter(sorted(adata_paths)))
    return _result(
        adata_paths=adata_paths,
        adata_path=adata_paths[first],
        per_sample=per_sample,
        barcode_rank=per_sample[first]["barcode_rank"],
        warnings=warnings,
        next_tool="cell_calling_review",
        metrics={
            "n_samples": len(per_sample),
            "barcodes_per_sample": {n: v["n_barcodes"] for n, v in per_sample.items()},
            "cliff_per_sample": {
                n: v["barcode_rank"].get("cliff_rank") for n, v in per_sample.items()
            },
        },
    )


def _result(
    *,
    adata_paths: dict[str, str] | None = None,
    adata_path: str | None = None,
    per_sample: dict[str, Any] | None = None,
    barcode_rank: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_paths": adata_paths or {},
        "adata_path": adata_path,
        "per_sample": per_sample or {},
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
