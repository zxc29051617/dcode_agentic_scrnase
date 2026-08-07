"""Validate a FASTQ bundle before it is handed to Cell Ranger.

This goes deeper than `ingest_validate`'s classification: it opens the first
read of each file to check actual read lengths against known 10x chemistries,
cross-checks an optional sample sheet, and verifies the reference path looks
like a real Cell Ranger transcriptome. Anything that would make
`cellranger_count` fail outright is reported as a blocking error, not a guess.

Run standalone:  python skills/fastq_preflight/fastq_preflight.py <fastq_dir> [--reference PATH]
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TOOL_NAME = "fastq_preflight"
INPUT_FIELDS = (
    "fastq_bundle",
    "samplesheet",
    "reference",
    "config",
)
OUTPUT_FIELDS = (
    "ready_to_count",
    "detected_libraries",
    "read_structure",
    "warnings",
    "blocking_errors",
    "recommended_next_tool",
)

FASTQ_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.IGNORECASE)
ILLUMINA_RE = re.compile(
    r"^(?P<sample>.+?)_S\d+(?:_L(?P<lane>\d{3}))?_(?P<read>[RI][123])_\d{3}\.(fastq|fq)(\.gz)?$",
    re.IGNORECASE,
)

#: The minimum R1 length each chemistry needs: 16bp barcode plus its UMI.
#:
#: A LOWER BOUND, not an identification. Read length is a sequencing parameter,
#: not a property of the kit — 10x's own pbmc_1k_v2 is a v2 library sequenced
#: with 28 cycles on R1, two more than v2 needs. Guessing v3 from that length,
#: as this module used to, is confidently wrong.
MIN_R1_LENGTH: dict[str, int] = {
    "SC3Pv2": 26,   # 16 + 10
    "SC5P": 26,     # 16 + 10
    "SC3Pv3": 28,   # 16 + 12
    "SC3Pv4": 28,   # 16 + 12, GEM-X
}

#: What actually identifies the chemistry: which barcode whitelist the reads
#: belong to. The lists are disjoint enough to be decisive — on 10x's own data,
#: 94% of pbmc_1k_v2's barcodes are in the v2 list and 8% in the v3 one.
#:
#: 3' v2 and 5' share `737K-august-2016`, so a hit there narrows to two kits and
#: no further. Telling them apart needs to know where the cDNA starts, which
#: means alignment — not something a preflight check can or should do.
WHITELIST_CHEMISTRY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("3M-february-2018_TRU.txt.gz", ("SC3Pv3",)),
    ("3M-3pgex-may-2023_TRU.txt.gz", ("SC3Pv4",)),
    ("3M-5pgex-jan-2023.txt.gz", ("SC5P-PE", "SC5P-R2")),
    ("737K-august-2016.txt", ("SC3Pv2", "SC5P-PE", "SC5P-R2")),
)

#: Fraction of sampled barcodes that must be in a whitelist to call it. Real
#: data lands near 95%; sequencing error puts the rest just off the list.
WHITELIST_HIT_THRESHOLD = 0.5

#: Barcodes to sample. Enough to be decisive, few enough to read in a moment.
BARCODE_SAMPLE_SIZE = 20_000
MIN_CDNA_READ_LENGTH = 50
"""Below this, R2 is too short to be a usable cDNA read for GEX."""

#: Files a Cell Ranger transcriptome reference must contain to be usable.
REFERENCE_MARKERS = ("reference.json",)


@dataclass
class Library:
    """One sample's FASTQ evidence."""

    sample: str
    lanes: list[str]
    n_files: int
    reads: dict[str, dict[str, Any]] = field(default_factory=dict)
    chemistry_guess: list[str] = field(default_factory=list)
    chemistry_evidence: dict[str, Any] = field(default_factory=dict)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _whitelist_dir() -> Path | None:
    """Cell Ranger ships the barcode whitelists; find them next to the binary."""
    import glob as _glob

    for pattern in (
        "~/projects/cellranger-*/lib/python/cellranger/barcodes",
        "~/cellranger-*/lib/python/cellranger/barcodes",
        "/opt/cellranger-*/lib/python/cellranger/barcodes",
    ):
        matches = sorted(_glob.glob(str(Path(pattern).expanduser())))
        if matches:
            return Path(matches[-1])
    return None


def _sample_barcodes(files: list[Path], length: int = 16) -> list[str]:
    """The first `BARCODE_SAMPLE_SIZE` barcodes, taken from the head of R1."""
    barcodes: list[str] = []
    for path in files:
        opener = gzip.open if path.name.endswith(".gz") else open
        try:
            with opener(path, "rt", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index % 4 == 1:
                        barcodes.append(line.strip()[:length])
                        if len(barcodes) >= BARCODE_SAMPLE_SIZE:
                            return barcodes
        except OSError:
            continue
    return barcodes


#: Identifying a chemistry means streaming ~4.5M whitelist lines, so the answer
#: is remembered per bundle. Keyed on path and mtime, so an edited file is
#: re-read rather than served stale.
_chemistry_cache: dict[tuple, tuple[list[str], dict[str, Any]]] = {}


def identify_chemistry(r1_files: list[Path]) -> tuple[list[str], dict[str, Any]]:
    """Which kit these reads came from, by whitelist membership.

    Streams each whitelist against the sampled barcodes rather than loading it
    into a set — the v3 list has 3.7M entries and only the 20k sample needs to
    be held.

    Returns (candidate kits, evidence). An empty list means undecided, which is
    an honest answer and better than the read-length guess this replaced.
    """
    try:
        key = tuple(sorted((str(f), f.stat().st_mtime_ns) for f in r1_files))
    except OSError:
        key = tuple(sorted(str(f) for f in r1_files))
    if key in _chemistry_cache:
        return _chemistry_cache[key]

    directory = _whitelist_dir()
    if directory is None:
        return [], {"reason": "Cell Ranger's barcode whitelists were not found"}

    sample = _sample_barcodes(r1_files)
    if not sample:
        return [], {"reason": "no barcodes could be read from R1"}
    wanted = set(sample)

    hits: dict[str, float] = {}
    for filename, kits in WHITELIST_CHEMISTRY:
        path = directory / filename
        if not path.is_file():
            continue
        opener = gzip.open if filename.endswith(".gz") else open
        found = 0
        try:
            with opener(path, "rt", errors="replace") as handle:
                for line in handle:
                    if line.split("\t", 1)[0].strip() in wanted:
                        found += 1
        except OSError:
            continue
        hits[filename] = found / len(wanted)

    if not hits:
        return [], {"reason": "no whitelist could be read"}

    best = max(hits, key=hits.get)
    evidence = {
        "n_barcodes_sampled": len(sample),
        "whitelist_hit_rate": {name: round(rate, 3) for name, rate in sorted(hits.items())},
        "matched_whitelist": best,
    }
    if hits[best] < WHITELIST_HIT_THRESHOLD:
        evidence["reason"] = (
            f"no whitelist matched more than {hits[best]:.0%} of the barcodes"
        )
        answer: tuple[list[str], dict[str, Any]] = ([], evidence)
    else:
        answer = (list(dict(WHITELIST_CHEMISTRY)[best]), evidence)
    _chemistry_cache[key] = answer
    return answer


def _peek_read_length(path: Path) -> int | None:
    """Read length of the first record, without loading the whole file."""
    opener = gzip.open if path.name.endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as handle:
            handle.readline()  # @header
            sequence = handle.readline().strip()
            return len(sequence) if sequence else None
    except OSError:
        return None


def _bundle_paths(payload: dict[str, Any]) -> list[str]:
    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return [str(bundle)]
    raw = bundle.get("paths") or bundle.get("path") or []
    return [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]


def _collect_fastq_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(p for p in path.iterdir() if p.is_file() and FASTQ_RE.search(p.name))
        elif FASTQ_RE.search(path.name):
            files.append(path)
    return files


def _group_by_sample(files: list[Path]) -> tuple[dict[str, dict[str, list[Path]]], list[str]]:
    """sample -> read role -> files (one per lane), plus names that didn't parse."""
    grouped: dict[str, dict[str, list[Path]]] = {}
    unparsed: list[str] = []
    for file in files:
        match = ILLUMINA_RE.match(file.name)
        if match is None:
            unparsed.append(file.name)
            continue
        sample_reads = grouped.setdefault(match.group("sample"), {})
        sample_reads.setdefault(match.group("read").upper(), []).append(file)
    return grouped, unparsed


def _build_library(sample: str, reads_to_files: dict[str, list[Path]]) -> Library:
    lanes = sorted(
        {
            m.group("lane")
            for files in reads_to_files.values()
            for f in files
            if (m := ILLUMINA_RE.match(f.name)) and m.group("lane")
        }
    )
    n_files = sum(len(files) for files in reads_to_files.values())
    library = Library(sample=sample, lanes=lanes, n_files=n_files)

    if "R1" not in reads_to_files:
        library.blocking.append(f"sample {sample!r} has no R1 (barcode+UMI) read")
    if "R2" not in reads_to_files:
        library.blocking.append(f"sample {sample!r} has no R2 (cDNA) read")

    for role, files in sorted(reads_to_files.items()):
        lengths = {f.name: _peek_read_length(f) for f in files}
        unreadable = [name for name, length in lengths.items() if length is None]
        if unreadable:
            library.warnings.append(f"{sample!r} {role}: could not read {', '.join(unreadable)}")
        observed = sorted({length for length in lengths.values() if length is not None})
        library.reads[role] = {
            "n_files": len(files),
            "lengths_observed": observed,
            "sampled_from": next(iter(lengths)),
        }
        if len(observed) > 1:
            library.warnings.append(
                f"{sample!r} {role} read length varies across files: {observed}"
            )

    # Chemistry comes from the barcode whitelist, not the read length.
    r1_files = reads_to_files.get("R1", [])
    if r1_files:
        kits, evidence = identify_chemistry(r1_files)
        library.chemistry_guess = kits
        library.chemistry_evidence = evidence
        if not kits:
            library.warnings.append(
                f"{sample!r} chemistry could not be identified from the barcode "
                f"whitelists ({evidence.get('reason', 'unknown')}); Cell Ranger's "
                f"own auto-detection will still run"
            )

    # Read length is only a validity check now: too short for any kit is a real
    # problem, but a longer read says nothing about which kit produced it.
    r1_lengths = library.reads.get("R1", {}).get("lengths_observed") or []
    if r1_lengths:
        shortest_kit = min(MIN_R1_LENGTH.values())
        if r1_lengths[0] < shortest_kit:
            library.blocking.append(
                f"{sample!r} R1 is {r1_lengths[0]}bp, too short for any 10x chemistry "
                f"(the shortest needs {shortest_kit}bp of barcode + UMI)"
            )
        elif library.chemistry_guess:
            needed = min(
                MIN_R1_LENGTH[k] for k in library.chemistry_guess if k in MIN_R1_LENGTH
            ) if any(k in MIN_R1_LENGTH for k in library.chemistry_guess) else 0
            if needed and r1_lengths[0] < needed:
                library.warnings.append(
                    f"{sample!r} R1 is {r1_lengths[0]}bp but "
                    f"{'/'.join(library.chemistry_guess)} needs {needed}bp"
                )

    r2_lengths = library.reads.get("R2", {}).get("lengths_observed") or []
    if r2_lengths and min(r2_lengths) < MIN_CDNA_READ_LENGTH:
        library.warnings.append(
            f"{sample!r} R2 length {min(r2_lengths)} is short for a cDNA read "
            f"(expected >= {MIN_CDNA_READ_LENGTH}bp)"
        )

    return library


def _check_samplesheet(
    libraries: list[Library], samplesheet: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Requested-but-missing samples are blocking; extra ones are only a warning."""
    detected = {lib.sample for lib in libraries}
    requested = {entry["sample"] for entry in samplesheet if entry.get("sample")}

    blocking = [
        f"samplesheet requests {sample!r} but no matching FASTQ was found"
        for sample in sorted(requested - detected)
    ]
    warnings = [
        f"FASTQ for {sample!r} was found but is not listed in the samplesheet"
        for sample in sorted(detected - requested)
    ]

    for entry in samplesheet:
        sample = entry.get("sample")
        chemistry = entry.get("chemistry")
        if not sample or not chemistry:
            continue
        library = next((lib for lib in libraries if lib.sample == sample), None)
        if library and library.chemistry_guess and chemistry not in library.chemistry_guess:
            library.warnings.append(
                f"samplesheet declares chemistry {chemistry!r} but reads look like "
                f"{library.chemistry_guess}"
            )

    return blocking, warnings


def _check_reference(reference: str | None) -> tuple[list[str], list[str]]:
    if not reference:
        return ["no reference provided; cellranger_count requires --reference"], []

    path = Path(reference).expanduser()
    if not path.exists():
        return [f"reference path does not exist: {path}"], []
    if not path.is_dir():
        return [f"reference path is not a directory: {path}"], []

    missing = [marker for marker in REFERENCE_MARKERS if not (path / marker).exists()]
    if missing:
        return [
            f"reference at {path} is missing {', '.join(missing)}; "
            "does not look like a Cell Ranger transcriptome"
        ], []
    return [], []


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    blocking: list[str] = []

    requested = _bundle_paths(payload)
    if not requested:
        return _result(errors=["input_bundle has no 'path' or 'paths'; nothing to validate"])

    paths: list[Path] = []
    for entry in requested:
        path = Path(entry).expanduser()
        if not path.exists():
            blocking.append(f"input path does not exist: {path}")
        else:
            paths.append(path)
    if blocking:
        return _result(errors=blocking)

    files = _collect_fastq_files(paths)
    if not files:
        return _result(errors=[f"no FASTQ files found under: {', '.join(str(p) for p in paths)}"])

    grouped, unparsed = _group_by_sample(files)
    if unparsed:
        warnings.append(
            f"{len(unparsed)} file(s) do not follow the Illumina naming convention: "
            f"{', '.join(sorted(unparsed)[:3])}"
        )
    if not grouped:
        return _result(errors=["no sample could be parsed from the FASTQ names"], warnings=warnings)

    # Libraries `sample_qc_triage` excluded never reach a count. This is the
    # only place the FASTQ route decides what to work on, so an exclusion that
    # was not honoured here would have been recorded and then ignored.
    excluded = set(
        ((payload.get("artifacts") or {}).get("sample_qc_triage") or {}).get("excluded_samples")
        or []
    )
    if excluded:
        kept = {name: reads for name, reads in grouped.items() if name not in excluded}
        dropped = sorted(set(grouped) - set(kept))
        if dropped:
            warnings.append(
                f"{len(dropped)} librar(y/ies) excluded by sample_qc_triage and not "
                f"counted: {', '.join(dropped)}"
            )
        if not kept:
            return _result(
                errors=["sample_qc_triage excluded every library; nothing left to count"],
                warnings=warnings,
            )
        grouped = kept

    libraries = [_build_library(sample, reads) for sample, reads in sorted(grouped.items())]

    samplesheet = config.get("samplesheet")
    if samplesheet:
        # Mutates library.warnings with chemistry-mismatch findings, so this must
        # run before per-library warnings are collected below.
        sheet_blocking, sheet_warnings = _check_samplesheet(libraries, samplesheet)
        blocking.extend(sheet_blocking)
        warnings.extend(sheet_warnings)

    for library in libraries:
        blocking.extend(library.blocking)
        warnings.extend(library.warnings)

    # `resolve_reference` owns *which* reference and whether it matches the
    # species; this step only asks whether that one can run a count. Its
    # resolved path wins over config, which is the fallback for a standalone run.
    resolved = (payload.get("artifacts") or {}).get("resolve_reference") or {}
    reference = resolved.get("transcriptome") or config.get("reference")
    ref_blocking, ref_warnings = _check_reference(reference)
    blocking.extend(ref_blocking)
    warnings.extend(ref_warnings)

    for field_name in ("localcores", "localmem", "expected_cells"):
        value = config.get(field_name)
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            warnings.append(f"config.{field_name}={value!r} is not a positive number")

    ready = not blocking
    return _result(
        ready_to_count=ready,
        detected_libraries=[asdict(lib) for lib in libraries],
        read_structure={lib.sample: lib.reads for lib in libraries},
        warnings=warnings,
        errors=blocking,  # mirrors blocking_errors so the graph's judge/gate sees it
        next_tool="cellranger_count" if ready else "human_review",
        metrics={
            "n_samples": len(libraries),
            "n_files": len(files),
            "n_ready": sum(1 for lib in libraries if not lib.blocking),
        },
    )


def _result(
    *,
    ready_to_count: bool = False,
    detected_libraries: list[dict[str, Any]] | None = None,
    read_structure: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blocking_errors = errors or []
    return {
        "ready_to_count": ready_to_count,
        "detected_libraries": detected_libraries or [],
        "read_structure": read_structure or {},
        "blocking_errors": blocking_errors,
        "warnings": warnings or [],
        "errors": blocking_errors,  # graph machinery reads this key; see call_skill
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="FASTQ directories or files to validate")
    parser.add_argument("--reference", help="Cell Ranger transcriptome path")
    parser.add_argument("--chemistry", help="expected chemistry, checked against a samplesheet")
    args = parser.parse_args(argv)

    result = run(
        {
            "input_bundle": {"paths": args.paths},
            "config": {"reference": args.reference},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ready_to_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
