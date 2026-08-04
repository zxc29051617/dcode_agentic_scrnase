"""Flag likely doublets with Scrublet — per library, at the rate the loading implies.

Two things this gets right that a default call does not.

**Doublets form in a GEM well, so detection is per library.** Simulating
doublets across a merged object would pair cells that were never in the same
droplet, and the scores would be meaningless. Scrublet is run per `sample`.

**The expected rate follows the cell count.** Scrublet's own default is 0.06 —
6% — which comes from a loading that recovers ~8,000 cells. 10x's published
multiplet table is close to linear at about 0.76% per thousand recovered, so a
1,200-cell library should expect ~0.9%, not 6%. Passing the default would look
for seven times more doublets than the chemistry can produce.

## Annotating always, removing only when asked
Unlike `apply_cell_qc_filter`, this step does not stop for a decision. Its
output is complete either way: the scores and calls are on every cell, and a
downstream step can use the flag as a covariate rather than a filter, which some
designs prefer. What it will not do is remove cells without being asked, and if
it does not remove them it says how many are still there.

Run standalone:
    python skills/detect_doublets/detect_doublets.py <adata.h5ad> --run-dir <out> [--remove]
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

TOOL_NAME = "detect_doublets"
INPUT_FIELDS = (
    "artifacts.apply_cell_qc_filter",
    "config.expected_doublet_rate",
    "config.remove_doublets",
    "config.doublet_threshold",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "doublets_removed",
    "doublet_summary",
    "per_sample",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: 10x's published multiplet rate, as a slope. Their table runs from 0.4% at 500
#: cells to 7.6% at 10,000 — near enough linear that one number covers it, and
#: far better than a fixed default that is right for exactly one loading.
MULTIPLET_RATE_PER_CELL = 7.6e-6

#: Scrublet needs enough cells to build a neighbour graph over simulated
#: doublets. Below this the score is noise, so the sample is annotated as
#: not-assessed rather than given a number nobody should trust.
MIN_CELLS_FOR_SCRUBLET = 50

#: Above this fraction called, the result is more likely a failed fit than a
#: library that is mostly doublets.
IMPLAUSIBLE_DOUBLET_FRACTION = 0.3

#: Scrublet's own defaults, repeated here only to bound the PCA below. Keeping
#: the numbers next to the reasoning beats reading them out of its signature.
SCRUBLET_COMPONENTS = 30
SCRUBLET_MIN_CELLS_PER_GENE = 3
SCRUBLET_GENE_VARIABILITY_PCTL = 85


def expected_rate_for(n_cells: int) -> float:
    """The multiplet rate 10x's loading table implies for this many cells."""
    return round(min(MULTIPLET_RATE_PER_CELL * n_cells, 0.25), 4)


def _resolve_adata_path(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    for step in ("apply_cell_qc_filter", "run_qc_metrics", "post_load_validate"):
        path = (artifacts.get(step) or {}).get("adata_path")
        if path:
            return str(path)
    return (payload.get("config") or {}).get("adata_path")


def _for(setting: Any, sample: str) -> Any:
    """A single value applies to every sample; a mapping is read per sample."""
    if isinstance(setting, dict):
        return setting.get(sample)
    return setting


def _components_for(adata: Any) -> int:
    """How many principal components this library can actually support.

    Scrublet asks for 30. Before the PCA it keeps genes seen in at least a few
    cells, then only the most variable 15% of those — on a real library that is
    still thousands of genes, but on a small or shallow one it can fall below 30
    and arpack raises instead of returning a smaller basis. The estimate here
    mirrors that filter so the bound is derived, not guessed.
    """
    import numpy as np

    per_gene = np.asarray((adata.X > 0).sum(axis=0)).ravel()
    expressed = int((per_gene >= SCRUBLET_MIN_CELLS_PER_GENE).sum())
    variable = int(expressed * (100 - SCRUBLET_GENE_VARIABILITY_PCTL) / 100)
    return max(2, min(SCRUBLET_COMPONENTS, variable - 1, int(adata.n_obs) - 1))


def _score_one(adata: Any, *, rate: float, threshold: float | None, seed: int) -> dict[str, Any]:
    """Run Scrublet on one library and return its scores plus what it decided."""
    import numpy as np
    import scanpy as sc

    scored = adata.copy()
    sc.pp.scrublet(
        scored,
        expected_doublet_rate=rate,
        random_state=seed,
        n_prin_comps=_components_for(scored),
    )

    auto_threshold = float((scored.uns.get("scrublet") or {}).get("threshold", float("nan")))
    scores = np.asarray(scored.obs["doublet_score"], dtype=float)
    used = float(threshold) if threshold is not None else auto_threshold
    calls = scores > used if np.isfinite(used) else np.asarray(scored.obs["predicted_doublet"])

    return {
        "scores": scores,
        "calls": np.asarray(calls, dtype=bool),
        "auto_threshold": None if not np.isfinite(auto_threshold) else round(auto_threshold, 4),
        "threshold_used": None if not np.isfinite(used) else round(used, 4),
        "threshold_source": "config" if threshold is not None else "scrublet",
        "expected_rate": rate,
    }


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source = _resolve_adata_path(payload)
    if not source:
        return _result(errors=["no AnnData path; apply_cell_qc_filter must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs == 0:
        return _result(errors=[f"{source} contains no cells"])

    seed = int(config.get("random_state", 0))
    samples = (
        sorted(str(s) for s in adata.obs["sample"].unique())
        if "sample" in adata.obs
        else ["all cells"]
    )

    scores = np.zeros(adata.n_obs, dtype=float)
    calls = np.zeros(adata.n_obs, dtype=bool)
    assessed = np.zeros(adata.n_obs, dtype=bool)
    per_sample: dict[str, Any] = {}

    for sample in samples:
        in_sample = (
            np.asarray(adata.obs["sample"] == sample)
            if "sample" in adata.obs
            else np.ones(adata.n_obs, dtype=bool)
        )
        n_cells = int(in_sample.sum())

        if n_cells < MIN_CELLS_FOR_SCRUBLET:
            warnings.append(
                f"{sample}: {n_cells:,} cells is too few for Scrublet to build a "
                f"neighbour graph (needs {MIN_CELLS_FOR_SCRUBLET}); no doublet score "
                "was computed rather than reporting one nobody should trust"
            )
            per_sample[sample] = {"n_cells": n_cells, "assessed": False}
            continue

        # The rate is derived from this library's own cell count unless given.
        given_rate = _for(config.get("expected_doublet_rate"), sample)
        rate = float(given_rate) if given_rate is not None else expected_rate_for(n_cells)
        try:
            outcome = _score_one(
                adata[in_sample],
                rate=rate,
                threshold=_for(config.get("doublet_threshold"), sample),
                seed=seed,
            )
        except Exception as exc:  # noqa: BLE001 - a failed fit is a finding
            warnings.append(
                f"{sample}: Scrublet failed ({type(exc).__name__}: {exc}); "
                "these cells carry no doublet score"
            )
            per_sample[sample] = {"n_cells": n_cells, "assessed": False}
            continue

        indices = np.flatnonzero(in_sample)
        scores[indices] = outcome["scores"]
        calls[indices] = outcome["calls"]
        assessed[indices] = True

        n_called = int(outcome["calls"].sum())
        fraction = n_called / max(n_cells, 1)
        entry = {
            "n_cells": n_cells,
            "assessed": True,
            "n_doublets": n_called,
            "pct_doublets": round(100 * fraction, 2),
            "expected_rate": outcome["expected_rate"],
            "expected_rate_source": "config" if given_rate is not None else "10x loading table",
            "threshold_used": outcome["threshold_used"],
            "threshold_source": outcome["threshold_source"],
            "auto_threshold": outcome["auto_threshold"],
            "median_score": round(float(np.median(outcome["scores"])), 4),
        }
        per_sample[sample] = entry

        if fraction > IMPLAUSIBLE_DOUBLET_FRACTION:
            warnings.append(
                f"{sample}: {n_called:,} of {n_cells:,} cells called doublets "
                f"({fraction:.0%}). Above {IMPLAUSIBLE_DOUBLET_FRACTION:.0%} this is "
                "more likely a failed fit than a library that is mostly doublets"
            )

    adata.obs["doublet_score"] = scores
    adata.obs["predicted_doublet"] = calls
    adata.obs["doublet_assessed"] = assessed

    n_called = int(calls.sum())
    n_assessed = int(assessed.sum())
    if n_assessed < adata.n_obs:
        notes.append(
            f"{adata.n_obs - n_assessed:,} cells carry no doublet score; "
            "`doublet_assessed` marks which were evaluated"
        )

    # ---- removing, only when asked -----------------------------------------
    remove = bool(config.get("remove_doublets", False))
    if remove and n_called:
        if n_called == adata.n_obs:
            return _result(
                errors=[
                    f"every one of the {adata.n_obs:,} cells was called a doublet; "
                    "removing them would leave nothing. Check the expected rate and "
                    "the threshold before filtering"
                ],
                per_sample=per_sample,
            )
        adata = adata[~calls].copy()
    elif n_called:
        notes.append(
            f"{n_called:,} cells are flagged as doublets and remain in the data. "
            "Set remove_doublets to drop them, or use `predicted_doublet` as a "
            "covariate downstream"
        )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    summary = {
        "n_cells_in": n_assessed + (len(scores) - n_assessed),
        "n_assessed": n_assessed,
        "n_doublets": n_called,
        "pct_doublets": round(100 * n_called / max(len(scores), 1), 2),
        "removed": bool(remove and n_called),
        "n_cells_out": int(adata.n_obs),
    }
    return _result(
        adata_path=adata_path,
        doublets_removed=summary["removed"],
        doublet_summary=summary,
        per_sample=per_sample,
        warnings=warnings,
        notes=notes,
        next_tool="normalize_hvg_prepare",
        metrics=summary,
    )


def _result(
    *,
    adata_path: str | None = None,
    doublets_removed: bool = False,
    doublet_summary: dict[str, Any] | None = None,
    per_sample: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "doublets_removed": doublets_removed,
        "doublet_summary": doublet_summary or {},
        "per_sample": per_sample or {},
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "notes": notes or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("adata")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--remove", action="store_true", help="drop the called doublets")
    parser.add_argument("--expected-rate", type=float,
                        help="override the rate derived from the cell count")
    parser.add_argument("--threshold", type=float, help="override Scrublet's own threshold")
    args = parser.parse_args(argv)

    result = run(
        {
            "artifacts": {"apply_cell_qc_filter": {"adata_path": args.adata}},
            "run_dir": args.run_dir,
            "config": {
                "remove_doublets": args.remove,
                "expected_doublet_rate": args.expected_rate,
                "doublet_threshold": args.threshold,
            },
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
