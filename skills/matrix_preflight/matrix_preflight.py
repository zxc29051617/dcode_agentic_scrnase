"""The matrix route's counterpart to `fastq_preflight`: is this usable at all?

A FASTQ run learns its species from the reference it has to resolve anyway. A
run that arrives holding a count matrix never touches a reference, so until this
step existed nothing on that side asked the equivalent questions. It asks four:

  format       can the file be read, and as what
  gene ids     which naming convention, and are stable ids present at all
  species      what organism the matrix actually contains
  orientation  cells x genes, or transposed

Species is answered from whatever evidence the file carries, strongest first:

  1. a recorded genome — only a 10x `.h5` has one
  2. Ensembl stable ids — `ENSG` against `ENSMUSG`
  3. symbol casing — `CD3E` against `Cd3e`

Third is a convention rather than a guarantee, so it is reported as weaker.
It still earns its place: the T2T RefSeq annotation names genes `LOC124900618`,
which defeats the first two and is exactly the case an mtx directory presents.

Run standalone:
    python skills/matrix_preflight/matrix_preflight.py <matrix> --species human
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import matrix_io  # noqa: E402
from src import species as species_table  # noqa: E402

TOOL_NAME = "matrix_preflight"
INPUT_FIELDS = ("input_bundle", "artifacts.ingest_validate", "config.species")
OUTPUT_FIELDS = (
    "readable",
    "matrix_format",
    "gene_id_convention",
    "feature_types",
    "species",
    "species_evidence",
    "orientation",
    "mito_prefix",
    "erythroid_genes",
    "warnings",
    "errors",
    "recommended_next_tool",
)

#: A 10x barcode: 16 nucleotides, optionally suffixed with a GEM-well number.
BARCODE_RE = re.compile(r"^[ACGT]{14,20}(-\d+)?$", re.IGNORECASE)

#: How many names to look at. Conventions are uniform within a matrix, so a
#: sample settles it and the whole index never has to be materialised.
SAMPLE_SIZE = 2_000


def _gene_id_convention(gene_ids: list[str]) -> str:
    if not gene_ids:
        return "symbols only"
    sample = [str(g) for g in gene_ids[:SAMPLE_SIZE]]
    if sum(g.upper().startswith("ENS") for g in sample) > len(sample) * 0.5:
        return "ensembl"
    if sum(g.upper().startswith("LOC") or g[:1].isdigit() for g in sample) > len(sample) * 0.5:
        return "refseq/entrez"
    return "symbols as ids"


def _looks_like_barcodes(names: list[str]) -> float:
    sample = [str(n) for n in names[:SAMPLE_SIZE]]
    if not sample:
        return 0.0
    return sum(bool(BARCODE_RE.match(n)) for n in sample) / len(sample)


def _identify_species(adata: Any) -> tuple[set[str], str]:
    """Strongest available evidence for what organism this is."""
    genomes = matrix_io.recorded_genomes(adata)
    if genomes:
        found = species_table.identify_reference({"genomes": sorted(genomes)})
        if found:
            return found, f"recorded genome ({', '.join(sorted(genomes))})"

    if "gene_ids" in adata.var:
        found = species_table.identify_from_gene_ids(adata.var["gene_ids"][:SAMPLE_SIZE])
        if found:
            return found, "Ensembl gene ids"

    found = species_table.identify_from_symbols(list(adata.var_names[:SAMPLE_SIZE]))
    if found:
        return found, "gene symbol casing (a convention, not a guarantee)"
    return set(), "none available"


def _resolve_matrices(payload: dict[str, Any]) -> dict[str, str]:
    """Every matrix on the way in, not just the first.

    This step decides the species for the whole run, so it has to look at every
    library: checking one and carrying that verdict for the rest is how a
    mouse sample smuggled into a human run would go unnoticed. It also has to
    pass them all on, because whatever it emits is what
    `count_matrix_classify` reads — it is consulted before `ingest_validate`.
    """
    artifacts = payload.get("artifacts") or {}
    ingest = artifacts.get("ingest_validate") or {}
    paths = ingest.get("matrix_paths")
    if paths:
        return {str(k): str(v) for k, v in paths.items()}
    single = ingest.get("matrix_path")
    if single:
        return {matrix_io.sample_name_for(single): str(single)}

    bundle = payload.get("input_bundle") or {}
    if isinstance(bundle, (str, Path)):
        return {matrix_io.sample_name_for(bundle): str(bundle)}
    raw = bundle.get("paths") or bundle.get("path") or []
    listed = [str(raw)] if isinstance(raw, (str, Path)) else [str(p) for p in raw]
    return matrix_io.name_samples(listed) if listed else {}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    constants = species_table.constants_for(config)
    warnings: list[str] = list(constants["warnings"])
    notes: list[str] = list(constants["notes"])

    matrices = _resolve_matrices(payload)
    if not matrices:
        return _result(errors=["no matrix to inspect"], constants=constants)

    # Every library is checked, but the structural report describes the first.
    # A second library that disagrees on species is caught by the loop at the
    # end; one that is unreadable is an error naming which one.
    ordered = sorted(matrices.items())
    per_matrix: dict[str, Any] = {}
    for name, candidate in ordered:
        as_path = Path(candidate).expanduser()
        if not as_path.exists():
            return _result(errors=[f"{name}: matrix path does not exist: {as_path}"],
                           constants=constants)

    first_name, source = ordered[0]
    path = Path(source).expanduser()

    try:
        adata, provenance = matrix_io.load_matrix(path)
    except Exception as exc:  # noqa: BLE001 - an unreadable matrix is the finding
        return _result(
            errors=[f"cannot read {path} as a count matrix: {type(exc).__name__}: {exc}"],
            constants=constants,
        )

    # --- orientation --------------------------------------------------------
    obs_barcode_like = _looks_like_barcodes(list(adata.obs_names))
    var_barcode_like = _looks_like_barcodes(list(adata.var_names))
    orientation = "cells x genes"
    if var_barcode_like > 0.8 and obs_barcode_like < 0.2:
        orientation = "genes x cells"
        return _result(
            errors=[
                f"the matrix looks transposed: {var_barcode_like:.0%} of its variable "
                f"names are 10x barcodes. Downstream expects cells as rows — "
                f"transpose it before continuing"
            ],
            matrix_format=provenance["source_format"],
            orientation=orientation,
            constants=constants,
        )

    # --- non-gene-expression features ---------------------------------------
    # A warning, not a note: unlike missing gene ids there IS something to
    # decide here. Either the antibody/CRISPR data was not wanted, or this is
    # the wrong pipeline for it.
    dropped = provenance.get("feature_types_dropped") or {}
    if dropped:
        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(dropped.items()))
        warnings.append(
            f"the file also holds {summary} features, which were dropped — this "
            f"pipeline analyses gene expression only. Use a multimodal tool if "
            f"those were wanted"
        )

    # --- gene ids -----------------------------------------------------------
    gene_ids = list(adata.var["gene_ids"]) if "gene_ids" in adata.var else []
    convention = _gene_id_convention(gene_ids)
    if not gene_ids:
        # A note, not a warning: nobody can add stable ids to a matrix that
        # shipped without them, so there is no decision here to stop for. It
        # still belongs in the record — annotation will be weaker for it.
        notes.append(
            "the matrix carries gene symbols but no stable ids; symbols are not "
            "unique or stable across annotation versions, so downstream mapping "
            "has nothing reliable to key on"
        )

    # --- species ------------------------------------------------------------
    seen, evidence = _identify_species(adata)
    declared = constants["species"]
    verified = False
    if not seen:
        notes.append(
            "the matrix carries no usable species evidence — no genome, no Ensembl "
            "ids, and symbol casing was inconclusive; verification skipped"
        )
    elif len(seen) > 1:
        notes.append(
            f"the matrix matches multiple species ({', '.join(sorted(seen))}) — "
            "barnyard/PDX; verification skipped"
        )
    else:
        found = seen.pop()
        if declared is None:
            warnings.append(
                f"no species declared; this matrix looks like {found} ({evidence}). "
                "Set config.species so the two can be cross-checked"
            )
        elif found != declared:
            return _result(
                errors=[
                    f"species mismatch: the run declares {declared!r} but the matrix "
                    f"looks like {found!r} from its {evidence}. Every number "
                    f"downstream would be filed under the wrong organism"
                ],
                matrix_format=provenance["source_format"],
                constants=constants,
            )
        else:
            verified = True

    # --- the other libraries ------------------------------------------------
    # Only the species is re-checked, and only where a matrix carries evidence:
    # it is the one property that must hold across every library, and the one
    # whose failure would file a whole organism's worth of numbers wrongly.
    per_matrix[first_name] = {"path": str(path), "species": sorted(seen) or None}
    for name, other in ordered[1:]:
        try:
            other_adata, _ = matrix_io.load_matrix(Path(other).expanduser())
        except Exception as exc:  # noqa: BLE001 - an unreadable library is a finding
            return _result(
                errors=[f"{name}: cannot read {other} as a count matrix: "
                        f"{type(exc).__name__}: {exc}"],
                constants=constants,
            )
        other_seen, other_evidence = _identify_species(other_adata)
        per_matrix[name] = {"path": str(other), "species": sorted(other_seen) or None}
        if len(other_seen) == 1 and declared is not None and other_seen != {declared}:
            return _result(
                errors=[
                    f"species mismatch: the run declares {declared!r} but {name} looks "
                    f"like {other_seen.pop()!r} from its {other_evidence}. Merging "
                    f"libraries from different organisms would file every number "
                    f"downstream under the wrong one"
                ],
                constants=constants,
            )

    if len(ordered) > 1:
        notes.append(
            f"{len(ordered)} libraries were detected ({', '.join(n for n, _ in ordered)}); "
            f"the structural report above describes {first_name}, and the species of "
            "every library was checked"
        )

    return _result(
        readable=True,
        matrix_format=provenance["source_format"],
        matrix_path=str(path),
        matrix_paths={name: value["path"] for name, value in per_matrix.items()},
        per_matrix=per_matrix,
        gene_id_convention=convention,
        feature_types=provenance.get("feature_types_on_disk") or {},
        species_verified=verified,
        species_evidence=evidence,
        orientation=orientation,
        constants=constants,
        warnings=warnings,
        notes=notes,
        next_tool="count_matrix_classify",
        metrics={
            "n_barcodes": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "gene_id_convention": convention,
            "species_verified": verified,
            "has_gene_ids": bool(gene_ids),
        },
    )


def _result(
    *,
    readable: bool = False,
    matrix_format: str | None = None,
    matrix_path: str | None = None,
    matrix_paths: dict[str, str] | None = None,
    per_matrix: dict[str, Any] | None = None,
    gene_id_convention: str | None = None,
    feature_types: dict[str, int] | None = None,
    species_verified: bool = False,
    species_evidence: str | None = None,
    orientation: str | None = None,
    constants: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    errors: list[str] | None = None,
    next_tool: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    constants = constants or {}
    return {
        "readable": readable,
        "matrix_format": matrix_format,
        "matrix_path": matrix_path,
        "matrix_paths": matrix_paths or ({"sample1": matrix_path} if matrix_path else {}),
        "per_matrix": per_matrix or {},
        "gene_id_convention": gene_id_convention,
        "feature_types": feature_types or {},
        "orientation": orientation,
        "species": constants.get("species"),
        "declared_species": constants.get("declared_species"),
        "species_verified": species_verified,
        "species_evidence": species_evidence,
        # The same constants the FASTQ route gets from `resolve_reference`, so
        # the mainline reads one shape whichever way the run came in.
        "mito_prefix": constants.get("mito_prefix"),
        "erythroid_genes": constants.get("erythroid_genes") or [],
        "marker_db": constants.get("marker_db"),
        "constants_source": constants.get("constants_source") or {},
        "recommended_next_tool": next_tool,
        "metrics": metrics or {},
        "notes": notes or [],
        "warnings": warnings or [],
        "errors": errors or [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("matrix")
    parser.add_argument("--species")
    args = parser.parse_args(argv)
    result = run(
        {
            "artifacts": {"ingest_validate": {"matrix_path": args.matrix}},
            "config": {"species": args.species},
        }
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
