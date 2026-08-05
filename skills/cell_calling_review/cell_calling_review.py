"""Decide which barcodes are cells — and let a person be the one deciding.

Cell Ranger will pick a number on its own: a knee on the barcode-rank curve,
then an EmptyDrops test that rescues low-UMI barcodes whose expression differs
from ambient RNA. That is a good default and a bad mandate. When the curve has
no sharp cliff, or the tissue is one where the algorithm is known to be
conservative, the number should be the operator's call.

So this step does two things and keeps them separate:

  1. **Measure.** Report the barcode-rank curve, where the cliff is, and what a
     given cell count would cost in UMIs — enough to choose from.
  2. **Apply, only when told.** With no instruction it decides nothing and stops
     at the human gate. A cell count is not something to guess on someone's
     behalf.

Choosing a count is `--force-cells` semantics, applied to the raw matrix that is
already on disk: seconds instead of a 20-minute recount, and identical, since
`--force-cells N` is itself the top N barcodes by UMI. What it gives up is
EmptyDrops. On pbmc_1k_v3 the two agree on 1,206 of 1,218 barcodes — the other
12 are exactly where the ambient-profile test overruled the ranking, and this
step says so rather than presenting a bare number.

## More than one sample
Each library has its own barcode-rank curve, so each needs its own cutoff. The
fan-out happens inside this step rather than in the graph:

  `force_cells: 1500`                    the same count for every sample
  `force_cells: {A: 1500, B: 2400}`      per sample, which is usually what the
                                         curves actually call for

A number that is right for one library is rarely right for another, so the
per-sample form is the one the evidence tends to point at.

Run standalone:
    python skills/cell_calling_review/cell_calling_review.py <raw.h5ad> \\
        --run-dir <out> [--force-cells N | --min-umi X]
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

TOOL_NAME = "cell_calling_review"
INPUT_FIELDS = (
    "artifacts.load_raw_counts",
    "artifacts.cellranger_count",
    "config.force_cells",
    "config.min_umi",
    "run_dir",
)
OUTPUT_FIELDS = (
    "cell_calling_state",
    "adata_path",
    "n_cells",
    "selection",
    "evidence",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Cell counts to cost out, so the barcode-rank curve is readable as a table
#: rather than a plot nobody opened.
PREVIEW_COUNTS = (500, 1_000, 2_000, 3_000, 5_000, 10_000)


def _cellranger_called_barcodes(artifacts: dict[str, Any]) -> set[str] | None:
    """The barcodes Cell Ranger itself kept, for comparison. None if unavailable."""
    libraries = (artifacts.get("cellranger_count") or {}).get("libraries") or []
    for library in libraries:
        path = Path(str(library.get("filtered_feature_bc_matrix", "")))
        if not path.is_file():
            continue
        try:
            import h5py

            with h5py.File(path, "r") as handle:
                group = handle.get("matrix")
                if group is None:
                    continue
                return {
                    b.decode() if isinstance(b, bytes) else str(b)
                    for b in group["barcodes"][:]
                }
        except Exception:  # noqa: BLE001 - a missing comparison is not a failure
            return None
    return None


def _preview(totals: Any, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """What each candidate cell count would actually keep."""
    import numpy as np

    ordered = np.sort(np.asarray(totals))[::-1]
    rows = []
    for count in PREVIEW_COUNTS:
        if count > ordered.size:
            break
        rows.append(
            {
                "cells": count,
                "umi_threshold": int(ordered[count - 1]),
                "median_umi": int(np.median(ordered[:count])),
            }
        )
    cliff = evidence.get("cliff_rank")
    if cliff and all(row["cells"] != cliff for row in rows):
        rows.append(
            {
                "cells": int(cliff),
                "umi_threshold": int(evidence.get("cliff_umi", 0)),
                "median_umi": int(np.median(ordered[:cliff])),
                "note": "the cliff — the steepest point of the barcode-rank curve",
            }
        )
    return sorted(rows, key=lambda row: row["cells"])


def _compare_with_cellranger(
    kept: set[str], called: set[str] | None, totals_by_barcode: dict[str, int]
) -> dict[str, Any] | None:
    """How this selection differs from the algorithm's, in barcodes not adjectives."""
    if called is None:
        return None
    only_here = kept - called
    only_cellranger = called - kept
    import statistics

    def median_umi(barcodes: set[str]) -> int | None:
        values = [totals_by_barcode[b] for b in barcodes if b in totals_by_barcode]
        return int(statistics.median(values)) if values else None

    return {
        "cellranger_cells": len(called),
        "selected_cells": len(kept),
        "shared": len(kept & called),
        "added_by_this_selection": len(only_here),
        "dropped_by_this_selection": len(only_cellranger),
        "median_umi_of_added": median_umi(only_here),
        "median_umi_of_dropped": median_umi(only_cellranger),
    }


def _save_rank_curve(totals: Any, path: Path) -> str:
    """Store the sorted UMI-per-barcode vector so the curve can be redrawn.

    The whole vector, not a downsample. Barcode-rank is plotted log-rank against
    log-UMI, and the knee occupies a narrow band of ranks: sampling evenly in
    rank space spends nearly every point on the flat tail and can miss the bend
    entirely. Keeping all of it costs little — 300,000 barcodes as int32 is
    about 1.2 MB before compression, against gigabytes for the raw matrix it
    would otherwise have to be recomputed from.

    Only the counts are stored: rank is the index, and barcode identity is not
    needed to draw the curve (the selected barcodes are already in the AnnData).
    """
    import numpy as np

    ordered = np.sort(np.asarray(totals))[::-1]
    # int64 only where the counts genuinely need it; int32 covers ~2.1e9 UMI.
    dtype = np.int32 if ordered.max(initial=0) < np.iinfo(np.int32).max else np.int64
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, sorted_umi_counts=ordered.astype(dtype))
    return str(path)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    config = payload.get("config") or {}
    artifacts = payload.get("artifacts") or {}
    warnings: list[str] = []

    loaded = artifacts.get("load_raw_counts") or {}
    incoming = loaded.get("adata_paths") or {}
    if not incoming:
        single = loaded.get("adata_path") or config.get("adata_path")
        if single:
            incoming = {"sample1": single}
    if not incoming:
        return _result(errors=["no raw AnnData; load_raw_counts must run first"])
    missing = [f"{n}: {p}" for n, p in incoming.items() if not Path(p).expanduser().exists()]
    if missing:
        return _result(errors=[f"raw AnnData does not exist: {'; '.join(missing)}"])

    force_cells = config.get("force_cells")
    min_umi = config.get("min_umi")
    if force_cells is not None and min_umi is not None:
        return _result(
            errors=[
                "force_cells and min_umi are two ways to say the same thing; set one"
            ]
        )

    def _for(setting: Any, name: str) -> Any:
        """A single value applies to every sample; a mapping is read per sample."""
        if isinstance(setting, dict):
            return setting.get(name)
        return setting

    called = _cellranger_called_barcodes(artifacts)
    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME

    per_sample: dict[str, Any] = {}
    adata_paths: dict[str, str] = {}
    undecided: list[str] = []

    for name, path in sorted(incoming.items()):
        try:
            adata, _ = matrix_io.load_matrix(path)
        except Exception as exc:  # noqa: BLE001
            return _result(errors=[f"cannot load {name} from {path}: {type(exc).__name__}: {exc}"])

        totals = matrix_io.total_counts(adata)
        evidence = matrix_io.barcode_rank_evidence(totals)
        evidence["preview"] = _preview(totals, evidence)
        if called is not None:
            evidence["cellranger_cells"] = len(called)

        # Saved on both paths, including `needs_review`: the curve is the
        # evidence the operator is deciding from, so it has to exist before a
        # decision is made, not only after one.
        evidence["rank_curve_path"] = _save_rank_curve(totals, out_dir / f"{name}_barcode_rank.npz")

        want_cells = _for(force_cells, name)
        want_umi = _for(min_umi, name)

        if want_cells is None and want_umi is None:
            undecided.append(name)
            per_sample[name] = {"cell_calling_state": "needs_review", "evidence": evidence}
            continue

        try:
            mask, selection = matrix_io.select_barcodes(
                totals, force_cells=want_cells, min_umi=want_umi
            )
        except ValueError as exc:
            return _result(errors=[f"{name}: {exc}"], evidence=evidence)

        n_selected = int(mask.sum())
        if n_selected == 0:
            return _result(
                errors=[
                    f"{name}: the chosen threshold keeps no barcodes at all "
                    f"({json.dumps(selection)}); the highest barcode has "
                    f"{evidence.get('max_umi', 0):,} UMI"
                ],
                evidence=evidence,
            )
        if want_cells is not None and n_selected < int(want_cells):
            warnings.append(
                f"{name}: asked for {int(want_cells):,} cells but only {n_selected:,} "
                "barcodes have any counts at all"
            )

        subset = adata[mask].copy()
        comparison = _compare_with_cellranger(
            set(subset.obs_names),
            called,
            {n: int(v) for n, v in zip(adata.obs_names, totals)},
        )
        if comparison:
            evidence["vs_cellranger"] = comparison
            if comparison["dropped_by_this_selection"] or comparison["added_by_this_selection"]:
                warnings.append(
                    f"{name}: this selection differs from Cell Ranger's — "
                    f"{comparison['added_by_this_selection']:,} barcodes added, "
                    f"{comparison['dropped_by_this_selection']:,} dropped. Choosing a "
                    f"count bypasses the EmptyDrops test, which is what rescues "
                    f"low-UMI barcodes whose expression differs from ambient RNA"
                )

        adata_paths[name] = matrix_io.write_h5ad(subset, out_dir / f"{name}.h5ad")
        per_sample[name] = {
            "cell_calling_state": "resolved",
            "n_cells": n_selected,
            "selection": {**selection, "chosen_by": "operator"},
            "evidence": evidence,
        }

    # Nothing goes downstream until every sample has a cutoff: a partial merge
    # would quietly analyse some libraries and drop the rest.
    if undecided:
        lines = []
        for name in undecided:
            ev = per_sample[name]["evidence"]
            cliff = ev.get("cliff_rank")
            knee = ev.get("knee_rank")
            lines.append(
                f"{name}: knee at {knee:,}, inflection at {cliff:,}"
                if cliff and knee else f"{name}: no clear cliff"
            )
        warnings.append(
            "no cell count chosen for "
            + ", ".join(undecided)
            + f". {'; '.join(lines)}. Set force_cells (top N barcodes) or min_umi "
            f"— a single value for every sample, or a mapping per sample — and re-run"
        )
        return _result(
            cell_calling_state="needs_review",
            per_sample=per_sample,
            evidence=per_sample[undecided[0]]["evidence"],
            warnings=warnings,
            next_tool="human_review",
            metrics={"n_samples": len(per_sample), "undecided": undecided},
        )

    first = next(iter(sorted(per_sample)))
    evidence = per_sample[first]["evidence"]

    total_cells = sum(v["n_cells"] for v in per_sample.values())
    return _result(
        cell_calling_state="resolved",
        adata_paths=adata_paths,
        adata_path=adata_paths[first],
        n_cells=total_cells,
        selection=per_sample[first]["selection"],
        per_sample=per_sample,
        evidence=evidence,
        warnings=warnings,
        next_tool="merge_samples",
        metrics={
            **_metrics(evidence, total_cells),
            "n_samples": len(per_sample),
            "cells_per_sample": {n: v["n_cells"] for n, v in per_sample.items()},
        },
    )


def _metrics(evidence: dict[str, Any], n_cells: int | None) -> dict[str, Any]:
    return {
        "n_barcodes_examined": evidence.get("n_barcodes"),
        "cliff_rank": evidence.get("cliff_rank"),
        "cliff_umi": evidence.get("cliff_umi"),
        "cliff_drop_ratio": evidence.get("cliff_drop_ratio"),
        "cellranger_cells": evidence.get("cellranger_cells"),
        "n_cells": n_cells,
    }


def _result(
    *,
    cell_calling_state: str = "needs_review",
    adata_paths: dict[str, str] | None = None,
    adata_path: str | None = None,
    per_sample: dict[str, Any] | None = None,
    n_cells: int | None = None,
    selection: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "cell_calling_state": cell_calling_state,
        "adata_paths": adata_paths or {},
        "adata_path": adata_path,
        "per_sample": per_sample or {},
        "n_cells": n_cells,
        "selection": selection or {},
        "evidence": evidence or {},
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("adata", help="the raw AnnData written by load_raw_counts")
    parser.add_argument("--run-dir", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--force-cells", type=int, help="keep the top N barcodes by UMI")
    group.add_argument("--min-umi", type=int, help="keep barcodes with at least this many UMI")
    parser.add_argument("--cellranger-filtered", help="Cell Ranger's own call, to compare against")
    args = parser.parse_args(argv)

    artifacts: dict[str, Any] = {"load_raw_counts": {"adata_path": args.adata}}
    if args.cellranger_filtered:
        artifacts["cellranger_count"] = {
            "libraries": [{"filtered_feature_bc_matrix": args.cellranger_filtered}]
        }

    result = run(
        {
            "artifacts": artifacts,
            "run_dir": args.run_dir,
            "config": {"force_cells": args.force_cells, "min_umi": args.min_umi},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
