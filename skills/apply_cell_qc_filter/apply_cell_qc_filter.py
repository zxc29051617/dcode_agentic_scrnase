"""Remove low-quality cells — with the operator choosing the thresholds.

`run_qc_metrics` measured. This is where a number becomes a cut, and cutting is
destructive: a cell removed here is gone from every plot, every marker test and
every cluster downstream. So the same rule as `cell_calling_review` applies —

  1. **Measure.** What each candidate threshold would cost, per criterion and
     per sample.
  2. **Apply, only when told.** With no thresholds it filters nothing and stops
     at the human gate. A QC cutoff is not guessed on someone's behalf.

There is no default threshold anywhere in this file. Published "standard" values
(200 genes, 20% mitochondrial) come from specific tissues and specific protocols,
and applying them silently to a different one is how good cells get thrown away
without anybody noticing.

## Filtering cells, not genes
Genes with too few cells are dropped in `normalize_hvg_prepare`, where the HVG
selection needs them gone anyway. Doing it here as well would mean two steps
could each silently shrink the feature space.

Run standalone:
    python skills/apply_cell_qc_filter/apply_cell_qc_filter.py <adata.h5ad> \\
        --run-dir <out> [--min-genes N] [--max-pct-mito X]
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

TOOL_NAME = "apply_cell_qc_filter"
INPUT_FIELDS = (
    "artifacts.run_qc_metrics",
    "config.min_genes",
    "config.min_counts",
    "config.max_pct_mito",
    "config.max_pct_erythroid",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "filter_state",
    "filter_summary",
    "thresholds",
    "per_sample",
    "evidence",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: The four cuts this step can make, mapped to the `obs` column each reads and
#: the direction of the comparison.
CRITERIA: dict[str, tuple[str, str]] = {
    "min_genes": ("n_genes_by_counts", "min"),
    "min_counts": ("total_counts", "min"),
    "max_pct_mito": ("pct_counts_mt", "max"),
    "max_pct_erythroid": ("pct_counts_erythroid", "max"),
}

#: Candidate values to cost out, so the distributions are readable as a table
#: rather than a plot nobody opened. Not defaults — nothing here is applied
#: unless it was asked for by name.
PREVIEW_VALUES: dict[str, tuple[float, ...]] = {
    "min_genes": (100, 200, 500, 1_000),
    "min_counts": (500, 1_000, 2_000, 5_000),
    "max_pct_mito": (5, 10, 15, 20, 25),
    "max_pct_erythroid": (1, 5, 10),
}

#: Percentiles that describe a distribution without needing the whole thing.
PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)

#: Above this fraction removed, the cut is reported as drastic. Not a limit —
#: a heavily contaminated sample may genuinely lose most of its droplets — but
#: it should never happen without being said out loud.
DRASTIC_REMOVAL_FRACTION = 0.5


def _resolve_adata_path(payload: dict[str, Any]) -> str | None:
    artifacts = payload.get("artifacts") or {}
    path = (artifacts.get("run_qc_metrics") or {}).get("adata_path")
    if path:
        return str(path)
    return (payload.get("config") or {}).get("adata_path")


def _for(setting: Any, sample: str) -> Any:
    """A single value applies to every sample; a mapping is read per sample."""
    if isinstance(setting, dict):
        return setting.get(sample)
    return setting


def _distribution(values: Any) -> dict[str, Any]:
    import numpy as np

    values = np.asarray(values, dtype=float)
    return {
        "percentiles": {
            str(p): round(float(np.percentile(values, p)), 2) for p in PERCENTILES
        },
        "min": round(float(values.min()), 2),
        "max": round(float(values.max()), 2),
    }


def _preview(adata: Any, available: dict[str, str]) -> dict[str, Any]:
    """What each candidate value would remove, per criterion.

    Each row is that criterion applied on its own — the cuts overlap, so the
    numbers do not add up to a combined total, and pretending otherwise would
    overstate what any single threshold costs.
    """
    import numpy as np

    rows: dict[str, Any] = {}
    n_cells = int(adata.n_obs)
    for name, column in available.items():
        direction = CRITERIA[name][1]
        values = np.asarray(adata.obs[column], dtype=float)
        entries = []
        for candidate in PREVIEW_VALUES[name]:
            failing = values < candidate if direction == "min" else values > candidate
            removed = int(failing.sum())
            entries.append(
                {
                    "threshold": candidate,
                    "cells_removed": removed,
                    "cells_kept": n_cells - removed,
                    "pct_removed": round(100 * removed / max(n_cells, 1), 1),
                }
            )
        rows[name] = entries
    return rows


def _available_criteria(adata: Any, qc: dict[str, Any]) -> dict[str, str]:
    """Which cuts this matrix can actually support, by column presence.

    `run_qc_metrics` leaves `pct_counts_mt` out entirely when the species was
    unresolved or no gene matched the prefix, rather than reporting a false 0.
    A threshold on a column that is not there has to fail loudly, not quietly
    pass every cell.
    """
    return {
        name: column
        for name, (column, _) in CRITERIA.items()
        if column in adata.obs
    }


def _mask_for(adata: Any, thresholds: dict[str, Any], available: dict[str, str]) -> tuple[Any, dict[str, int]]:
    """Boolean keep-mask, plus how many cells each criterion rejected on its own.

    The per-criterion counts overlap: a cell can fail two cuts, and is counted
    in both. That is deliberate — "which criterion is doing the work" is the
    question being answered, not a partition.
    """
    import numpy as np

    keep = np.ones(adata.n_obs, dtype=bool)
    attribution: dict[str, int] = {}
    for name, value in thresholds.items():
        if value is None:
            continue
        column = available[name]
        values = np.asarray(adata.obs[column], dtype=float)
        failing = values < value if CRITERIA[name][1] == "min" else values > value
        attribution[name] = int(failing.sum())
        keep &= ~failing
    return keep, attribution


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    config = payload.get("config") or {}
    warnings: list[str] = []
    notes: list[str] = []

    source = _resolve_adata_path(payload)
    if not source:
        return _result(errors=["no AnnData path; run_qc_metrics must run first"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, _ = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])
    if adata.n_obs == 0:
        return _result(errors=[f"{source} contains no cells"])

    available = _available_criteria(adata, config)
    if not available:
        return _result(
            errors=[
                "the matrix carries no QC columns to filter on; run_qc_metrics "
                "must run before this step"
            ]
        )

    requested = {name: config.get(name) for name in CRITERIA if config.get(name) is not None}

    # A threshold on a metric that was never computed cannot be honoured. Passing
    # every cell would look identical to a filter that found nothing to remove.
    unusable = sorted(set(requested) - set(available))
    if unusable:
        return _result(
            errors=[
                f"cannot apply {', '.join(unusable)}: the matching QC column is not in "
                f"this matrix. run_qc_metrics leaves it out when the species was "
                f"unresolved or no gene matched — fix that rather than filtering on "
                f"a number that does not exist"
            ]
        )

    evidence = {
        "n_cells": int(adata.n_obs),
        "distributions": {
            name: _distribution(adata.obs[column]) for name, column in available.items()
        },
        "preview": _preview(adata, available),
        "criteria_available": sorted(available),
    }

    samples = (
        sorted(str(s) for s in adata.obs["sample"].unique())
        if "sample" in adata.obs
        else []
    )
    if samples:
        evidence["per_sample_distributions"] = {
            sample: {
                name: _distribution(adata.obs.loc[adata.obs["sample"] == sample, column])
                for name, column in available.items()
            }
            for sample in samples
        }

    # ---- nothing chosen: measure, report, and stop -------------------------
    if not requested:
        headline = []
        for name, column in sorted(available.items()):
            pct = evidence["distributions"][name]["percentiles"]
            headline.append(f"{name.replace('_', ' ')} median {pct['50']}")
        warnings.append(
            "no QC thresholds chosen, so nothing was filtered. "
            + "; ".join(headline)
            + ". Set min_genes / min_counts / max_pct_mito / max_pct_erythroid "
            "— a single value for every sample, or a mapping per sample — and re-run"
        )
        return _result(
            filter_state="needs_review",
            evidence=evidence,
            warnings=warnings,
            next_tool="human_review",
            metrics={"n_cells": int(adata.n_obs), "criteria_available": sorted(available)},
        )

    # ---- thresholds given: apply them --------------------------------------
    per_sample: dict[str, Any] = {}
    if samples and any(isinstance(v, dict) for v in requested.values()):
        import numpy as np

        keep = np.zeros(adata.n_obs, dtype=bool)
        attribution: dict[str, int] = {}
        for sample in samples:
            in_sample = np.asarray(adata.obs["sample"] == sample)
            sample_thresholds = {
                name: _for(value, sample) for name, value in requested.items()
            }
            sample_thresholds = {k: v for k, v in sample_thresholds.items() if v is not None}
            if not sample_thresholds:
                warnings.append(
                    f"{sample}: no threshold given for this sample, so every cell in "
                    "it was kept while others were filtered"
                )
                keep |= in_sample
                per_sample[sample] = {
                    "thresholds": {},
                    "n_before": int(in_sample.sum()),
                    "n_after": int(in_sample.sum()),
                }
                continue
            subset = adata[in_sample]
            sample_keep, sample_attr = _mask_for(subset, sample_thresholds, available)
            indices = np.flatnonzero(in_sample)
            keep[indices[sample_keep]] = True
            for name, count in sample_attr.items():
                attribution[name] = attribution.get(name, 0) + count
            per_sample[sample] = {
                "thresholds": sample_thresholds,
                "n_before": int(in_sample.sum()),
                "n_after": int(sample_keep.sum()),
                "removed_by": sample_attr,
            }
    else:
        flat = {name: _for(value, samples[0] if samples else "") for name, value in requested.items()}
        flat = {k: v for k, v in flat.items() if v is not None}
        keep, attribution = _mask_for(adata, flat, available)
        requested = flat
        for sample in samples:
            in_sample = np.asarray(adata.obs["sample"] == sample)
            per_sample[sample] = {
                "thresholds": flat,
                "n_before": int(in_sample.sum()),
                "n_after": int((in_sample & keep).sum()),
            }

    n_before = int(adata.n_obs)
    n_after = int(keep.sum())
    if n_after == 0:
        return _result(
            errors=[
                f"these thresholds remove every one of the {n_before:,} cells "
                f"({json.dumps(requested, default=str)}). The evidence table shows what "
                f"each value would cost on its own"
            ],
            evidence=evidence,
        )

    removed = n_before - n_after
    if removed / n_before > DRASTIC_REMOVAL_FRACTION:
        warnings.append(
            f"{removed:,} of {n_before:,} cells removed ({removed / n_before:.0%}). "
            "That may be right for a contaminated sample, but it is most of the data — "
            "check the per-criterion breakdown before continuing"
        )
    for sample, entry in per_sample.items():
        if entry["n_before"] and entry["n_after"] / entry["n_before"] < 0.25:
            warnings.append(
                f"{sample}: only {entry['n_after']:,} of {entry['n_before']:,} cells "
                "survived; a threshold set for the whole run can be far harsher on "
                "one library than another"
            )

    filtered = adata[keep].copy()
    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(filtered, out_dir / "adata.h5ad")

    summary = {
        "n_before": n_before,
        "n_after": n_after,
        "n_removed": removed,
        "pct_removed": round(100 * removed / n_before, 1),
        # Overlapping counts: a cell failing two cuts appears in both.
        "removed_by_criterion": attribution,
    }
    return _result(
        adata_path=adata_path,
        filter_state="applied",
        filter_summary=summary,
        thresholds={**requested, "chosen_by": "operator"},
        per_sample=per_sample,
        evidence=evidence,
        warnings=warnings,
        notes=notes,
        next_tool="detect_doublets",
        metrics={**summary, "n_samples": len(per_sample)},
    )


def _result(
    *,
    adata_path: str | None = None,
    filter_state: str = "needs_review",
    filter_summary: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    per_sample: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        # `applied` / `needs_review` — the graph routes on this, so an unfiltered
        # object can never reach the mainline by being accepted at the gate.
        "filter_state": filter_state,
        "filter_summary": filter_summary or {},
        "thresholds": thresholds or {},
        "per_sample": per_sample or {},
        "evidence": evidence or {},
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
    parser.add_argument("--min-genes", type=float)
    parser.add_argument("--min-counts", type=float)
    parser.add_argument("--max-pct-mito", type=float)
    parser.add_argument("--max-pct-erythroid", type=float)
    args = parser.parse_args(argv)

    result = run(
        {
            "artifacts": {"run_qc_metrics": {"adata_path": args.adata}},
            "run_dir": args.run_dir,
            "config": {
                "min_genes": args.min_genes,
                "min_counts": args.min_counts,
                "max_pct_mito": args.max_pct_mito,
                "max_pct_erythroid": args.max_pct_erythroid,
            },
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
