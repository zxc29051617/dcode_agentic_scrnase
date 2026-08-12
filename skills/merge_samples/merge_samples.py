"""Concatenate the loaded samples into one AnnData, labelled by sample.

Everything before this point is per-sample: Cell Ranger counts each library,
each matrix is classified and loaded on its own, and cell calling reads a
barcode-rank curve that belongs to one library. Everything after is one object —
QC, normalisation, clustering and `run_integration`, which exists precisely to
correct the batch effect this step creates.

It runs even for a single sample. Downstream then never has to ask how many
there were, and the `sample` column is there either way.

## The two things that go silently wrong

**Barcodes repeat between samples.** `AAACCCAAGAAACACT-1` is a valid 10x barcode
in every library ever made. Concatenating without disambiguating merges cells
that have nothing to do with each other, and nothing downstream can tell.

**Gene sets can differ.** `anndata.concat` defaults to an inner join, so two
matrices counted against different references quietly become their
intersection. A run can lose thousands of genes and report nothing. This step
refuses instead.

Run standalone:
    python skills/merge_samples/merge_samples.py A=a.h5ad B=b.h5ad --run-dir <out>
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

TOOL_NAME = "merge_samples"
INPUT_FIELDS = (
    "artifacts.cell_calling_review",
    "artifacts.load_filtered_counts",
    "artifacts.load_raw_counts",
    "run_dir",
)
OUTPUT_FIELDS = (
    "adata_path",
    "n_samples",
    "n_cells",
    "n_genes",
    "per_sample",
    "sample_key",
    "library_key",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Producers that can hand over loaded matrices, most specific first.
#: `cell_calling_review` wins over `load_raw_counts` because its output is the
#: subset of the other.
PRODUCERS = ("cell_calling_review", "load_filtered_counts", "load_raw_counts")

#: The obs column that says which library a cell came from. `LIBRARY_KEY` is
#: the name that means what it says; `SAMPLE_KEY` is kept as an alias written
#: alongside it, because objects and code from before this rename read it.
#:
#: It is emphatically **not** the key `run_integration` corrects on. It used to
#: be — `batch_key` defaulted to `"sample"` — which meant a study design was
#: read off a FASTQ filename and Harmony removed whatever the libraries differed
#: by, including the condition under study. The batch to correct is
#: `technical_batch`, which exists only when a validated manifest declared it.
LIBRARY_KEY = "library_id"
SAMPLE_KEY = "sample"

#: Written to `obs` from the manifest, one value per cell, resolved by library.
#: `library_id` is excluded: it is the join key and is written separately.
DESIGN_COLUMNS = ("sample_id", "donor_id", "condition", "technical_batch")


def _incoming(payload: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    """`{sample: adata path}` from whichever step loaded them."""
    artifacts = payload.get("artifacts") or {}
    for step in PRODUCERS:
        source = artifacts.get(step) or {}
        paths = source.get("adata_paths")
        if paths:
            return {str(k): str(v) for k, v in paths.items()}, step
        single = source.get("adata_path")
        if single:
            # A one-sample run still gets a label, so downstream is uniform.
            name = source.get("sample") or "sample1"
            return {str(name): str(single)}, step
    config_paths = (payload.get("config") or {}).get("adata_paths")
    if config_paths:
        return {str(k): str(v) for k, v in config_paths.items()}, "config"
    return {}, None


def _apply_design(merged: Any, payload: dict[str, Any], names: list[str]) -> list[str]:
    """Write the declared study design onto the cells, or say why there is none.

    Joined on `library_id` and nothing else. There is no positional fallback and
    no near-match: `study_design` has already been validated against the
    libraries this run found, so a name that is not in it here would be a wiring
    mistake rather than something to paper over.

    Values are written as `category` with real nulls, never the string
    "unknown" — a placeholder string is a value, and a value can silently become
    a batch to correct on.
    """
    import pandas as pd

    design = payload.get("study_design") or {}
    by_library = design.get("by_library") or {}
    if not by_library:
        if len(names) > 1:
            return [
                f"{len(names)} libraries were merged with no study design attached, so "
                f"donor, condition and technical batch are unknown for every cell. "
                f"Integration cannot run without them; pass --sample-manifest to "
                f"declare them"
            ]
        return []

    labels = merged.obs[SAMPLE_KEY].astype(str)
    for column in DESIGN_COLUMNS:
        mapping = {lib: (values or {}).get(column) for lib, values in by_library.items()}
        merged.obs[column] = pd.Categorical(labels.map(mapping))
    return []


def run(payload: dict[str, Any]) -> dict[str, Any]:
    import anndata

    warnings: list[str] = []
    incoming, producer = _incoming(payload)
    if not incoming:
        return _result(errors=["nothing to merge; no loader ran before this"])

    missing = [f"{name}: {path}" for name, path in incoming.items() if not Path(path).exists()]
    if missing:
        return _result(errors=[f"loaded matrices are gone: {'; '.join(missing)}"])

    loaded: dict[str, Any] = {}
    for name, path in sorted(incoming.items()):
        try:
            adata, _ = matrix_io.load_matrix(path)
        except Exception as exc:  # noqa: BLE001
            return _result(errors=[f"cannot load {name} from {path}: {type(exc).__name__}: {exc}"])
        if adata.n_obs == 0:
            return _result(errors=[f"{name} has no cells"])
        loaded[name] = adata

    # --- the gene sets must actually match ---------------------------------
    gene_sets = {name: set(adata.var_names) for name, adata in loaded.items()}
    reference_name, reference_genes = next(iter(gene_sets.items()))
    mismatched = {
        name: genes for name, genes in gene_sets.items() if genes != reference_genes
    }
    if mismatched:
        shared = set.intersection(*gene_sets.values())
        return _result(
            errors=[
                "the samples do not share a gene set, so merging them would "
                "silently reduce every one to their intersection ("
                + ", ".join(f"{n}: {len(g):,} genes" for n, g in sorted(gene_sets.items()))
                + f"; only {len(shared):,} in common). Count them against the same "
                "reference, or merge them yourself and say what you intended"
            ]
        )

    # --- and the same genome, where the matrices record one ----------------
    genomes = {
        name: sorted(matrix_io.recorded_genomes(adata))
        for name, adata in loaded.items()
        if matrix_io.recorded_genomes(adata)
    }
    distinct = {tuple(v) for v in genomes.values()}
    if len(distinct) > 1:
        return _result(
            errors=[
                "the samples were counted against different genomes: "
                + "; ".join(f"{n}={'/'.join(g)}" for n, g in sorted(genomes.items()))
            ]
        )

    # --- concatenate --------------------------------------------------------
    # `index_unique` suffixes every barcode with its sample. Without it two
    # libraries that both contain AAACCCAAGAAACACT-1 — and they all do — merge
    # those cells into one.
    names = sorted(loaded)
    merged = anndata.concat(
        [loaded[name] for name in names],
        label=SAMPLE_KEY,
        keys=names,
        index_unique="-" if len(names) > 1 else None,
        join="outer",
        merge="first",
    )
    merged.obs[SAMPLE_KEY] = merged.obs[SAMPLE_KEY].astype("category")
    # The same values under the name that says what they are. Downstream steps
    # asking "which library" should read this one; `sample` stays for callers
    # written before the distinction existed.
    merged.obs[LIBRARY_KEY] = merged.obs[SAMPLE_KEY]

    design_warnings = _apply_design(merged, payload, names)
    warnings.extend(design_warnings)

    duplicated = int(merged.n_obs - len(set(merged.obs_names)))
    if duplicated:
        merged.obs_names_make_unique()
        warnings.append(f"{duplicated:,} barcodes were still duplicated after suffixing")

    per_sample = {
        name: {
            "n_cells": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "source": incoming[name],
        }
        for name, adata in sorted(loaded.items())
    }
    if len(names) > 1:
        counts = [v["n_cells"] for v in per_sample.values()]
        if max(counts) > 10 * max(min(counts), 1):
            warnings.append(
                f"cell counts differ by more than tenfold across samples "
                f"({min(counts):,} to {max(counts):,}); the largest will dominate "
                "clustering unless integration accounts for it"
            )

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    adata_path = matrix_io.write_h5ad(merged, out_dir / "adata.h5ad")

    return _result(
        adata_path=adata_path,
        n_samples=len(names),
        n_cells=int(merged.n_obs),
        n_genes=int(merged.n_vars),
        per_sample=per_sample,
        producer=producer,
        warnings=warnings,
        next_tool="post_load_validate",
        metrics={
            "n_samples": len(names),
            "n_cells": int(merged.n_obs),
            "n_genes": int(merged.n_vars),
            "cells_per_sample": {n: v["n_cells"] for n, v in per_sample.items()},
        },
    )


def _result(
    *,
    adata_path: str | None = None,
    n_samples: int = 0,
    n_cells: int | None = None,
    n_genes: int | None = None,
    per_sample: dict[str, Any] | None = None,
    producer: str | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "adata_path": adata_path,
        "n_samples": n_samples,
        "n_cells": n_cells,
        "n_genes": n_genes,
        "per_sample": per_sample or {},
        # Which column says "this cell came from that library", named once here
        # so no downstream step has to guess it. Not the key `run_integration`
        # corrects on — that is `technical_batch`, and conflating the two is
        # what let a study design be inferred from a filename.
        "sample_key": SAMPLE_KEY,
        "library_key": LIBRARY_KEY,
        "producer": producer,
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("samples", nargs="+", metavar="NAME=PATH")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    paths = {}
    for item in args.samples:
        if "=" not in item:
            parser.error(f"expected NAME=PATH, got {item!r}")
        name, _, path = item.partition("=")
        paths[name] = path

    result = run({"config": {"adata_paths": paths}, "run_dir": args.run_dir})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
