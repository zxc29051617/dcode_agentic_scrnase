"""Reading count matrices, and the barcode-rank evidence cell calling needs.

Shared by `load_raw_counts`, `load_filtered_counts` and `cell_calling_review`.
Like `species.py` this is a helper, not a step: it holds no policy and makes no
decisions, so every caller can be read on its own terms.

**AnnData travels as a path, not an object.** The graph state is JSON in an
audit log, and a matrix is not. Each step writes `<run_dir>/<step>/adata.h5ad`
and passes the path on, which also makes every step resumable from disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: A barcode-rank curve is read on log-log axes, so ranks are sampled
#: geometrically rather than evenly — the interesting structure is in the first
#: few thousand barcodes.
RANK_PROBES = (10, 100, 500, 1_000, 2_000, 5_000, 10_000, 50_000, 100_000)

#: No cell caller returns more than this, so the cliff is searched no further.
#: Past it the curve is ambient droplets, where counts step from 2 to 1 to 0 —
#: vertical on a log axis, and the steepest thing on the plot if you let the
#: search reach it. Matches the ceiling `count_matrix_classify` uses.
MAX_PLAUSIBLE_CELLS = 50_000

#: Below this the curve is quantisation noise, not structure.
MIN_CLIFF_RANK = 10


def load_matrix(path: str | Path) -> tuple[Any, dict[str, Any]]:
    """Read a 10x `.h5`, an mtx directory, or an `.h5ad` into AnnData.

    Returns the object plus provenance describing what was actually read, so a
    later step never has to guess which format the counts came from.
    """
    import warnings

    import anndata
    import scanpy as sc

    path = Path(path).expanduser()
    with warnings.catch_warnings():
        # A 10x reference legitimately carries duplicate gene symbols; the reader
        # warns before handing them over, and the next line is the fix.
        warnings.filterwarnings("ignore", message=".*not unique.*")
        if path.is_dir():
            adata = sc.read_10x_mtx(path, var_names="gene_symbols", make_unique=True, cache=False)
            source_format = "mtx_dir"
        elif path.name.endswith(".h5ad"):
            adata = anndata.read_h5ad(path)
            source_format = "h5ad"
        elif path.name.endswith(".h5"):
            adata = sc.read_10x_h5(path)
            adata.var_names_make_unique()
            source_format = "10x_h5"
        else:
            raise ValueError(f"unsupported matrix format: {path}")

    provenance = {
        "source_path": str(path),
        "source_format": source_format,
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
    }
    return adata, provenance


def write_h5ad(adata: Any, path: str | Path) -> str:
    """Persist an AnnData so the next step can pick it up by path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(path, compression="gzip")
    return str(path)


def total_counts(adata: Any) -> Any:
    """UMIs per barcode, as a dense 1-D array."""
    import numpy as np
    import scipy.sparse as sp

    matrix = adata.X
    if sp.issparse(matrix):
        return np.asarray(matrix.sum(axis=1)).ravel()
    return np.asarray(matrix.sum(axis=1)).ravel()


def genes_per_barcode(adata: Any) -> Any:
    """Detected genes per barcode, as a dense 1-D array."""
    import numpy as np
    import scipy.sparse as sp

    matrix = adata.X
    if sp.issparse(matrix):
        return np.asarray((matrix > 0).sum(axis=1)).ravel()
    return np.asarray((matrix > 0).sum(axis=1)).ravel()


def barcode_rank_evidence(totals: Any, *, probes: tuple[int, ...] = RANK_PROBES) -> dict[str, Any]:
    """Describe the barcode-rank curve so a person can choose a cutoff.

    The "cliff" is the steepest point of the log-log curve — where UMI counts
    fall away fastest, which is the usual visual cue for where cells end and
    ambient droplets begin. A curve with no sharp cliff is exactly the case
    where a human should pick the number instead of an algorithm, so the
    steepness is reported rather than just its location.
    """
    import numpy as np

    ordered = np.sort(np.asarray(totals))[::-1]
    nonzero = ordered[ordered > 0]
    evidence: dict[str, Any] = {
        "n_barcodes": int(ordered.size),
        "n_nonzero": int(nonzero.size),
        "total_umi": int(ordered.sum()),
        "max_umi": int(ordered[0]) if ordered.size else 0,
        "umi_at_rank": {
            str(rank): int(ordered[rank - 1]) for rank in probes if rank <= ordered.size
        },
    }
    if nonzero.size < MIN_CLIFF_RANK * 2:
        return evidence

    # Sample on a uniform log-rank grid. A window that is constant in rank space
    # shrinks in log space as rank grows, which makes the far tail look
    # arbitrarily steep; a constant log-space step makes slopes comparable.
    upper = min(nonzero.size, MAX_PLAUSIBLE_CELLS)
    grid = np.unique(
        np.round(np.logspace(np.log10(MIN_CLIFF_RANK), np.log10(upper), 300)).astype(int)
    )
    log_rank = np.log10(grid)
    log_umi = np.log10(nonzero[grid - 1])
    slope = np.diff(log_umi) / np.diff(log_rank)

    steepest = int(np.argmin(slope))
    cliff_rank = int(grid[steepest + 1])

    # How far UMIs fall across the cliff, comparing an octave either side. The
    # raw log-log slope is not reported: it depends on where in the searched
    # range the cliff sits, so the same curve scores differently for reasons
    # that have nothing to do with the data. A ratio is comparable between runs
    # and says something a person can act on — "counts drop 400x here".
    below = nonzero[max(cliff_rank // 2, 1) - 1]
    above_rank = min(cliff_rank * 2, nonzero.size)
    above = nonzero[above_rank - 1]

    evidence.update(
        {
            "cliff_rank": cliff_rank,
            "cliff_umi": int(nonzero[cliff_rank - 1]),
            "cliff_drop_ratio": round(float(below / max(above, 1)), 1),
            "cliff_searched_to_rank": int(upper),
            "median_umi_top_1000": int(np.median(ordered[: min(1000, ordered.size)])),
        }
    )
    return evidence


def select_barcodes(
    totals: Any,
    *,
    force_cells: int | None = None,
    min_umi: int | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Choose which barcodes are cells, by count or by threshold.

    `force_cells` takes the top N barcodes by UMI, which is exactly what Cell
    Ranger's own `--force-cells` does — and, like it, bypasses the EmptyDrops
    test that rescues low-UMI barcodes whose expression differs from ambient
    RNA. The caller is responsible for saying so.

    Returns a boolean mask and a description of how it was made.
    """
    import numpy as np

    totals = np.asarray(totals)
    if force_cells is not None:
        if force_cells <= 0:
            raise ValueError(f"force_cells must be positive, got {force_cells}")
        keep = min(int(force_cells), int((totals > 0).sum()))
        cutoff_rank = np.argsort(-totals, kind="stable")[:keep]
        mask = np.zeros(totals.size, dtype=bool)
        mask[cutoff_rank] = True
        threshold = int(totals[mask].min()) if keep else 0
        return mask, {
            "method": "force_cells",
            "requested": int(force_cells),
            "selected": int(mask.sum()),
            "umi_threshold": threshold,
        }

    if min_umi is not None:
        mask = totals >= int(min_umi)
        return mask, {
            "method": "min_umi",
            "umi_threshold": int(min_umi),
            "selected": int(mask.sum()),
        }

    raise ValueError("select_barcodes needs either force_cells or min_umi")
