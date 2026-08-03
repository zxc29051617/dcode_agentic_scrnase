"""Resolve the Cell Ranger reference for counting, and prove it is the right one.

Runs on the **FASTQ branch only**, and is that route's entry check. A 32 GB STAR
index is what turns reads into counts; a run arriving with a count matrix never
needs one, and gets `matrix_preflight` instead.

Both entry steps also emit the species constants the mainline needs — the
mitochondrial prefix, haemoglobin symbols, the marker database — from the shared
table in `src/species.py`. Each route answers the species question with the
evidence it has: this one reads the reference's `reference.json`, the matrix
route reads the matrix.

Picking the wrong reference fails silently: the counts are wrong, but the audit
log, the report and the matrix all agree with each other and are wrong together.
So this step reads the reference's own `reference.json` and refuses to continue
when it names a different species than the run declared.

Paths stay project-local. Code and config say `reference/<dirname>`; the symlink
that `scripts/link_reference.sh` makes is the only thing that knows where the
bytes actually live.

Run standalone:  python skills/resolve_reference/resolve_reference.py --species human
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# The species table is a sibling package module, not a skill. Skills are loaded
# by path (see src/registry.load_skill), so the project root has to be on the
# path before `src` can be imported.
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import species as species_table  # noqa: E402

TOOL_NAME = "resolve_reference"
INPUT_FIELDS = (
    "config.species",
    "config.transcriptome",
    "config.reference_root",
    "artifacts.ingest_validate",
)
OUTPUT_FIELDS = (
    "species",
    "transcriptome",
    "reference_available",
    "species_verified",
    "reference_genomes",
    "mito_prefix",
    "erythroid_genes",
    "notes",
    "warnings",
    "errors",
    "recommended_next_tool",
)

DEFAULT_REFERENCE_ROOT = "reference"


def _how_to_get(source: species_table.ReferenceSource) -> str:
    """One line telling the user how to put this reference on disk."""
    if source.url:
        size = f" (~{source.download_gb:g} GB download" if source.download_gb else " ("
        size += f", ~{source.disk_gb:g} GB on disk)" if source.disk_gb else ")"
        return f"download {source.url}{size}, then: bash scripts/link_reference.sh <species> <path>"
    disk = f" (~{source.disk_gb:g} GB on disk)" if source.disk_gb else ""
    return (
        f"build it{disk} — {source.build or 'see reference/README.md'} — "
        f"then: bash scripts/link_reference.sh <species> <path>"
    )


def _read_reference_json(transcriptome: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Parse the reference's own metadata, or say why it could not be read."""
    meta_path = transcriptome / "reference.json"
    if not transcriptome.exists():
        return None, f"reference path does not exist: {transcriptome}"
    if not transcriptome.is_dir():
        return None, f"reference path is not a directory: {transcriptome}"
    if not meta_path.is_file():
        return None, (
            f"{transcriptome} has no reference.json, so it is not a Cell Ranger "
            f"reference (mkref always writes one)"
        )
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"{meta_path} could not be read: {type(exc).__name__}: {exc}"


def _verify_species(
    meta: dict[str, Any], declared: str | None
) -> tuple[bool, list[str], list[str]]:
    """Cross-check the reference's own metadata against the declared species.

    Returns (verified, errors, warnings). Not being able to tell is a warning,
    never a pass — blocking a legitimate custom or barnyard reference would be
    worse than the typo it would catch.
    """
    seen = species_table.identify_reference(meta)

    if not seen:
        return False, [], [
            "the reference does not identify its species in reference.json; "
            "species verification skipped (normal for a custom build)"
        ]
    if len(seen) > 1:
        return False, [], [
            f"the reference matches multiple species ({', '.join(sorted(seen))}) — "
            "barnyard/PDX; species verification skipped"
        ]
    found = seen.pop()
    if declared is None:
        return False, [], [
            f"no species declared; the reference looks like {found}. "
            "Set config.species so the two can be cross-checked"
        ]
    if found != declared:
        return False, [
            f"species mismatch: the run declares {declared!r} but the reference was "
            f"built for {found!r}. Counting against it would file {found} counts "
            f"under {declared}'s name in the log AND the report"
        ], []
    return True, [], []


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    artifacts = payload.get("artifacts") or {}
    warnings: list[str] = []
    errors: list[str] = []

    constants = species_table.constants_for(config)
    warnings.extend(constants["warnings"])
    notes = list(constants["notes"])
    declared = constants["species"]
    profile = species_table.profile(config.get("species"))
    raw_species = config.get("species")

    # --- resolve the path ---------------------------------------------------
    explicit = config.get("transcriptome")
    root = Path(config.get("reference_root") or DEFAULT_REFERENCE_ROOT)
    transcriptome: Path | None = None

    if explicit:
        transcriptome = Path(explicit).expanduser()
    elif profile is not None:
        transcriptome = root / profile.reference.dirname
    else:
        errors.append(
            f"no reference for species={raw_species!r}"
            + (f" (recognised as {declared})" if declared else " (unrecognised)")
            + f". Registered: {', '.join(species_table.known())}. "
            "Either use a registered species or set config.transcriptome explicitly"
        )

    # --- check it is really there -------------------------------------------
    meta: dict[str, Any] | None = None
    available = False
    if transcriptome is not None:
        meta, problem = _read_reference_json(transcriptome)
        if problem:
            hint = ""
            if profile is not None and not explicit:
                hint = f". Get it: {_how_to_get(profile.reference)}"
            errors.append(problem + hint)
        else:
            available = True

    # --- prove it is the species the run claims -----------------------------
    verified = False
    if meta is not None:
        verified, verify_errors, verify_warnings = _verify_species(meta, declared)
        errors.extend(verify_errors)
        warnings.extend(verify_warnings)

    genomes = list((meta or {}).get("genomes") or [])
    return _result(
        species=declared,
        transcriptome=str(transcriptome) if transcriptome else None,
        reference_available=available,
        species_verified=verified,
        reference_genomes=genomes,
        reference_version=(meta or {}).get("version"),
        constants=constants,
        notes=notes,
        warnings=warnings,
        errors=errors,
        next_tool="fastq_preflight",
        metrics={
            "reference_available": available,
            "species_verified": verified,
            "n_reference_genomes": len(genomes),
        },
    )


def _result(
    *,
    species: str | None = None,
    transcriptome: str | None = None,
    reference_available: bool = False,
    species_verified: bool = False,
    reference_genomes: list[str] | None = None,
    reference_version: str | None = None,
    constants: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "species": species,
        "transcriptome": transcriptome,
        "reference_available": reference_available,
        "species_verified": species_verified,
        "reference_genomes": reference_genomes or [],
        "reference_version": reference_version,
        # The same constants `matrix_preflight` emits, so the mainline reads one
        # shape whichever way the run came in.
        "mito_prefix": (constants or {}).get("mito_prefix"),
        "erythroid_genes": (constants or {}).get("erythroid_genes") or [],
        "marker_db": (constants or {}).get("marker_db"),
        "constants_source": (constants or {}).get("constants_source") or {},
        "notes": notes or [],
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--species", help="human, mouse, 小鼠, ...")
    parser.add_argument("--transcriptome", help="explicit reference path; wins over --species")
    parser.add_argument("--reference-root", default=DEFAULT_REFERENCE_ROOT)
    args = parser.parse_args(argv)

    result = run(
        {
            "config": {
                "species": args.species,
                "transcriptome": args.transcriptome,
                "reference_root": args.reference_root,
            },
            "artifacts": {},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
