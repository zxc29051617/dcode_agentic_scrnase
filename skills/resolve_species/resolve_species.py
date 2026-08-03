"""Resolve the species constants every route needs, without touching the disk.

Split out of `resolve_reference` because the two answer different questions and
only one of them applies everywhere:

  * **species constants** — what a mitochondrial gene is called here, which
    haemoglobin symbols to look for, whether a marker database covers this
    organism. A table lookup. Needed by QC and annotation on *both* routes.
  * **the Cell Ranger reference** — a 32 GB STAR index. Needed only to turn
    FASTQ into counts.

Putting them in one node meant a count-matrix run passed through something
called "resolve_reference" for a reference it never uses. This one runs before
the route splits; `resolve_reference` now runs on the FASTQ branch only.

Nothing here reads the filesystem, so nothing here can fail on a missing file.

Run standalone:
    python skills/resolve_species/resolve_species.py --species human
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

from src import species as species_table  # noqa: E402

TOOL_NAME = "resolve_species"
INPUT_FIELDS = ("config.species", "config.mito_prefix", "config.erythroid_genes")
OUTPUT_FIELDS = (
    "species",
    "mito_prefix",
    "erythroid_genes",
    "marker_db",
    "constants_source",
    "notes",
    "warnings",
    "errors",
    "recommended_next_tool",
)


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    warnings: list[str] = []
    # `warnings` are things that might be wrong and need a decision now; `notes`
    # are true and worth knowing but are not a decision point. Mixing them means
    # every mouse run stops at step two, which teaches people to click through.
    notes: list[str] = []

    declared = config.get("species")
    canonical = species_table.canonical(declared)
    profile = species_table.profile(declared)

    if not declared:
        warnings.append(
            "no species declared, so nothing downstream can be cross-checked against "
            "the data's own genome. Set config.species"
        )
    elif canonical is None:
        warnings.append(
            f"species {declared!r} is not recognised; QC constants must come from "
            f"config. Recognised names: {', '.join(sorted(species_table.SPECIES_ALIASES))}"
        )
    elif profile is None:
        warnings.append(
            f"{canonical} is recognised but has no vetted gene lists — only "
            f"{', '.join(species_table.known())} do. Supply mito_prefix and "
            f"erythroid_genes in config, or QC will measure nothing"
        )

    # Config always wins: a curated table is a default, not an override of the
    # person who knows their own annotation.
    mito_prefix = config.get("mito_prefix") or (profile.mito_prefix if profile else None)
    erythroid = list(config.get("erythroid_genes") or (profile.erythroid if profile else []))

    sources = {
        "mito_prefix": _source(config.get("mito_prefix"), profile),
        "erythroid_genes": _source(config.get("erythroid_genes"), profile),
    }
    if profile is not None and not profile.qc_defaults_native:
        notes.append(
            f"QC starting points for {profile.canonical} were derived from another "
            "species' data; review the thresholds rather than trusting the defaults"
        )
    if not mito_prefix:
        warnings.append(
            "no mitochondrial gene prefix known, so run_qc_metrics cannot measure "
            "mitochondrial fraction — it will report zero rather than fail"
        )

    return _result(
        species=canonical,
        declared=declared,
        mito_prefix=mito_prefix,
        erythroid_genes=erythroid,
        marker_db=profile.marker_db if profile else None,
        constants_source=sources,
        notes=notes,
        warnings=warnings,
        next_tool=_next_tool(payload, config),
    )


def _source(from_config: Any, profile: Any) -> str:
    if from_config:
        return "config"
    return "species table" if profile is not None else "unavailable"


def _next_tool(payload: dict[str, Any], config: dict[str, Any]) -> str:
    ingest = (payload.get("artifacts") or {}).get("ingest_validate") or {}
    if config.get("sample_qc_triage") and (
        payload.get("sample_metadata") or config.get("qc_metrics_csv")
    ):
        return "sample_qc_triage"
    return "resolve_reference" if ingest.get("needs_upstream_preprocessing") else "count_matrix_classify"


def _result(
    *,
    species: str | None = None,
    declared: str | None = None,
    mito_prefix: str | None = None,
    erythroid_genes: list[str] | None = None,
    marker_db: str | None = None,
    constants_source: dict[str, str] | None = None,
    notes: list[str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
) -> dict[str, Any]:
    return {
        "species": species,
        "declared_species": declared,
        "mito_prefix": mito_prefix,
        "erythroid_genes": erythroid_genes or [],
        "marker_db": marker_db,
        "constants_source": constants_source or {},
        "notes": notes or [],
        "recommended_next_tool": next_tool,
        "metrics": {
            "species": species,
            "has_mito_prefix": bool(mito_prefix),
            "n_erythroid_genes": len(erythroid_genes or []),
            "has_marker_db": bool(marker_db),
        },
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--species")
    args = parser.parse_args(argv)
    result = run({"config": {"species": args.species}, "artifacts": {}})
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
