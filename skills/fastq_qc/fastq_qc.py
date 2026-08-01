"""Sequencing quality assessment: FastQC over every read, MultiQC to aggregate.

`fastq_preflight` reads one record per file and answers "can this run through
Cell Ranger?". This reads every record and answers "is the sequencing any good?".
It runs second because a bundle with a missing R2 should be rejected in
milliseconds, not after twenty minutes of FastQC.

The part that needs care is 10x read roles. R1 is 28bp of barcode + UMI and I1
is an 8bp index, so FastQC will fail both on per-base sequence content,
duplication and overrepresented sequences — none of which are quality problems.
Reporting those as failures would make every 10x run look broken. Quality is
therefore judged on R2, the actual cDNA read, and the structural failures on
R1/I1 are recorded as expected rather than counted against the run.

Run standalone:
    python skills/fastq_qc/fastq_qc.py --fastqs <dir> --run-dir runs/manual
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

TOOL_NAME = "fastq_qc"
INPUT_FIELDS = (
    "input_bundle",
    "run_dir",
    "config.fastqc_threads",
    "artifacts.ingest_validate",
)
OUTPUT_FIELDS = (
    "reports",
    "multiqc_report",
    "per_read_role",
    "module_failures",
    "metrics",
    "warnings",
    "errors",
    "recommended_next_tool",
)

FASTQ_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.IGNORECASE)
READ_ROLE_RE = re.compile(r"_(?P<read>[RI][123])_\d{3}\.(fastq|fq)(\.gz)?$", re.IGNORECASE)

DEFAULT_THREADS = 8
DEFAULT_MIN_Q30 = 0.75
"""Below this fraction of R2 reads at Q30+, the run is worth a person's attention."""

#: Modules that FastQC fails on 10x barcode/index reads by construction. A 28bp
#: barcode has skewed base composition and repeats by design; that is what a
#: barcode IS, not a defect. Only downgraded for R1/I1 — never for R2.
STRUCTURAL_MODULES = frozenset(
    {
        "Per base sequence content",
        "Sequence Duplication Levels",
        "Overrepresented sequences",
        "Sequence Length Distribution",
        "Per sequence GC content",
    }
)

#: Read roles that carry biology. Everything else is a barcode or an index.
BIOLOGICAL_READS = frozenset({"R2"})


# --------------------------------------------------------------------------
# Parsing FastQC output
# --------------------------------------------------------------------------


def _read_fastqc_data(zip_path: Path) -> str | None:
    """Pull `fastqc_data.txt` out of a FastQC zip, rather than scraping the HTML."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            name = next(
                (n for n in archive.namelist() if n.endswith("fastqc_data.txt")), None
            )
            if name is None:
                return None
            return archive.read(name).decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile):
        return None


def _split_modules(data: str) -> dict[str, tuple[str, list[str]]]:
    """`>>Module\tstatus` ... `>>END_MODULE` into {module: (status, lines)}."""
    modules: dict[str, tuple[str, list[str]]] = {}
    name: str | None = None
    status = ""
    body: list[str] = []
    for line in data.splitlines():
        if line.startswith(">>END_MODULE"):
            if name is not None:
                modules[name] = (status, body)
            name, body = None, []
        elif line.startswith(">>"):
            head = line[2:].split("\t")
            name = head[0]
            status = head[1].lower() if len(head) > 1 else ""
            body = []
        elif name is not None:
            body.append(line)
    return modules


def _basic_statistics(body: list[str]) -> dict[str, str]:
    stats = {}
    for line in body:
        if line.startswith("#") or "\t" not in line:
            continue
        key, _, value = line.partition("\t")
        stats[key.strip()] = value.strip()
    return stats


def _q30_fraction(body: list[str]) -> float | None:
    """Fraction of reads whose MEAN quality is >= 30.

    FastQC does not report Q30 directly; this is computed from the
    "Per sequence quality scores" histogram, which is quality -> read count.
    """
    total = 0.0
    at_least_30 = 0.0
    for line in body:
        if line.startswith("#") or "\t" not in line:
            continue
        quality, _, count = line.partition("\t")
        try:
            q = float(quality)
            n = float(count)
        except ValueError:
            continue
        total += n
        if q >= 30:
            at_least_30 += n
    return round(at_least_30 / total, 4) if total else None


def _duplicate_fraction(body: list[str]) -> float | None:
    for line in body:
        if line.startswith("#Total Deduplicated Percentage"):
            try:
                return round(1.0 - float(line.split("\t")[1]) / 100.0, 4)
            except (IndexError, ValueError):
                return None
    return None


def _max_adapter_pct(body: list[str]) -> float | None:
    """Highest adapter percentage seen at any position, for any adapter type."""
    highest = None
    for line in body:
        if line.startswith("#") or "\t" not in line:
            continue
        for cell in line.split("\t")[1:]:
            try:
                value = float(cell)
            except ValueError:
                continue
            highest = value if highest is None else max(highest, value)
    return round(highest, 4) if highest is not None else None


def parse_fastqc_zip(zip_path: Path) -> dict[str, Any] | None:
    """One FastQC report, reduced to the numbers a judge can act on."""
    data = _read_fastqc_data(zip_path)
    if data is None:
        return None
    modules = _split_modules(data)
    basic = _basic_statistics(modules.get("Basic Statistics", ("", []))[1])

    # The read role comes from FastQC's own record of the input file: it strips
    # `.fastq.gz` when naming the zip (`..._R2_001_fastqc.zip`), so the archive
    # name alone cannot tell R2 from anything else.
    read_role = _read_role(basic.get("Filename", zip_path.name))

    statuses = {name: status for name, (status, _) in modules.items()}
    failed = sorted(name for name, status in statuses.items() if status == "fail")
    warned = sorted(name for name, status in statuses.items() if status == "warn")

    # A barcode read failing on composition is a barcode, not a defect.
    structural = []
    if read_role not in BIOLOGICAL_READS:
        structural = [name for name in failed + warned if name in STRUCTURAL_MODULES]
        failed = [name for name in failed if name not in STRUCTURAL_MODULES]
        warned = [name for name in warned if name not in STRUCTURAL_MODULES]

    return {
        "file": basic.get("Filename", zip_path.stem),
        "read_role": read_role,
        "total_sequences": int(basic.get("Total Sequences", 0) or 0),
        "sequence_length": basic.get("Sequence length"),
        "pct_gc": basic.get("%GC"),
        "q30_fraction": _q30_fraction(modules.get("Per sequence quality scores", ("", []))[1]),
        "duplicate_fraction": _duplicate_fraction(
            modules.get("Sequence Duplication Levels", ("", []))[1]
        ),
        "max_adapter_pct": _max_adapter_pct(modules.get("Adapter Content", ("", []))[1]),
        "modules_failed": failed,
        "modules_warned": warned,
        "modules_expected_for_read_role": sorted(structural),
        "module_status": statuses,
    }


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


def _fastq_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(p for p in sorted(path.iterdir()) if p.is_file() and FASTQ_RE.search(p.name))
        elif FASTQ_RE.search(path.name):
            files.append(path)
    return files


def _read_role(name: str) -> str | None:
    match = READ_ROLE_RE.search(name)
    return match.group("read").upper() if match else None


def _bundle_paths(payload: dict[str, Any]) -> list[str]:
    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return [str(bundle)]
    raw = bundle.get("paths") or bundle.get("path") or []
    return [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []

    if config.get("skip_fastq_qc"):
        return _result(
            warnings=["fastq_qc skipped by config; sequencing quality was not assessed"],
            next_tool="cellranger_count",
        )

    fastqc = config.get("fastqc_binary") or shutil.which("fastqc")
    if not fastqc:
        # Advisory, not blocking: Cell Ranger's web_summary still reports Q30 and
        # mapping rate, so a missing optional tool must not stop a viable count.
        return _result(
            warnings=[
                "fastqc is not installed, so sequencing quality was not assessed "
                "(conda install -c bioconda fastqc multiqc)"
            ],
            next_tool="cellranger_count",
        )

    paths = [Path(p).expanduser() for p in _bundle_paths(payload)]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return _result(errors=[f"input path does not exist: {', '.join(missing)}"])

    files = _fastq_files(paths)
    if not files:
        return _result(errors=[f"no FASTQ found under: {', '.join(str(p) for p in paths)}"])

    out_dir = Path(payload.get("run_dir") or ".") / TOOL_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "fastqc.log"

    command = [
        str(fastqc),
        "--outdir", str(out_dir),
        "--threads", str(config.get("fastqc_threads", DEFAULT_THREADS)),
        "--quiet",
        *[str(f) for f in files],
    ]
    with log_path.open("w", encoding="utf-8") as log:
        log.write(" ".join(command) + "\n\n")
        log.flush()
        completed = subprocess.run(
            command, stdout=log, stderr=subprocess.STDOUT, check=False
        )
    if completed.returncode != 0:
        return _result(errors=[f"fastqc exited {completed.returncode}; log: {log_path}"])

    reports = []
    for zip_path in sorted(out_dir.glob("*_fastqc.zip")):
        parsed = parse_fastqc_zip(zip_path)
        if parsed is not None:
            reports.append(parsed)
    if not reports:
        return _result(errors=[f"fastqc produced no parseable output in {out_dir}"])

    multiqc_report, multiqc_warning = _run_multiqc(config, out_dir, log_path)
    if multiqc_warning:
        warnings.append(multiqc_warning)

    return _summarize(reports, out_dir, multiqc_report, config, warnings)


def _run_multiqc(
    config: dict[str, Any], out_dir: Path, log_path: Path
) -> tuple[str | None, str | None]:
    """Aggregate the per-file reports. Optional: FastQC already did the work."""
    multiqc = config.get("multiqc_binary") or shutil.which("multiqc")
    if not multiqc:
        return None, "multiqc is not installed; per-file FastQC reports are still available"

    command = [str(multiqc), str(out_dir), "--outdir", str(out_dir), "--force", "--quiet"]
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n" + " ".join(command) + "\n\n")
        log.flush()
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    if completed.returncode != 0:
        return None, f"multiqc exited {completed.returncode}; per-file reports are still available"

    report = out_dir / "multiqc_report.html"
    return (str(report) if report.is_file() else None), None


def _summarize(
    reports: list[dict[str, Any]],
    out_dir: Path,
    multiqc_report: str | None,
    config: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    by_role: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        by_role.setdefault(report["read_role"] or "unknown", []).append(report)

    def mean_of(role: str, key: str) -> float | None:
        values = [r[key] for r in by_role.get(role, []) if r.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else None

    q30_r2 = mean_of("R2", "q30_fraction")
    duplicate_r2 = mean_of("R2", "duplicate_fraction")
    adapter_max = max(
        (r["max_adapter_pct"] for r in reports if r.get("max_adapter_pct") is not None),
        default=None,
    )

    # Genuine failures only: structural ones on R1/I1 were already separated out.
    module_failures = {
        r["file"]: r["modules_failed"] for r in reports if r["modules_failed"]
    }
    for file, failed in module_failures.items():
        warnings.append(f"{file}: FastQC failed {', '.join(failed)}")

    min_q30 = float(config.get("min_q30", DEFAULT_MIN_Q30))
    if q30_r2 is not None and q30_r2 < min_q30:
        warnings.append(
            f"only {q30_r2:.1%} of cDNA (R2) reads reach Q30, below the {min_q30:.0%} "
            "threshold; low base quality will reduce mapping rate"
        )
    if not by_role.get("R2"):
        warnings.append(
            "no R2 (cDNA) read was found, so sequencing quality was judged on "
            "barcode/index reads only"
        )
    if adapter_max is not None and adapter_max >= 10.0:
        warnings.append(f"adapter content reaches {adapter_max:.1f}% at its worst position")

    expected = sorted(
        {
            module
            for r in reports
            for module in r["modules_expected_for_read_role"]
        }
    )

    return _result(
        reports=reports,
        multiqc_report=multiqc_report,
        report_dir=str(out_dir),
        per_read_role={
            role: {
                "n_files": len(items),
                "q30_fraction": mean_of(role, "q30_fraction"),
                "duplicate_fraction": mean_of(role, "duplicate_fraction"),
                "total_sequences": sum(r["total_sequences"] for r in items),
            }
            for role, items in sorted(by_role.items())
        },
        module_failures=module_failures,
        expected_module_flags=expected,
        warnings=warnings,
        next_tool="cellranger_count",
        metrics={
            "n_files": len(reports),
            "total_sequences": sum(r["total_sequences"] for r in reports),
            "q30_r2": q30_r2,
            "pct_duplicate_r2": duplicate_r2,
            "max_adapter_pct": adapter_max,
            "n_module_failures": sum(len(v) for v in module_failures.values()),
        },
    )


def _result(
    *,
    reports: list[dict[str, Any]] | None = None,
    multiqc_report: str | None = None,
    report_dir: str | None = None,
    per_read_role: dict[str, Any] | None = None,
    module_failures: dict[str, Any] | None = None,
    expected_module_flags: list[str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "reports": reports or [],
        "multiqc_report": multiqc_report,
        "report_dir": report_dir,
        "per_read_role": per_read_role or {},
        "module_failures": module_failures or {},
        # Recorded so a reader can see WHY a 10x run shows FastQC flags without
        # them counting as findings.
        "expected_module_flags": expected_module_flags or [],
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fastqs", nargs="+", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--min-q30", type=float, default=DEFAULT_MIN_Q30)
    args = parser.parse_args(argv)

    result = run(
        {
            "input_bundle": {"paths": args.fastqs},
            "run_dir": args.run_dir,
            "config": {"fastqc_threads": args.threads, "min_q30": args.min_q30},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
