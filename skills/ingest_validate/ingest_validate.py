"""Classify an input bundle so the orchestrator knows which route to take.

This is the first tool the workflow calls. It looks at the filesystem only —
it never loads a count matrix — and reports what it found plus how confident it
is. Anything it cannot resolve is returned as an error so the graph stops at the
human gate instead of guessing a route.

Run standalone:  python skills/ingest_validate/ingest_validate.py <path>...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import matrix_io  # noqa: E402

TOOL_NAME = "ingest_validate"
INPUT_FIELDS = (
    "input bundle",
    "optional sample metadata",
    "routing config",
)
OUTPUT_FIELDS = (
    "normalized state",
    "detected input type",
    "warnings",
    "errors",
    "recommended_next_tool",
)

FASTQ_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.IGNORECASE)
#: Illumina bcl2fastq naming: <sample>_S1_L001_R1_001.fastq.gz
ILLUMINA_RE = re.compile(
    r"^(?P<sample>.+?)_S\d+(?:_L(?P<lane>\d{3}))?_(?P<read>[RI][123])_\d{3}\.(fastq|fq)(\.gz)?$",
    re.IGNORECASE,
)

MTX_NAMES = ("matrix.mtx", "matrix.mtx.gz")
BARCODE_NAMES = ("barcodes.tsv", "barcodes.tsv.gz")
FEATURE_NAMES = ("features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz")

#: Cell Ranger writes both; the name is the only reliable raw/filtered signal.
RAW_TOKENS = ("raw_feature_bc_matrix", "raw_gene_bc_matrices", "raw_feature_bc_matrix.h5")
FILTERED_TOKENS = (
    "filtered_feature_bc_matrix",
    "filtered_gene_bc_matrices",
    "filtered_feature_bc_matrix.h5",
)
IGNORED_H5_NAMES = {"molecule_info.h5"}

#: Above this many barcodes a matrix is almost certainly an unfiltered barcode list.
RAW_BARCODE_THRESHOLD = 100_000


@dataclass
class Detection:
    """One recognized artifact inside the bundle."""

    path: str
    input_type: str  # "fastq" | "matrix"
    artifact_kind: str  # "fastq" | "mtx_dir" | "tenx_h5" | "h5ad"
    matrix_kind: str | None = None  # "raw" | "filtered" | "unknown"
    evidence: dict[str, Any] = field(default_factory=dict)


def _matrix_kind_from_name(path: Path) -> str:
    """Read raw/filtered off the Cell Ranger naming convention."""
    parts = [part.lower() for part in (path.name, *(p.name for p in path.parents[:2]))]
    for token in FILTERED_TOKENS:
        if any(token in part for part in parts):
            return "filtered"
    for token in RAW_TOKENS:
        if any(token in part for part in parts):
            return "raw"
    return "unknown"


def _has_any(directory: Path, names: tuple[str, ...]) -> bool:
    return any((directory / name).exists() for name in names)


def _is_mtx_dir(directory: Path) -> bool:
    return (
        _has_any(directory, MTX_NAMES)
        and _has_any(directory, BARCODE_NAMES)
        and _has_any(directory, FEATURE_NAMES)
    )


def _h5ad_shape(path: Path) -> dict[str, Any]:
    """Read n_obs/n_vars from an h5ad without materializing the matrix."""
    try:
        import h5py
    except ImportError:  # pragma: no cover - h5py ships with scanpy
        return {"shape_read": False, "reason": "h5py not available"}

    try:
        with h5py.File(path, "r") as handle:
            shape: dict[str, Any] = {"shape_read": True}
            for axis, group in (("n_obs", "obs"), ("n_vars", "var")):
                node = handle.get(group)
                if node is None:
                    continue
                index_key = node.attrs.get("_index", "_index")
                if isinstance(index_key, bytes):
                    index_key = index_key.decode()
                index = node.get(index_key)
                if index is not None:
                    shape[axis] = int(index.shape[0])
            return shape
    except Exception as exc:  # noqa: BLE001 - an unreadable file is a finding, not a crash
        return {"shape_read": False, "reason": f"{type(exc).__name__}: {exc}"}


def _classify_file(path: Path) -> Detection | None:
    name = path.name.lower()

    if name.endswith(".h5ad"):
        evidence = _h5ad_shape(path)
        kind = _matrix_kind_from_name(path)
        n_obs = evidence.get("n_obs")
        if kind == "unknown" and isinstance(n_obs, int) and n_obs >= RAW_BARCODE_THRESHOLD:
            kind = "raw"
            evidence["raw_inferred_from"] = f"n_obs={n_obs} exceeds {RAW_BARCODE_THRESHOLD}"
        return Detection(str(path), "matrix", "h5ad", kind, evidence)

    if name.endswith(".h5"):
        return Detection(str(path), "matrix", "tenx_h5", _matrix_kind_from_name(path))

    if FASTQ_RE.search(name):
        return Detection(str(path), "fastq", "fastq", evidence={"files": 1})

    return None


def _classify_dir(directory: Path) -> list[Detection]:
    """Walk one directory level deep, the way a Cell Ranger `outs/` is laid out."""
    found: list[Detection] = []

    fastqs = sorted(p for p in directory.iterdir() if p.is_file() and FASTQ_RE.search(p.name))
    if fastqs:
        found.append(
            Detection(
                str(directory),
                "fastq",
                "fastq",
                evidence={"files": len(fastqs), "names": [p.name for p in fastqs[:8]]},
            )
        )

    if _is_mtx_dir(directory):
        found.append(Detection(str(directory), "matrix", "mtx_dir", _matrix_kind_from_name(directory)))

    for child in sorted(directory.iterdir()):
        if child.is_dir() and _is_mtx_dir(child):
            found.append(Detection(str(child), "matrix", "mtx_dir", _matrix_kind_from_name(child)))
        elif child.is_file() and child.name.lower() not in IGNORED_H5_NAMES and (
            child.name.lower().endswith(".h5ad") or child.name.lower().endswith(".h5")
        ):
            detection = _classify_file(child)
            if detection is not None:
                found.append(detection)

    h5_names = {
        Path(item.path).stem.lower()
        for item in found
        if item.artifact_kind == "tenx_h5"
    }
    return [
        item
        for item in found
        if not (item.artifact_kind == "mtx_dir" and Path(item.path).name.lower() in h5_names)
    ]


def _fastq_layout(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Map each sample to its lanes and reads, and flag reads with no mate.

    `fastq_preflight` and `cellranger_count` both need the lane list, so it is
    resolved once here rather than re-globbed downstream.
    """
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(p for p in path.iterdir() if p.is_file() and FASTQ_RE.search(p.name))
        elif FASTQ_RE.search(path.name):
            files.append(path)

    grouped: dict[str, dict[str, Any]] = {}
    unparsed: list[str] = []
    for file in files:
        match = ILLUMINA_RE.match(file.name)
        if match is None:
            unparsed.append(file.name)
            continue
        entry = grouped.setdefault(
            match.group("sample"), {"lanes": set(), "reads": set(), "n_files": 0}
        )
        entry["n_files"] += 1
        entry["reads"].add(match.group("read").upper())
        if match.group("lane"):
            entry["lanes"].add(match.group("lane"))

    warnings: list[str] = []
    if unparsed:
        warnings.append(
            f"{len(unparsed)} FASTQ file(s) do not follow the Illumina naming convention: "
            f"{', '.join(sorted(unparsed)[:3])}"
        )
    for sample, entry in sorted(grouped.items()):
        reads = entry["reads"]
        if "R1" in reads and "R2" not in reads:
            warnings.append(f"sample {sample!r} has R1 but no R2")
        elif "R2" in reads and "R1" not in reads:
            warnings.append(f"sample {sample!r} has R2 but no R1")
        if len(entry["lanes"]) > 1 and entry["n_files"] % len(entry["lanes"]) != 0:
            warnings.append(f"sample {sample!r} has an uneven file count across lanes")

    layout = {
        sample: {
            "lanes": sorted(entry["lanes"]),
            "reads": sorted(entry["reads"]),
            "n_files": entry["n_files"],
        }
        for sample, entry in sorted(grouped.items())
    }
    return layout, warnings


def _bundle_paths(payload: dict[str, Any]) -> list[str]:
    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return [str(bundle)]
    raw = bundle.get("paths") or bundle.get("path") or []
    return [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Classify the bundle and recommend the next tool."""
    config = payload.get("config") or {}
    warnings: list[str] = []
    errors: list[str] = []

    requested = _bundle_paths(payload)
    if not requested:
        return _result(
            errors=["input_bundle has no 'path' or 'paths'; nothing to classify"],
            warnings=warnings,
        )

    paths: list[Path] = []
    for entry in requested:
        path = Path(entry).expanduser()
        if not path.exists():
            errors.append(f"input path does not exist: {path}")
        else:
            paths.append(path)
    if errors:
        return _result(errors=errors, warnings=warnings)

    detections: list[Detection] = []
    for path in paths:
        if path.is_dir():
            found = _classify_dir(path)
            if not found:
                errors.append(f"unsupported bundle layout, nothing recognized in: {path}")
            detections.extend(found)
        else:
            detection = _classify_file(path)
            if detection is None:
                errors.append(f"unsupported file type: {path}")
            else:
                detections.append(detection)

    if errors:
        return _result(errors=errors, warnings=warnings, detections=detections)
    if not detections:
        return _result(errors=["no FASTQ, matrix, or h5ad artifact found"], warnings=warnings)

    types = {d.input_type for d in detections}
    if len(types) > 1:
        return _result(
            errors=[
                "mixed assay types in one bundle: "
                + ", ".join(f"{d.artifact_kind} at {d.path}" for d in detections)
                + " — split the bundle or state the intended entry point"
            ],
            warnings=warnings,
            detections=detections,
        )

    input_type = types.pop()

    if input_type == "fastq":
        layout, layout_warnings = _fastq_layout(paths)
        warnings.extend(layout_warnings)
        samples = sorted(layout)
        if not samples and not layout_warnings:
            warnings.append("no sample could be parsed from the FASTQ names")
        return _result(
            input_type="fastq",
            artifact_kind="fastq",
            needs_upstream_preprocessing=True,
            needs_cell_calling=True,  # cell calling happens inside Cell Ranger
            sample_ids=samples,
            fastq_layout=layout,
            detections=detections,
            warnings=warnings,
            errors=errors,
            next_tool=_next_tool("fastq", config, payload),
            metrics={
                "n_samples": len(samples),
                "n_fastq_files": sum(entry["n_files"] for entry in layout.values()),
                "n_lanes": len({lane for entry in layout.values() for lane in entry["lanes"]}),
                "n_artifacts": len(detections),
            },
        )

    matrix_kind, kind_warnings = _reconcile_matrix_kind(detections)
    warnings.extend(kind_warnings)
    chosen = _preferred_matrix(detections, matrix_kind)

    # Every matrix of the settled kind travels on, not just the preferred one.
    # Emitting a single `matrix_path` here used to mean N inputs became one
    # library with nothing said about it: downstream steps fall back to the
    # singular key and name it `sample1`, so a two-sample run silently reported
    # on one. The plural mapping is what makes `merge_samples` see them all.
    matching = [d for d in detections if d.matrix_kind == matrix_kind] or detections
    matrix_paths = matrix_io.name_samples([d.path for d in matching])
    samples = sorted(matrix_paths)

    return _result(
        input_type="matrix",
        artifact_kind=chosen.artifact_kind,
        matrix_kind_hint=matrix_kind,
        needs_upstream_preprocessing=False,
        needs_cell_calling={"raw": True, "filtered": False}.get(matrix_kind),
        matrix_path=chosen.path,
        matrix_paths=matrix_paths,
        sample_ids=samples,
        detections=detections,
        warnings=warnings,
        errors=errors,
        next_tool=_next_tool("matrix", config, payload),
        metrics={
            "n_artifacts": len(detections),
            "n_samples": len(matrix_paths),
            **chosen.evidence,
        },
    )


def _reconcile_matrix_kind(detections: list[Detection]) -> tuple[str, list[str]]:
    """Cell Ranger emits raw and filtered side by side; that is normal, not ambiguous."""
    kinds = {d.matrix_kind for d in detections if d.matrix_kind}
    if kinds == {"raw", "filtered"}:
        return "filtered", [
            "bundle contains both raw and filtered matrices; routing on filtered, "
            "raw is still available for cell_calling_review"
        ]
    if len(kinds) == 1:
        kind = kinds.pop()
        if kind == "unknown":
            return "unknown", [
                "raw vs filtered could not be determined from the layout; "
                "count_matrix_classify must decide before the mainline"
            ]
        return kind, []
    return "unknown", ["conflicting raw/filtered signals in the bundle"]


def _preferred_matrix(detections: list[Detection], matrix_kind: str) -> Detection:
    matching = [d for d in detections if d.matrix_kind == matrix_kind]
    return (matching or detections)[0]


def _next_tool(input_type: str, config: dict[str, Any], payload: dict[str, Any]) -> str:
    """Sample-level triage is an optional pre-route; see docs/tool_registry.md."""
    if config.get("sample_qc_triage") and (payload.get("sample_metadata") or config.get("qc_metrics_csv")):
        return "sample_qc_triage"
    return "fastq_preflight" if input_type == "fastq" else "count_matrix_classify"


def _result(
    *,
    input_type: str = "unknown",
    artifact_kind: str | None = None,
    matrix_kind_hint: str | None = None,
    needs_upstream_preprocessing: bool | None = None,
    needs_cell_calling: bool | None = None,
    matrix_path: str | None = None,
    matrix_paths: dict[str, str] | None = None,
    sample_ids: list[str] | None = None,
    fastq_layout: dict[str, Any] | None = None,
    detections: list[Detection] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "input_type": input_type,
        "artifact_kind": artifact_kind,
        "matrix_kind_hint": matrix_kind_hint,
        "needs_upstream_preprocessing": needs_upstream_preprocessing,
        "needs_cell_calling": needs_cell_calling,
        "matrix_path": matrix_path,
        "matrix_paths": matrix_paths or ({"sample1": matrix_path} if matrix_path else {}),
        "sample_ids": sample_ids or [],
        "fastq_layout": fastq_layout or {},
        "detected": [asdict(d) for d in detections or []],
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="bundle directories or files to classify")
    parser.add_argument("--sample-qc-triage", action="store_true")
    args = parser.parse_args(argv)

    result = run(
        {
            "input_bundle": {"paths": args.paths},
            "config": {"sample_qc_triage": args.sample_qc_triage},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
