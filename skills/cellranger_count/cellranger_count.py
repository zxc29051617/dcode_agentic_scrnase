"""Run `cellranger count` per library, against a reference that has been proven.

Idempotent, but only for the SAME reference. Counting costs ~20 minutes a
library, so an existing `filtered_feature_bc_matrix.h5` is reused — but only
after the genome recorded *inside that matrix* is checked against the reference
this run was told to use.

That check is the point of this module. Without it, changing the reference and
re-running into the same run directory skips every library and finishes in
seconds with the old counts, while the audit log and the report both name the
new reference. Log, report and matrix then agree with each other and are all
wrong together — the one error shape nothing downstream can notice.

Run standalone:
    python skills/cellranger_count/cellranger_count.py \\
        --fastqs <dir> --sample <prefix> --transcriptome <ref> --run-dir <out>
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

TOOL_NAME = "cellranger_count"
INPUT_FIELDS = (
    "input_bundle",
    "run_dir",
    "config.cellranger",
    "artifacts.resolve_reference",
    "artifacts.fastq_preflight",
)
OUTPUT_FIELDS = (
    "libraries",
    "count_manifest",
    "raw_feature_bc_matrix",
    "filtered_feature_bc_matrix",
    "metrics",
    "warnings",
    "errors",
    "recommended_next_tool",
)

DEFAULTS: dict[str, Any] = {
    "binary": "cellranger",
    "localcores": 16,
    "localmem": 64,
    "create_bam": True,
    "include_introns": True,
}

#: Where a tarball install usually lands. Cell Ranger is not installed with a
#: package manager and rarely ends up on PATH, so looking for it beats making
#: everyone remember the version number in the path.
BINARY_SEARCH_GLOBS = (
    "~/projects/cellranger-*/bin/cellranger",
    "~/cellranger-*/bin/cellranger",
    "/opt/cellranger-*/bin/cellranger",
    "/usr/local/cellranger-*/bin/cellranger",
)


def find_binary(configured: str | None = None) -> str | None:
    """Locate the cellranger executable, or None if it is nowhere obvious.

    An explicit setting is honoured even when it is wrong — reporting "the path
    you gave does not exist" is more useful than silently running a different
    install than the one asked for.
    """
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        return shutil.which(configured)

    found = shutil.which(DEFAULTS["binary"])
    if found:
        return found

    for pattern in BINARY_SEARCH_GLOBS:
        # Sorted, so a later version wins when several are installed.
        matches = sorted(glob.glob(str(Path(pattern).expanduser())))
        executables = [m for m in matches if Path(m).is_file()]
        if executables:
            return executables[-1]
    return None


# --------------------------------------------------------------------------
# The reference guard
# --------------------------------------------------------------------------


def _reference_genomes(transcriptome: Path) -> set[str]:
    """The genome name(s) mkref stamped into this reference.

    `reference.json`'s `genomes` is exactly what was passed as `--genome=` to
    mkref, and exactly what Cell Ranger writes into every matrix counted against
    it. That shared string is what makes the two sides comparable at all.
    """
    meta = transcriptome / "reference.json"
    if not meta.is_file():
        raise ValueError(
            f"{meta} is missing, so an existing matrix has nothing to be compared "
            f"against. Point the reference at a real Cell Ranger reference "
            f"(mkref always writes reference.json)."
        )
    return set(json.loads(meta.read_text(encoding="utf-8")).get("genomes") or [])


def _matrix_genomes(h5: Path) -> set[str]:
    """The genome name(s) Cell Ranger wrote INTO an existing count matrix.

    This is the half that makes the reuse check possible without re-running
    anything: the matrix carries the identity of the reference it was counted
    against, so a stale matrix is caught by reading one dataset out of the file
    we were about to trust — microseconds, against ~20 minutes of counting.
    """
    import h5py
    import numpy as np

    with h5py.File(h5, "r") as handle:
        if "matrix/features/genome" in handle:        # CR3+ (h5 format v2)
            raw = np.unique(handle["matrix/features/genome"][:]).tolist()
        else:                                         # CR2 (v1): one group per genome
            raw = [key for key in handle.keys() if "barcodes" in handle[key]]
    if not raw:
        raise ValueError("no genome recorded in the matrix")
    return {v.decode() if isinstance(v, bytes) else str(v) for v in raw}


def assert_same_reference(filtered: Path, transcriptome: Path, library_id: str) -> set[str]:
    """Refuse to reuse a matrix counted against a DIFFERENT reference.

    Not being able to verify is treated as a mismatch. A stop is recoverable in
    one command; a wrong reuse is not detectable at all.

    Raises `ValueError` with a message meant to be read by a person.
    """
    want = _reference_genomes(transcriptome)
    try:
        got = _matrix_genomes(filtered)
    except Exception as exc:  # noqa: BLE001 - unverifiable is a finding, not a crash
        raise ValueError(
            f"[{library_id}] an existing matrix is here but its reference cannot be "
            f"read ({type(exc).__name__}: {exc}): {filtered}. Refusing to reuse it. "
            f"Delete {filtered.parent.parent} to recount."
        ) from exc

    if got != want:
        raise ValueError(
            f"[{library_id}] REFUSING to reuse the existing count matrix — it was "
            f"counted against a different reference. "
            f"existing matrix: {', '.join(sorted(got))}; "
            f"this run wants: {', '.join(sorted(want))} ({transcriptome}); "
            f"at: {filtered}. Reusing it would file the OLD reference's counts under "
            f"the NEW reference's name, in the audit log AND in the report. "
            f"Fix: use a fresh run directory, or delete {filtered.parent.parent}."
        )
    return got


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def _chemistry_for(config: dict[str, Any]) -> str:
    """Explicit config wins; otherwise let Cell Ranger auto-detect.

    `fastq_preflight`'s read-length guess is deliberately NOT used to set
    `--chemistry`: when that guess is ambiguous (26bp covers three kits) forcing
    one would be worse than Cell Ranger's own detection, which sees the reads.
    """
    declared = config.get("chemistry")
    if declared and str(declared).lower() != "auto":
        return str(declared)
    return "auto"


def _libraries_from_artifacts(artifacts: dict[str, Any], config: dict[str, Any]) -> list[dict]:
    """Which samples to count, preferring what `fastq_preflight` already resolved."""
    chemistry = _chemistry_for(config)
    detected = (artifacts.get("fastq_preflight") or {}).get("detected_libraries") or []
    if detected:
        return [
            {"library_id": lib["sample"], "sample_prefix": lib["sample"], "chemistry": chemistry}
            for lib in detected
        ]

    layout = (artifacts.get("ingest_validate") or {}).get("fastq_layout") or {}
    return [
        {"library_id": name, "sample_prefix": name, "chemistry": chemistry}
        for name in sorted(layout)
    ]


def _fastq_dirs(payload: dict[str, Any]) -> list[str]:
    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return [str(bundle)]
    raw = bundle.get("paths") or bundle.get("path") or []
    return [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]


def _build_command(
    binary: str,
    library: dict[str, Any],
    fastqs: str,
    transcriptome: Path,
    config: dict[str, Any],
) -> list[str]:
    settings = {**DEFAULTS, **{k: v for k, v in config.items() if k in DEFAULTS}}
    command = [
        binary,
        "count",
        f"--id={library['library_id']}",
        f"--transcriptome={transcriptome}",
        f"--fastqs={fastqs}",
        f"--sample={library['sample_prefix']}",
        f"--create-bam={'true' if settings['create_bam'] else 'false'}",
        f"--include-introns={'true' if settings['include_introns'] else 'false'}",
        f"--localcores={settings['localcores']}",
        f"--localmem={settings['localmem']}",
    ]
    if library.get("chemistry", "auto") != "auto":
        command.append(f"--chemistry={library['chemistry']}")
    if config.get("expected_cells"):
        command.append(f"--expect-cells={config['expected_cells']}")
    return command


def _read_metrics_summary(outs: Path) -> dict[str, Any]:
    """Cell Ranger's own top-line numbers — what the judge actually scores."""
    path = outs / "metrics_summary.csv"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return dict(rows[0]) if rows else {}
    except (OSError, csv.Error):
        return {}


def _library_record(library: dict[str, Any], outs: Path, **extra: Any) -> dict[str, Any]:
    return {
        "library_id": library["library_id"],
        "chemistry": library.get("chemistry", "auto"),
        "outs": str(outs),
        "raw_feature_bc_matrix": str(outs / "raw_feature_bc_matrix.h5"),
        "filtered_feature_bc_matrix": str(outs / "filtered_feature_bc_matrix.h5"),
        "bam": str(outs / "possorted_genome_bam.bam"),
        "web_summary": str(outs / "web_summary.html"),
        "metrics_summary": _read_metrics_summary(outs),
        **extra,
    }


def _count_one(
    library: dict[str, Any],
    *,
    fastqs: str,
    transcriptome: Path,
    work: Path,
    config: dict[str, Any],
    binary: str,
) -> dict[str, Any]:
    """Run (or skip) `cellranger count` for one library."""
    library_id = library["library_id"]
    outs = work / library_id / "outs"
    filtered = outs / "filtered_feature_bc_matrix.h5"

    if filtered.exists():
        genomes = assert_same_reference(filtered, transcriptome, library_id)
        return _library_record(
            library, outs, disposition=f"reused: {', '.join(sorted(genomes))}"
        )

    stale = work / library_id
    if stale.exists():
        raise ValueError(
            f"[{library_id}] {stale} exists but holds no filtered matrix "
            f"(partial or aborted run). Remove it and re-run."
        )

    command = _build_command(binary, library, fastqs, transcriptome, config)
    work.mkdir(parents=True, exist_ok=True)
    # Cell Ranger's own _log lives inside its run directory, but that directory
    # does not exist yet and is removed on some failures; keep ours outside it.
    log_path = work / "logs" / f"{library_id}.cellranger.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        log.write(" ".join(command) + "\n\n")
        log.flush()
        completed = subprocess.run(
            command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=False
        )
    elapsed = time.monotonic() - started

    if completed.returncode != 0:
        raise ValueError(
            f"[{library_id}] cellranger exited {completed.returncode} after "
            f"{elapsed / 60:.1f} min. Log: {log_path}"
        )
    if not filtered.exists():
        raise ValueError(
            f"[{library_id}] cellranger finished cleanly but produced no {filtered}. "
            f"Log: {log_path}"
        )
    return _library_record(
        library,
        outs,
        disposition="counted",
        elapsed_min=round(elapsed / 60, 1),
        command=command,
        log=str(log_path),
    )


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    artifacts = payload.get("artifacts") or {}
    warnings: list[str] = []

    # --- the reference, already resolved and species-checked upstream -------
    resolved = artifacts.get("resolve_reference") or {}
    reference = resolved.get("transcriptome") or config.get("transcriptome")
    if not reference:
        return _result(errors=["no transcriptome; resolve_reference must run first"])
    transcriptome = Path(reference).expanduser()
    if not (transcriptome / "reference.json").is_file():
        return _result(
            errors=[f"not a Cell Ranger reference (no reference.json): {transcriptome}"]
        )
    if resolved and not resolved.get("species_verified"):
        warnings.append(
            "the reference was not verified against the declared species; counts "
            "will be filed under a species nobody confirmed"
        )

    # --- refuse to start if preflight said the bundle is not ready -----------
    preflight = artifacts.get("fastq_preflight") or {}
    if preflight.get("ready_to_count") is False:
        return _result(
            errors=[
                "fastq_preflight reported the bundle is not ready to count: "
                + "; ".join(preflight.get("blocking_errors") or ["(no reason given)"])
            ]
        )

    configured = config.get("binary")
    binary = find_binary(configured)
    if binary is None:
        looked = configured or f"PATH, {', '.join(BINARY_SEARCH_GLOBS)}"
        return _result(
            errors=[
                f"cellranger executable not found (looked in: {looked}). "
                f"Set config.binary to the path of the `cellranger` file inside "
                f"an unpacked release, e.g. ~/projects/cellranger-10.1.0/bin/cellranger"
            ]
        )

    dirs = _fastq_dirs(payload)
    if not dirs:
        return _result(errors=["input_bundle has no FASTQ path"])

    libraries = _libraries_from_artifacts(artifacts, config)
    if not libraries:
        return _result(errors=["no library to count; fastq_preflight found no sample"])

    fastqs = ",".join(dirs)
    work = Path(payload.get("run_dir") or ".") / TOOL_NAME

    records: list[dict[str, Any]] = []
    for library in libraries:
        try:
            records.append(
                _count_one(
                    library,
                    fastqs=fastqs,
                    transcriptome=transcriptome,
                    work=work,
                    config=config,
                    binary=binary,
                )
            )
        except ValueError as exc:
            # One library's refusal stops the step: a partial count set feeding
            # the mainline silently is worse than stopping here.
            return _result(errors=[str(exc)], libraries=records, warnings=warnings)

    manifest = work / "count_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({r["library_id"]: r["outs"] for r in records}, indent=2), encoding="utf-8"
    )

    n_reused = sum(1 for r in records if str(r.get("disposition", "")).startswith("reused"))
    if n_reused:
        warnings.append(
            f"{n_reused} of {len(records)} librar"
            f"{'y' if n_reused == 1 else 'ies'} reused an existing matrix "
            "(verified against this reference, not recounted)"
        )

    first = records[0]
    return _result(
        libraries=records,
        count_manifest=str(manifest),
        raw_matrix=first["raw_feature_bc_matrix"],
        filtered_matrix=first["filtered_feature_bc_matrix"],
        warnings=warnings,
        next_tool="count_matrix_classify",
        metrics={
            "n_libraries": len(records),
            "n_counted": len(records) - n_reused,
            "n_reused": n_reused,
            "reference_genomes": sorted(_reference_genomes(transcriptome)),
            "per_library": {
                r["library_id"]: {
                    "disposition": r.get("disposition"),
                    **{
                        key: value
                        for key, value in (r.get("metrics_summary") or {}).items()
                        if key
                        in (
                            "Estimated Number of Cells",
                            "Mean Reads per Cell",
                            "Median Genes per Cell",
                            "Fraction Reads in Cells",
                            "Reads Mapped Confidently to Transcriptome",
                        )
                    },
                }
                for r in records
            },
        },
    )


def _result(
    *,
    libraries: list[dict[str, Any]] | None = None,
    count_manifest: str | None = None,
    raw_matrix: str | None = None,
    filtered_matrix: str | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "libraries": libraries or [],
        "count_manifest": count_manifest,
        "raw_feature_bc_matrix": raw_matrix,
        "filtered_feature_bc_matrix": filtered_matrix,
        # `count_matrix_classify` routes on these. Cell Ranger emits both raw and
        # filtered; filtered is the standard downstream choice, and the raw one
        # stays available for cell_calling_review.
        "matrix_path": filtered_matrix,
        "matrix_kind": "filtered" if filtered_matrix else None,
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fastqs", required=True, help="FASTQ directory")
    parser.add_argument("--sample", required=True, help="Cell Ranger sample prefix")
    parser.add_argument("--transcriptome", required=True)
    parser.add_argument("--run-dir", required=True, help="where the count output goes")
    parser.add_argument("--binary", default=DEFAULTS["binary"])
    parser.add_argument("--localcores", type=int, default=DEFAULTS["localcores"])
    parser.add_argument("--localmem", type=int, default=DEFAULTS["localmem"])
    parser.add_argument("--chemistry", default="auto")
    parser.add_argument("--expect-cells", type=int)
    parser.add_argument("--no-bam", action="store_true")
    args = parser.parse_args(argv)

    result = run(
        {
            "input_bundle": {"paths": [args.fastqs]},
            "run_dir": args.run_dir,
            "config": {
                "transcriptome": args.transcriptome,
                "binary": args.binary,
                "localcores": args.localcores,
                "localmem": args.localmem,
                "chemistry": args.chemistry,
                "expected_cells": args.expect_cells,
                "create_bam": not args.no_bam,
            },
            "artifacts": {
                "fastq_preflight": {
                    "detected_libraries": [{"sample": args.sample}],
                    "ready_to_count": True,
                }
            },
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
