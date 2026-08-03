"""Decide whether a count matrix is pre- or post-cell-calling.

Upstream steps supply *hints*: `ingest_validate` reads the Cell Ranger naming
convention, `cellranger_count` knows which file it just wrote. This step turns a
hint into a decision, and it does so from the matrix itself — because a file can
be renamed, moved, or pointed at by mistake, and getting this wrong means either
running the mainline on 300,000 empty droplets or reviewing cell calling that
was already done.

The definitive evidence is empty barcodes. A raw matrix is the whole observed
barcode list, so it contains droplets with zero detected genes; a filtered
matrix, by construction, contains none. That test costs one read of the `indptr`
array — the per-barcode offsets — without touching the counts themselves.

Run standalone:
    python skills/count_matrix_classify/count_matrix_classify.py <matrix>
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

TOOL_NAME = "count_matrix_classify"
INPUT_FIELDS = (
    "input_bundle",
    "artifacts.ingest_validate",
    "artifacts.cellranger_count",
)
OUTPUT_FIELDS = (
    "matrix_class",
    "evidence",
    "needs_cell_calling",
    "matrix_path",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: Above this many barcodes, no cell caller produced the list. The 10x v3
#: whitelist alone is ~3M, and a raw matrix keeps every barcode that was seen.
RAW_BARCODE_THRESHOLD = 100_000

#: Below this, a barcode list is plausibly a set of called cells. Deliberately
#: far above a normal run (500-20,000) so a superloaded one is not misread.
FILTERED_BARCODE_CEILING = 50_000


# --------------------------------------------------------------------------
# Reading evidence out of each matrix format
# --------------------------------------------------------------------------


def _evidence_from_10x_h5(path: Path) -> dict[str, Any]:
    """Barcode count and empty-droplet count, from `indptr` alone.

    The 10x HDF5 matrix is CSC with barcodes as columns, so the number of
    detected genes per barcode is the difference between consecutive `indptr`
    entries. Reading that one array answers the question without decompressing
    a single count.
    """
    import h5py
    import numpy as np

    with h5py.File(path, "r") as handle:
        group = handle.get("matrix")
        if group is None:  # CR2 wrote one group per genome
            names = [k for k in handle.keys() if "barcodes" in handle[k]]
            if not names:
                raise ValueError("no matrix group found in the HDF5 file")
            group = handle[names[0]]

        n_barcodes = int(group["barcodes"].shape[0])
        n_features = int(group["features/id"].shape[0]) if "features" in group else None
        indptr = group["indptr"][:]

    genes_per_barcode = np.diff(indptr)
    return {
        "format": "10x_h5",
        "n_barcodes": n_barcodes,
        "n_features": n_features,
        "n_empty_barcodes": int((genes_per_barcode == 0).sum()),
        "median_genes_per_barcode": int(np.median(genes_per_barcode)),
        "min_genes_per_barcode": int(genes_per_barcode.min()) if len(genes_per_barcode) else None,
    }


def _count_lines(path: Path) -> int:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", errors="replace") as handle:
        return sum(1 for _ in handle)


def _evidence_from_mtx_dir(directory: Path) -> dict[str, Any]:
    """Dimensions come from the MatrixMarket header, not the whole file.

    The first non-comment line of an `.mtx` is `n_features n_barcodes nnz`. When
    `nnz` is smaller than `n_barcodes`, some barcode must have no entries at all
    — the pigeonhole version of the empty-droplet test, for a fraction of the
    read cost.
    """
    barcodes = next(
        (directory / n for n in ("barcodes.tsv.gz", "barcodes.tsv") if (directory / n).exists()),
        None,
    )
    matrix = next(
        (directory / n for n in ("matrix.mtx.gz", "matrix.mtx") if (directory / n).exists()),
        None,
    )
    if barcodes is None or matrix is None:
        raise ValueError(f"{directory} is missing barcodes.tsv(.gz) or matrix.mtx(.gz)")

    n_barcodes = _count_lines(barcodes)

    n_features = nnz = None
    opener = gzip.open if matrix.name.endswith(".gz") else open
    with opener(matrix, "rt", errors="replace") as handle:
        for line in handle:
            if line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) == 3:
                n_features, _, nnz = int(parts[0]), int(parts[1]), int(parts[2])
            break

    evidence: dict[str, Any] = {
        "format": "mtx_dir",
        "n_barcodes": n_barcodes,
        "n_features": n_features,
        "nnz": nnz,
    }
    if nnz is not None and nnz < n_barcodes:
        evidence["n_empty_barcodes"] = n_barcodes - nnz
        evidence["n_empty_barcodes_is_lower_bound"] = True
    return evidence


def _evidence_from_h5ad(path: Path) -> dict[str, Any]:
    """`n_obs` from the index, plus per-cell totals only if already annotated.

    Computing counts from `X` would mean reading the whole matrix, so an h5ad
    with no QC columns is judged on its cell count alone and is more likely to
    come back `unknown`. That is the honest outcome, not a gap to paper over.
    """
    import h5py
    import numpy as np

    evidence: dict[str, Any] = {"format": "h5ad"}
    with h5py.File(path, "r") as handle:
        obs = handle.get("obs")
        if obs is not None:
            index_key = obs.attrs.get("_index", "_index")
            if isinstance(index_key, bytes):
                index_key = index_key.decode()
            index = obs.get(index_key)
            if index is not None:
                evidence["n_barcodes"] = int(index.shape[0])

            for column in ("total_counts", "n_genes", "n_genes_by_counts"):
                node = obs.get(column)
                if node is None or not hasattr(node, "shape"):
                    continue
                values = node[:]
                evidence["n_empty_barcodes"] = int((values == 0).sum())
                evidence["empty_from_column"] = column
                evidence[f"median_{column}"] = float(np.median(values))
                break

        var = handle.get("var")
        if var is not None:
            index_key = var.attrs.get("_index", "_index")
            if isinstance(index_key, bytes):
                index_key = index_key.decode()
            index = var.get(index_key)
            if index is not None:
                evidence["n_features"] = int(index.shape[0])
    return evidence


def gather_evidence(path: Path) -> dict[str, Any]:
    """Read whatever the format cheaply allows."""
    if path.is_dir():
        return _evidence_from_mtx_dir(path)
    if path.name.endswith(".h5ad"):
        return _evidence_from_h5ad(path)
    if path.name.endswith(".h5"):
        return _evidence_from_10x_h5(path)
    raise ValueError(f"unsupported matrix format: {path}")


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


def classify(evidence: dict[str, Any]) -> tuple[str, list[str]]:
    """Turn evidence into `raw` / `filtered` / `unknown`, with the reasons."""
    reasons: list[str] = []
    n_barcodes = evidence.get("n_barcodes")
    n_empty = evidence.get("n_empty_barcodes")

    if n_empty is not None and n_empty > 0:
        qualifier = " at least" if evidence.get("n_empty_barcodes_is_lower_bound") else ""
        reasons.append(
            f"{n_empty:,}{qualifier} barcodes have no detected genes; a cell caller "
            f"would have removed them"
        )
        return "raw", reasons

    if n_barcodes is None:
        reasons.append("the matrix does not expose a barcode count")
        return "unknown", reasons

    if n_barcodes >= RAW_BARCODE_THRESHOLD:
        reasons.append(
            f"{n_barcodes:,} barcodes is far more than any cell caller returns "
            f"(threshold {RAW_BARCODE_THRESHOLD:,})"
        )
        return "raw", reasons

    if n_barcodes <= FILTERED_BARCODE_CEILING:
        if n_empty == 0:
            reasons.append(
                f"{n_barcodes:,} barcodes and none empty — consistent with a called cell set"
            )
        else:
            reasons.append(
                f"{n_barcodes:,} barcodes is within the range a cell caller returns, "
                f"though emptiness could not be checked"
            )
        return "filtered", reasons

    reasons.append(
        f"{n_barcodes:,} barcodes falls between the filtered ceiling "
        f"({FILTERED_BARCODE_CEILING:,}) and the raw threshold "
        f"({RAW_BARCODE_THRESHOLD:,}); the count alone cannot decide"
    )
    return "unknown", reasons


def artifacts_of(payload: dict[str, Any], step: str) -> dict[str, Any]:
    return (payload.get("artifacts") or {}).get(step) or {}


def _hinted_paths_and_kind(payload: dict[str, Any]) -> tuple[dict[str, str], str | None, str]:
    """`{sample: path}`, and what upstream believes they are.

    Cell Ranger hands over one matrix per library; a person handing over a
    matrix directly hands over one. Both arrive here as a mapping so the rest of
    the step does not branch on how many there are.
    """
    artifacts = payload.get("artifacts") or {}
    for step in ("cellranger_count", "matrix_preflight", "ingest_validate"):
        source = artifacts.get(step) or {}
        hint = source.get("matrix_kind_hint") or source.get("matrix_kind")
        hint = hint if hint != "unknown" else None
        paths = source.get("matrix_paths")
        if paths:
            return {str(k): str(v) for k, v in paths.items()}, hint, step
        path = source.get("matrix_path")
        if path:
            return {"sample1": str(path)}, hint, step

    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return {"sample1": str(bundle)}, None, "input_bundle"
    raw = bundle.get("paths") or bundle.get("path") or []
    listed = [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]
    return ({"sample1": listed[0]} if listed else {}), None, "input_bundle"


def run(payload: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    incoming, hint, hint_source = _hinted_paths_and_kind(payload)

    if not incoming:
        return _result(errors=["no matrix to classify; nothing upstream supplied a path"])

    per_sample: dict[str, Any] = {}
    for name, raw_path in sorted(incoming.items()):
        path = Path(raw_path).expanduser()
        if not path.exists():
            return _result(errors=[f"matrix path does not exist ({name}): {path}"])
        try:
            sample_evidence = gather_evidence(path)
        except Exception as exc:  # noqa: BLE001 - an unreadable matrix is a finding
            return _result(errors=[f"cannot read {name} at {path}: {type(exc).__name__}: {exc}"])
        sample_class, sample_reasons = classify(sample_evidence)
        per_sample[name] = {
            "path": str(path),
            "matrix_class": sample_class,
            "reasons": sample_reasons,
            "evidence": sample_evidence,
        }

    # Samples that disagree cannot be merged: one has been through a cell caller
    # and the other has not, so combining them would mix cells with empty
    # droplets and nothing downstream could separate them again.
    classes = {v["matrix_class"] for v in per_sample.values()}
    if len(classes) > 1:
        return _result(
            errors=[
                "the samples are not all the same kind of matrix ("
                + ", ".join(f"{n}={v['matrix_class']}" for n, v in sorted(per_sample.items()))
                + "). Merging cells with empty droplets is not recoverable downstream"
            ],
            per_sample=per_sample,
        )

    first = next(iter(sorted(per_sample)))
    matrix_class = per_sample[first]["matrix_class"]
    reasons = per_sample[first]["reasons"]
    evidence = per_sample[first]["evidence"]
    path = Path(per_sample[first]["path"])
    if len(per_sample) > 1:
        evidence = {"n_samples": len(per_sample), "per_sample": {
            n: {k: v["evidence"].get(k) for k in ("n_barcodes", "n_empty_barcodes")}
            for n, v in sorted(per_sample.items())
        }}

    # Two different situations, and conflating them is what made this step read
    # as guesswork:
    #
    #   selection — Cell Ranger wrote BOTH matrices and said which one it is
    #               routing on. The kind is known; this step's job is to confirm
    #               the file matches that claim, not to rediscover it.
    #   discovery — someone handed over a matrix. Nothing knows what it is, so
    #               the contents are the only evidence there is.
    available = (artifacts_of(payload, "cellranger_count") or {}).get("available_matrices") or {}
    mode = "selection" if available else "discovery"
    evidence["mode"] = mode
    if available:
        evidence["available_matrices"] = sorted(available)

    # Either way, a claim that disagrees with the contents is a finding: it means
    # a file was renamed, moved, or pointed at by mistake.
    if hint and matrix_class != "unknown" and hint != matrix_class:
        detail = (
            f"{hint_source} selected the {hint!r} matrix"
            if mode == "selection"
            else f"{hint_source} called this matrix {hint!r}"
        )
        return _result(
            matrix_class="unknown",
            evidence=evidence,
            reasons=reasons,
            matrix_path=str(path),
            errors=[
                f"{detail} but its contents look {matrix_class!r}: "
                f"{'; '.join(reasons)}. Routing on either would be a guess — "
                f"confirm which file this actually is"
            ],
        )

    if matrix_class == "unknown":
        warnings.append(
            "raw vs filtered could not be decided from the matrix; the cell calling "
            "state is unresolved"
        )
    elif hint == matrix_class:
        evidence["hint_confirmed"] = (
            f"{hint_source} selected {hint!r}; the contents agree"
            if mode == "selection"
            else f"{hint_source} said {hint!r}, contents agree"
        )

    return _result(
        matrix_class=matrix_class,
        evidence=evidence,
        reasons=reasons,
        matrix_path=str(path),
        matrix_paths={n: v["path"] for n, v in sorted(per_sample.items())},
        per_sample=per_sample,
        warnings=warnings,
        next_tool={
            "raw": "load_raw_counts",
            "filtered": "load_filtered_counts",
        }.get(matrix_class, "human_review"),
    )


def _result(
    *,
    matrix_class: str = "unknown",
    evidence: dict[str, Any] | None = None,
    reasons: list[str] | None = None,
    matrix_path: str | None = None,
    matrix_paths: dict[str, str] | None = None,
    per_sample: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
) -> dict[str, Any]:
    evidence = evidence or {}
    return {
        "matrix_class": matrix_class,
        # The graph branches on this; `matrix_class` is the contract's name for
        # the same decision.
        "matrix_kind": matrix_class,
        "evidence": evidence,
        "reasons": reasons or [],
        "matrix_path": matrix_path,
        "matrix_paths": matrix_paths or ({"sample1": matrix_path} if matrix_path else {}),
        "per_sample": per_sample or {},
        # None, not False, when undecided: "we do not know" must never read as
        # "cell calling is already done".
        "needs_cell_calling": {"raw": True, "filtered": False}.get(matrix_class),
        "recommended_next_tool": next_tool,
        "metrics": {
            key: evidence[key]
            for key in (
                "n_barcodes",
                "n_features",
                "n_empty_barcodes",
                "median_genes_per_barcode",
            )
            if key in evidence
        },
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("matrix", help="a 10x .h5, an mtx directory, or an .h5ad")
    parser.add_argument("--hint", choices=["raw", "filtered"], help="what upstream believes")
    args = parser.parse_args(argv)

    source: dict[str, Any] = {"matrix_path": args.matrix}
    if args.hint:
        source["matrix_kind_hint"] = args.hint

    result = run({"artifacts": {"ingest_validate": source}, "config": {}})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
