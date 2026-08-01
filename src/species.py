"""Everything that changes when the species changes — as data, not behaviour.

This module is a table, the same kind of thing as `registry.py`. It performs no
I/O and reads nothing off disk, so anything may import it: the
`resolve_reference` skill, a shell script asking where a reference belongs, a
QC step asking what a mitochondrial gene is called in this organism.

WHY ONLY TWO ROWS. Human and mouse are the two whose gene lists can be written
down without guessing. A wrong symbol here is worse than a missing one: a
missing one stops the run, a wrong one filters the wrong cells and says
nothing. Every other species in `SPECIES_ALIASES` is deliberately left without a
profile — the run then asks for the values in config instead of inventing them.
Blocking a legitimate non-model organism would be worse than the gap it closes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReferenceSource:
    """Where a species' Cell Ranger reference comes from.

    `dirname` is relative to this project's `reference/` directory and is the
    name `mkref` itself wrote (or the tarball unpacks to) — not a name we chose,
    so `scripts/link_reference.sh` and `resolve_reference` agree on where it
    lands without either of them guessing.
    """

    dirname: str
    note: str
    url: str | None = None
    """Prebuilt tarball, when 10x ships one."""
    build: str | None = None
    """Script that builds it from FASTA + GTF, when 10x ships none."""
    download_gb: float | None = None
    disk_gb: float | None = None
    """Unpacked size — what actually has to be free."""


@dataclass(frozen=True)
class SpeciesProfile:
    """The species-dependent constants a run needs, in one place."""

    canonical: str
    reference: ReferenceSource
    erythroid: tuple[str, ...]
    marker_db: str | None
    """PanglaoDB column. The database only covers human and mouse, so this is
    None elsewhere and the marker cross-check degrades rather than scoring one
    species' symbols against another's."""
    mito_prefix: str = "MT-"
    """Matched case-insensitively. Human is MT-ND1, mouse is mt-Nd1 — the same
    prefix once upper-cased, which is why human-only code happens to work for
    mouse. Kept explicit so the next species that breaks the pattern
    (Drosophila is mt:ND1, with a colon) is a one-line fix, not a hunt."""
    qc_defaults_native: bool = True
    """False means this profile's QC starting points were read off another
    species' data and the run should say so rather than passing them off."""


#: 3' GEX references. This project counts GEX only — Multiome/ATAC references
#: are built by `cellranger-arc mkref` and are not interchangeable with these.
SPECIES_PROFILES: dict[str, SpeciesProfile] = {
    "human": SpeciesProfile(
        canonical="human",
        reference=ReferenceSource(
            dirname="T2T_CHM13v2_RefSeqLiftoff_v5_3",
            note="T2T-CHM13v2.0 (maskedY) + JHU RefSeq/Liftoff v5.3, built in-house "
                 "(39,048 genes, chrM merged in). This project's default.",
            build="see reference/README.md",
            disk_gb=32.0,
        ),
        erythroid=("HBA1", "HBA2", "HBB", "HBM", "HBD", "ALAS2"),
        marker_db="human",
    ),
    "mouse": SpeciesProfile(
        canonical="mouse",
        reference=ReferenceSource(
            dirname="refdata-gex-GRCm39-2024-A",
            note="10x prebuilt GRCm39 (2024-A). No build step — 10x ships it.",
            url="https://cf.10xgenomics.com/supp/cell-exp/refdata-gex-GRCm39-2024-A.tar.gz",
            download_gb=9.6,
            disk_gb=20.0,
        ),
        # MGI symbols. Mouse has no HBD/HBM equivalent and splits adult beta into
        # bs/bt. The embryonic chains (Hba-x, Hbb-y) are left out on purpose —
        # they only fire in embryonic tissue, where their presence is not what
        # "erythroid contamination" means.
        erythroid=("Hba-a1", "Hba-a2", "Hbb-bs", "Hbb-bt", "Alas2"),
        marker_db="mouse",
        qc_defaults_native=False,
    ),
}


#: Fingerprints that appear in a reference's own `reference.json` — the `genomes`
#: name mkref stamped in, and the input FASTA filenames it recorded. Assembly
#: codes are the reliable part; a directory name can be anything.
SPECIES_SIGNATURES: dict[str, tuple[str, ...]] = {
    "human": ("chm13", "grch38", "grch37", "hg38", "hg19", "homo_sapiens", "t2t"),
    "mouse": ("grcm39", "grcm38", "mm10", "mm39", "mus_musculus"),
    "rat": ("rnor", "mratbn", "rattus", "rn6", "rn7"),
    "zebrafish": ("grcz11", "grcz10", "danio", "danrer"),
    "pig": ("sscrofa", "susscrofa"),
    "macaque": ("mmul", "macaca", "rhemac"),
    "drosophila": ("bdgp6", "dm6", "drosophila", "melanogaster"),
}

#: What a user may write for each canonical species above.
SPECIES_ALIASES: dict[str, str] = {
    "human": "human", "homo sapiens": "human", "hsapiens": "human",
    "人": "human", "人類": "human", "人类": "human",
    "mouse": "mouse", "mus musculus": "mouse", "mmusculus": "mouse",
    "小鼠": "mouse", "老鼠": "mouse", "鼠": "mouse",
    "rat": "rat", "rattus norvegicus": "rat", "大鼠": "rat",
    "zebrafish": "zebrafish", "danio rerio": "zebrafish", "斑馬魚": "zebrafish",
    "pig": "pig", "sus scrofa": "pig", "豬": "pig",
    "macaque": "macaque", "rhesus": "macaque", "monkey": "macaque", "猴": "macaque",
    "drosophila": "drosophila", "fly": "drosophila", "果蠅": "drosophila",
}


def canonical(species: str | None) -> str | None:
    """Map whatever the user typed to a key of `SPECIES_SIGNATURES`, or None."""
    return SPECIES_ALIASES.get((species or "").strip().lower())


def profile(species: str | None) -> SpeciesProfile | None:
    """The profile for a species, or None when there are no vetted gene lists.

    None is a supported state, not a failure: `resolve_reference` turns it into
    a request for explicit config rather than a refusal to run.
    """
    return SPECIES_PROFILES.get(canonical(species) or "")


def known() -> list[str]:
    """Species with a full profile — the ones a species name alone can drive."""
    return sorted(SPECIES_PROFILES)


def identify_reference(reference_json: dict[str, Any]) -> set[str]:
    """Which species a reference claims to be, read from its own metadata.

    Takes the parsed `reference.json` rather than a path, so this module stays
    free of I/O — the caller owns the filesystem.

    An empty set means "not recognised" and a set of two or more means a
    barnyard/PDX reference. Both are states where the caller should stay quiet
    rather than pick: there is no wrong answer to point at.
    """
    haystack = " ".join(
        str(value).lower()
        for key in ("genomes", "input_fasta_files", "input_gtf_files", "version")
        for value in (
            reference_json.get(key)
            if isinstance(reference_json.get(key), list)
            else [reference_json.get(key)]
        )
        if value is not None
    )
    return {
        name
        for name, fingerprints in SPECIES_SIGNATURES.items()
        if any(fingerprint in haystack for fingerprint in fingerprints)
    }
