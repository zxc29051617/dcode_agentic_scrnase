"""Which files inside a run may be served, and how one is found again.

The rule that makes this safe is not a path check bolted onto a download
handler — it is that **there is no way to name a file**. A client can only ask
for an `artifact_id`, and an `artifact_id` only exists because
`list_artifacts()` produced it by walking a fixed set of glob patterns inside
one resolved run directory. `resolve_artifact()` answers a request by building
that same list again and looking the id up in it. A path the manifest would
not list is a path the content endpoint cannot reach, because the lookup never
consults the request for anything but an opaque token.

Three consequences worth stating, since each is a mistake that reads
plausibly:

- `../` in a request is not "sanitised". It is meaningless: the id is a hash,
  and a hash that no manifest entry produced matches nothing.
- A symlink inside the run pointing outside it is caught when the manifest is
  built, not when the file is read — `_safe_files()` resolves every candidate
  and drops any that no longer lives under the run directory. So a symlink
  planted after a manifest was fetched still fails on the next lookup.
- The same 404 answers an unknown id, a rejected symlink and a file that has
  since been deleted. Distinguishing them would let a caller map the
  filesystem by asking.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Refuse rather than stream something unbounded. MultiQC reports are the
#: large case — tens of megabytes is normal for one with many samples — and
#: this is set well above that while still being a limit. A file over it is
#: reported as too large, not silently truncated, because half an HTML report
#: renders as a broken page with no explanation.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactRule:
    """One kind of file a run may publish, and where it is written."""

    kind: str
    #: Glob relative to the run directory. `*` does not cross a directory
    #: separator, so every pattern here names an exact depth — a rule cannot
    #: quietly widen into a subtree.
    pattern: str
    media_type: str
    label: str


#: The whitelist. A file matching none of these cannot be listed and therefore
#: cannot be fetched. Each path comes from the skill that writes it:
#: `fastq_qc` runs FastQC and MultiQC with `--outdir <run>/fastq_qc`;
#: `cellranger_count` puts each library's `outs` under
#: `<run>/cellranger_count/<library_id>/outs`; `build_report` writes its
#: rendered report and figures under `<run>/build_report`.
ARTIFACT_RULES: tuple[ArtifactRule, ...] = (
    ArtifactRule("fastqc_html", "fastq_qc/*_fastqc.html", "text/html", "FastQC report"),
    ArtifactRule("multiqc_html", "fastq_qc/multiqc_report.html", "text/html", "MultiQC report"),
    ArtifactRule(
        "cellranger_web_summary",
        "cellranger_count/*/outs/web_summary.html",
        "text/html",
        "Cell Ranger web summary",
    ),
    ArtifactRule("report_html", "build_report/report.html", "text/html", "Run report (HTML)"),
    ArtifactRule("report_pdf", "build_report/*.pdf", "application/pdf", "Run report (PDF)"),
    ArtifactRule("figure", "build_report/figures/*.png", "image/png", "Report figure"),
    ArtifactRule("figure", "build_report/figures/*.svg", "image/svg+xml", "Report figure"),
)

#: Kinds whose bytes are third-party HTML. The content endpoint sends these
#: with a sandboxing Content-Security-Policy as well, so a report opened
#: directly — not only one inside the app's iframe — is still isolated.
HTML_KINDS = frozenset({"fastqc_html", "multiqc_html", "cellranger_web_summary", "report_html"})


def artifact_id(relative_path: str) -> str:
    """A stable opaque id for a path inside a run.

    A hash rather than the path itself: an encoded path is still a path, and
    a client that can construct one can ask for anything the encoding allows.
    A hash can only be *recognised*, which means only a path the manifest
    already listed can be requested.
    """
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def _safe_files(run_dir: Path, pattern: str) -> list[Path]:
    """Files matching `pattern` that genuinely live inside `run_dir`.

    `glob` will happily return a symlink; `resolve()` is what says where it
    actually points, and `relative_to` is what rejects it if that is outside
    the run. Done here, when the manifest is built, so the same check runs on
    every lookup rather than once at download time.
    """
    root = run_dir.resolve()
    found: list[Path] = []
    for candidate in sorted(root.glob(pattern)):
        try:
            real = candidate.resolve()
            real.relative_to(root)
        except (ValueError, OSError):
            continue
        if real.is_file():
            found.append(real)
    return found


def list_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    """Every servable artifact in this run, as the manifest presents it."""
    root = run_dir.resolve()
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in ARTIFACT_RULES:
        for path in _safe_files(root, rule.pattern):
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            entries.append({
                "artifact_id": artifact_id(relative),
                "kind": rule.kind,
                "label": rule.label,
                "name": path.name,
                # Relative to the run directory, never an absolute host path.
                # The same class of value `get_report` already returns as
                # `source_path`: it describes the layout inside a run, which a
                # reader comparing this against a directory needs.
                "relative_path": relative,
                "media_type": rule.media_type,
                "size_bytes": size,
                "too_large": size > MAX_ARTIFACT_BYTES,
                "is_html": rule.kind in HTML_KINDS,
            })
    return entries


@dataclass(frozen=True)
class ResolvedArtifact:
    path: Path
    entry: dict[str, Any]


def resolve_artifact(run_dir: Path, requested_id: str) -> ResolvedArtifact | None:
    """The file behind an id, or None if the manifest does not produce that id.

    Rebuilds the manifest rather than trusting anything in the request. The
    request contributes one thing — a token to compare against — and never a
    path fragment.
    """
    root = run_dir.resolve()
    for entry in list_artifacts(root):
        if entry["artifact_id"] == requested_id:
            path = (root / entry["relative_path"]).resolve()
            # Re-checked at read time as well: the manifest was built a moment
            # ago, and a symlink could have been swapped in between.
            try:
                path.relative_to(root)
            except ValueError:
                return None
            if not path.is_file():
                return None
            return ResolvedArtifact(path=path, entry=entry)
    return None
