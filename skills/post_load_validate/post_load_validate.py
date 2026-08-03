"""The merge point: guarantee one shape of AnnData whichever route produced it.

Three steps can hand a matrix to the mainline — `load_filtered_counts`,
`cell_calling_review`, and (for a raw matrix a person had already called cells
on) `load_raw_counts`. Three producers and one consumer is exactly when the
consumer starts growing per-route special cases, so everything downstream of
here is promised the same object instead.

What is promised:

  * unique `obs_names` and `var_names`
  * `X` holds raw integer counts, not something already normalised
  * **the same counts also in `layers["counts"]`** — normalisation overwrites
    `X` in place, so without a copy the raw values are gone by the time
    anything wants to go back to them
  * every barcode has at least one count
  * gene ids alongside gene symbols, where the source had them
  * the species, cross-checked against whatever the data itself records

That last one is the check the count-matrix route otherwise had nowhere to put.
A 10x `.h5` records its reference in `var['genome']`; an mtx directory records
nothing, so verification is skipped rather than faked.

Run standalone:
    python skills/post_load_validate/post_load_validate.py <adata.h5ad> --run-dir <out>
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
from src import species as species_table  # noqa: E402

TOOL_NAME = "post_load_validate"
INPUT_FIELDS = (
    "artifacts.cell_calling_review",
    "artifacts.load_filtered_counts",
    "artifacts.load_raw_counts",
    "artifacts.matrix_preflight",
    "artifacts.resolve_reference",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "n_cells",
    "n_genes",
    "genome",
    "species_verified",
    "normalizations",
    "notes",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Steps that can produce the matrix, most specific first. `cell_calling_review`
#: wins over `load_raw_counts` because its output is the subset of the other.
PRODUCERS = ("cell_calling_review", "load_filtered_counts", "load_raw_counts")


def _incoming(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    artifacts = payload.get("artifacts") or {}
    for step in PRODUCERS:
        path = (artifacts.get(step) or {}).get("adata_path")
        if path:
            return str(path), step
    return (payload.get("config") or {}).get("adata_path"), "config"


def _looks_like_counts(matrix: Any) -> tuple[bool, str | None]:
    """Raw counts are non-negative whole numbers. Anything else came pre-cooked.

    Feeding log-normalised values into a counts pipeline produces plots that
    look fine and numbers that mean nothing, so it is worth one pass over the
    data to refuse.
    """
    import numpy as np
    import scipy.sparse as sp

    values = matrix.data if sp.issparse(matrix) else np.asarray(matrix).ravel()
    if values.size == 0:
        return True, None
    if values.min() < 0:
        return False, "the matrix contains negative values, so it is not raw counts"
    sample = values if values.size <= 1_000_000 else values[:: max(1, values.size // 1_000_000)]
    if not bool(np.all(sample == np.floor(sample))):
        return False, (
            "the matrix holds fractional values, so it has already been normalised "
            "or transformed — the mainline expects raw counts"
        )
    return True, None


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    warnings: list[str] = []
    notes: list[str] = []
    normalizations: list[str] = []

    source, producer = _incoming(payload)
    if not source:
        return _result(errors=["no AnnData to standardize; no loader ran before this"])
    if not Path(source).expanduser().exists():
        return _result(errors=[f"AnnData does not exist: {source}"])

    try:
        adata, provenance = matrix_io.load_matrix(source)
    except Exception as exc:  # noqa: BLE001
        return _result(errors=[f"cannot load {source}: {type(exc).__name__}: {exc}"])

    if adata.n_obs == 0:
        return _result(errors=[f"{source} contains no cells"])

    # --- counts, not something already processed ----------------------------
    ok, problem = _looks_like_counts(adata.X)
    if not ok:
        return _result(errors=[f"{problem} ({source})"])

    # --- unique names -------------------------------------------------------
    for axis, names in (("obs", adata.obs_names), ("var", adata.var_names)):
        duplicates = int(len(names) - len(set(names)))
        if duplicates:
            if axis == "obs":
                adata.obs_names_make_unique()
            else:
                adata.var_names_make_unique()
            normalizations.append(f"made {duplicates:,} duplicate {axis}_names unique")

    # --- every barcode has counts -------------------------------------------
    totals = matrix_io.total_counts(adata)
    n_empty = int((totals == 0).sum())
    if n_empty:
        adata = adata[totals > 0].copy()
        normalizations.append(f"dropped {n_empty:,} barcodes with no counts")
        warnings.append(
            f"{n_empty:,} barcodes had no counts and were dropped here; cell calling "
            "should have removed them upstream"
        )

    # --- keep the counts reachable after normalisation ----------------------
    # `normalize_hvg_prepare` writes normalised values into X. Anything that
    # needs the originals afterwards — differential expression, re-filtering,
    # export — has nowhere to look unless a copy is put aside first, and by then
    # it is too late.
    if "counts" not in adata.layers:
        adata.layers["counts"] = adata.X.copy()
        normalizations.append("copied raw counts into layers['counts']")

    # --- the genome these counts were made against --------------------------
    genomes = matrix_io.recorded_genomes(adata)
    artifacts = payload.get("artifacts") or {}
    entry = (artifacts.get("matrix_preflight") or {}) or (artifacts.get("resolve_reference") or {})
    declared = species_table.canonical(
        entry.get("declared_species") or (payload.get("config") or {}).get("species")
    )
    verified, verify_errors, verify_warnings, verify_notes = _verify_genome(genomes, declared)
    warnings.extend(verify_warnings)
    notes.extend(verify_notes)
    if verify_errors:
        return _result(errors=verify_errors, genome=sorted(genomes))

    if "gene_ids" not in adata.var:
        notes.append(
            "the matrix carries no gene ids, only symbols; downstream annotation "
            "cannot fall back to a stable identifier"
        )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(adata, out_dir / "adata.h5ad")

    return _result(
        adata_path=adata_path,
        n_cells=int(adata.n_obs),
        n_genes=int(adata.n_vars),
        genome=sorted(genomes),
        species_verified=verified,
        normalizations=normalizations,
        source={"producer": producer, **provenance},
        notes=notes,
        warnings=warnings,
        next_tool="run_qc_metrics",
        metrics={
            "n_cells": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "median_umi_per_cell": int(np.median(matrix_io.total_counts(adata))),
            "median_genes_per_cell": int(np.median(matrix_io.genes_per_barcode(adata))),
            "n_normalizations": len(normalizations),
            "species_verified": verified,
        },
    )


def _verify_genome(
    genomes: set[str], declared: str | None
) -> tuple[bool, list[str], list[str], list[str]]:
    """Cross-check the matrix's own genome against the declared species.

    Returns (verified, errors, warnings, notes). Being unable to verify is a
    *note*: most public data ships as mtx, which records no genome at all, and
    stopping every such run at the gate would teach people to click through it.
    A declared species that contradicts the data is an error.
    """
    if not genomes:
        return False, [], [], [
            "the matrix does not record which genome it was counted against, so "
            "the declared species could not be cross-checked (normal for an mtx "
            "directory)"
        ]

    seen = species_table.identify_reference({"genomes": sorted(genomes)})
    if not seen:
        return False, [], [], [
            f"the genome {', '.join(sorted(genomes))} is not one this project "
            "recognises; species verification skipped (normal for a custom build)"
        ]
    if len(seen) > 1:
        return False, [], [], [
            f"the matrix matches multiple species ({', '.join(sorted(seen))}) — "
            "barnyard/PDX; species verification skipped"
        ]
    found = seen.pop()
    if declared is None:
        # Actionable, unlike the cases above: the data says what it is and the
        # run just has not been told, so this one is worth stopping for.
        return False, [], [
            f"no species declared; these counts were made against {found}. "
            "Set config.species so the two can be cross-checked"
        ], []
    if found != declared:
        return False, [
            f"species mismatch: the run declares {declared!r} but these counts were "
            f"made against a {found!r} genome ({', '.join(sorted(genomes))}). Every "
            f"number downstream would be filed under the wrong organism"
        ], [], []
    return True, [], [], []


def _result(
    *,
    adata_path: str | None = None,
    n_cells: int | None = None,
    n_genes: int | None = None,
    genome: list[str] | None = None,
    species_verified: bool = False,
    normalizations: list[str] | None = None,
    source: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "n_cells": n_cells,
        "n_genes": n_genes,
        "genome": genome or [],
        "species_verified": species_verified,
        # What had to be changed to meet the contract. Empty is the good case,
        # and a non-empty list is worth reading: it means a producer upstream
        # emitted something the mainline could not have used as-is.
        "normalizations": normalizations or [],
        "source": source or {},
        "notes": notes or [],
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("adata")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--species")
    args = parser.parse_args(argv)
    result = run(
        {
            "artifacts": {"load_filtered_counts": {"adata_path": args.adata}},
            "run_dir": args.run_dir,
            "config": {"species": args.species},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
