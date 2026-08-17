"""Rebuild the human T2T-CHM13v2.0 + RefSeq/Liftoff v5.3 Cell Ranger reference
from traceable official sources, with provenance that lets it be rebuilt again.

The reference this project shipped with was built once, by hand, in a project
directory (`claude_agentic_scrna`) that no longer exists. Its build script and
provenance record went with it. This script exists so that never happens
again: every source is a URL plus either a published checksum or one this
script computed and recorded, every step is a named command this script ran
(not one a person typed once and forgot), and the output directory carries
its own history.

## What running nothing does

Every subcommand below except `plan` requires an explicit flag before it opens
a network connection or starts `cellranger`. Running this file with no
arguments, or with `plan`, only prints what it would do and touches no disk
and no network. That is deliberate: a builder that downloads a gigabyte of
genome sequence because someone ran it to see the `--help` text has already
done something a person did not ask for.

## The four problems this build has to survive

Recorded at length in `reference/README.md`; restated here because each one
is silent if the code does not check for it:

1. RefSeq/Liftoff v5.3 does not annotate the mitochondrial contig at all —
   confirmed independently via the NCBI assembly-comparison API, which reports
   the RefSeq assembly (GCF_009914755.1) as having "Removed chromosome MT"
   relative to its GenBank pair (GCA_009914755.4). `pct_counts_mt` reads as 0
   forever without a chrM annotation merged in from elsewhere.
2. The FASTA must be the `maskedY` variant, not `maskedY_rCRS` — same file
   size (980 MB), different MD5, and the wrong one puts chrM annotation on the
   wrong sequence without erroring.
3. GFF3 exon rows do not carry `gene_id`; it has to be propagated down the
   `ID`/`Parent` chain that GFF3 encodes and GTF does not.
4. RefSeq's biotype vocabulary (`V_segment`, `J_segment`, `C_region`) is not
   GENCODE's (`IG_V_gene`, `TR_V_gene`, ...); a `mkgtf --attribute` filter
   written for GENCODE drops every immune receptor segment.

## What this script refuses to do that the old build apparently did

It does not hardcode which file supplies the mitochondrial annotation. Two
candidate CAT/Liftoff sources exist and neither is authoritative on its own;
`compare-chrm-candidates` downloads both, detects the mitochondrial contig
from the FASTA itself (by sequence length, never by assuming a name), counts
how many of the 13 canonical mitochondrial protein-coding genes each
candidate's contig carries, and **fails closed** — prints the comparison and
exits non-zero — unless exactly one candidate is the unambiguous better
answer. `merge-chrm` will not run until that comparison has produced a winner.

Run with:

    python scripts/build_t2t_chm13_reference.py plan
    python scripts/build_t2t_chm13_reference.py fetch-fasta --i-confirm-download
    python scripts/build_t2t_chm13_reference.py fetch-primary-annotation --i-confirm-download
    python scripts/build_t2t_chm13_reference.py compare-chrm-candidates --i-confirm-download
    python scripts/build_t2t_chm13_reference.py schema-audit-chrm-candidates
    python scripts/build_t2t_chm13_reference.py select-chrm-candidate --label <label> --i-confirm-human-selection
    python scripts/build_t2t_chm13_reference.py merge-chrm
    python scripts/build_t2t_chm13_reference.py gff3-to-gtf
    python scripts/build_t2t_chm13_reference.py mkgtf
    python scripts/build_t2t_chm13_reference.py mkref --i-confirm-build --nthreads 16 --memgb 128

or the whole thing, still gated:

    python scripts/build_t2t_chm13_reference.py all --i-confirm-download --i-confirm-build
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = Path.home() / "data" / "references" / "_build_T2T_CHM13v2_v5_3"
OUTPUT_DIR = Path.home() / "data" / "references" / "T2T_CHM13v2_RefSeqLiftoff_v5_3"

#: `MB` and `GB` are used only in printed estimates, never in a comparison —
#: comparisons against a downloaded file always use bytes, so a rounding
#: difference in a printed estimate can never make a size check pass or fail.
GB = 1024**3


# --------------------------------------------------------------------------
# Sources — every one traceable to a URL, checked at download time
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    label: str
    url: str
    #: Published by the upstream provider, verified against the download.
    #: `None` when upstream publishes none — the script then computes and
    #: *records* a sha256 rather than pretending to verify one.
    expected_md5: str | None
    approx_size_bytes: int
    note: str


#: Published in `analysis_set/README.txt` at the bucket root, fetched
#: directly from AWS S3 and quoted here — not retyped from a webpage that
#: could have reformatted it.
#:
#:     bd90ddb80c86af7fcfeefe7a0909b175  chm13v2.0_maskedY.fa.gz
#:
#: `maskedY_rCRS` is the file most tutorials link and is the *wrong* one for
#: this build (see problem 2 above) — it is 980.4 MB as well, so a size check
#: alone cannot catch the substitution. Only the MD5 can, which is why it is
#: checked automatically after every download rather than left as a note for
#: a human to remember.
FASTA_SOURCE = Source(
    label="chm13v2.0_maskedY",
    url=(
        "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/"
        "assemblies/analysis_set/chm13v2.0_maskedY.fa.gz"
    ),
    expected_md5="bd90ddb80c86af7fcfeefe7a0909b175",
    approx_size_bytes=980_400_000,
    note=(
        "PARs on chrY hard-masked to N. NOT chm13v2.0_maskedY.rCRS.fa.gz, which is "
        "the same size and a different sequence — the MD5 is the only thing that "
        "tells them apart."
    ),
)

#: JHU RefSeq + Liftoff v5.3. No checksum is published for this file at the
#: source (unlike the FASTA); this script computes one on download and writes
#: it to BUILD_PROVENANCE.json rather than claiming a verification that
#: cannot happen.
PRIMARY_ANNOTATION_SOURCE = Source(
    label="chm13v2.0_RefSeq_Liftoff_v5.3",
    url=(
        "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/"
        "assemblies/annotation/chm13v2.0_RefSeq_Liftoff_v5.3.gff.gz"
    ),
    expected_md5=None,
    approx_size_bytes=44_100_000,
    note=(
        "Curated Y-chromosome ampliconic genes, latest RefSeq/Liftoff release as of "
        "this script's writing. Confirmed via the NCBI datasets API "
        "(GCF_009914755.1 vs its GenBank pair GCA_009914755.4) that RefSeq's "
        "CHM13v2.0 'Removed chromosome MT' — this annotation is not expected to "
        "cover the mitochondrial contig, which is why a separate source is needed "
        "for it rather than a missing-chrM check being treated as a bug in this file."
    ),
)

#: Neither of these is assumed correct. `compare-chrm-candidates` decides
#: between them from evidence — see the module docstring.
CHRM_CANDIDATES: tuple[Source, ...] = (
    Source(
        label="ucsc_cat_liftoff_whole_genome",
        url=(
            "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/"
            "assemblies/annotation/chm13.draft_v2.0.gene_annotation.gff3"
        ),
        expected_md5=None,
        approx_size_bytes=3_980_115_260,
        note="UCSC GENCODEv35 CAT+Liftoff, whole genome, uncompressed GFF3.",
    ),
    Source(
        label="ucsc_cat_liftoff_vep",
        url=(
            "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/"
            "assemblies/annotation/chm13v2.0_GENCODEv35_CAT_Liftoff.vep.gff3.gz"
        ),
        expected_md5=None,
        approx_size_bytes=158_318_946,
        note=(
            "Same CAT+Liftoff annotation, VEP-formatted and bgzip/tabix-indexed. "
            "Smaller because VEP's format drops fields the plain GFF3 keeps; "
            "whether that includes or excludes the mitochondrial gene set is "
            "exactly what compare-chrm-candidates has to determine, not assume."
        ),
    ),
)

#: The 13 canonical human mitochondrial protein-coding genes. Matches the
#: `MT-` prefix convention `src/species.py` already uses for human
#: (`mito_prefix="MT-"`) — this is the target list a chrM annotation is
#: checked against, not an assumption about what the *contig* is named. The
#: contig itself is found by sequence length (see `detect_mitochondrial_contig`).
CANONICAL_MT_GENES: tuple[str, ...] = (
    "MT-ND1", "MT-ND2", "MT-CO1", "MT-CO2", "MT-ATP8", "MT-ATP6", "MT-CO3",
    "MT-ND3", "MT-ND4L", "MT-ND4", "MT-ND5", "MT-ND6", "MT-CYB",
)

#: Liftoff/CAT annotations do not reliably carry the `MT-` prefix; the bare
#: names are matched too, case-insensitively, so a candidate is not marked
#: "missing" a gene it actually has under a different but recognisable label.
_MT_GENE_BARE = tuple(name.removeprefix("MT-") for name in CANONICAL_MT_GENES)

#: Human mtDNA is 16,569 bp (rCRS length; CHM13's own mitochondrion,
#: CP068254.1 per NCBI's GCA_009914755.4 sequence report, is the same length).
#: A small tolerance is kept for assemblies that pad or trim by a few bases,
#: but a match on length alone is not trusted without also checking the name
#: — see `detect_mitochondrial_contig`.
MT_LENGTH_BP = 16_569
MT_LENGTH_TOLERANCE_BP = 200

#: RefSeq's own biotype vocabulary, as `docs`/`reference/README.md` records it.
#: A `mkgtf` filter list written against GENCODE's vocabulary
#: (`IG_V_gene`, `TR_V_gene`, ...) silently drops every one of these.
REFSEQ_MKGTF_ATTRIBUTES: tuple[str, ...] = (
    "gene_biotype:protein_coding",
    "gene_biotype:lncRNA",
    "gene_biotype:antisense_RNA",
    "gene_biotype:V_segment",
    "gene_biotype:C_region",
    "gene_biotype:J_segment",
    "gene_biotype:D_segment",
    "gene_biotype:miRNA",
    "gene_biotype:snRNA",
    "gene_biotype:snoRNA",
    "gene_biotype:rRNA",
)


# --------------------------------------------------------------------------
# Small, honest primitives — every one used by both build and provenance
# --------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_of(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 -- matching upstream's own published checksum algorithm, not used for security
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def iter_fasta_lengths(path: Path) -> Iterator[tuple[str, int]]:
    """`(sequence_name, length_bp)` for every record, streamed.

    Never loads the sequence into memory — only the running length. A T2T
    FASTA is ~3 GB decompressed; this reads it once, in one pass, and holds
    at most one sequence's worth of nothing.
    """
    name: str | None = None
    length = 0
    with _open_maybe_gzip(path) as handle:
        for line in handle:
            if line.startswith(">"):
                if name is not None:
                    yield name, length
                name = line[1:].split()[0].strip()
                length = 0
            else:
                length += len(line.strip())
    if name is not None:
        yield name, length


def detect_mitochondrial_contig(fasta_path: Path) -> str:
    """The mitochondrial contig's name, found from the sequence itself.

    Two independent signals, and both are required to agree before this
    returns without asking a person: the sequence length has to sit within
    `MT_LENGTH_TOLERANCE_BP` of the known human mtDNA length, **and** the
    contig's own name has to look mitochondrial (`MT`, `chrM`, `chrMT`,
    case-insensitive). Length alone would occasionally match a short
    unplaced scaffold; name alone would trust a header nobody has verified
    describes what it claims to.

    Raises rather than guesses when zero or more than one contig satisfies
    both — a build that assumed the first candidate would be exactly the
    silent-failure mode `reference/README.md` already documents once.
    """
    # "chrM" (UCSC convention, no trailing T) and "MT"/"chrMT" (Ensembl/RefSeq
    # convention) both name the same molecule; a pattern that only accepted
    # one family would silently refuse a correctly-built reference using the
    # other.
    name_pattern = re.compile(r"^(chr)?m(t)?$", re.IGNORECASE)
    candidates: list[tuple[str, int]] = []
    for name, length in iter_fasta_lengths(fasta_path):
        if abs(length - MT_LENGTH_BP) <= MT_LENGTH_TOLERANCE_BP and name_pattern.match(name):
            candidates.append((name, length))

    if len(candidates) == 1:
        return candidates[0][0]
    if not candidates:
        raise RuntimeError(
            f"no contig in {fasta_path.name} is both ~{MT_LENGTH_BP}bp and named like a "
            "mitochondrial contig (MT / chrM / chrMT). Cannot proceed without a human "
            "confirming which contig is mitochondrial."
        )
    raise RuntimeError(
        f"{len(candidates)} contigs in {fasta_path.name} match both the length and name "
        f"pattern for mitochondrial DNA: {candidates}. Ambiguous; refusing to guess."
    )


_GFF3_ATTR_RE = re.compile(r"(\w+)=([^;]*)")
_GTF_ATTR_RE = re.compile(r'(\w+)\s+"([^"]*)"')


def parse_attributes(column9: str) -> dict[str, str]:
    """GFF3 (`key=value;...`) or GTF (`key "value"; ...`) attributes, whichever this is."""
    if "=" in column9 and '"' not in column9:
        return dict(_GFF3_ATTR_RE.findall(column9))
    return dict(_GTF_ATTR_RE.findall(column9))


def iter_gff_rows(path: Path, *, contig: str | None = None) -> Iterator[list[str]]:
    """GFF3/GTF data rows (9 columns), streamed, optionally filtered to one contig.

    Filtering here rather than after loading everything is what makes this
    usable against the 3.98 GB whole-genome candidate: only rows for the one
    contig being compared are ever held past the loop body.
    """
    with _open_maybe_gzip(path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                continue
            if contig is not None and fields[0] != contig:
                continue
            yield fields


def gene_names_on_contig(path: Path, contig: str) -> set[str]:
    """Every `gene_name`/`Name`/`gene` attribute value seen on `contig`."""
    names: set[str] = set()
    for fields in iter_gff_rows(path, contig=contig):
        attrs = parse_attributes(fields[8])
        for key in ("gene_name", "Name", "gene"):
            if key in attrs:
                names.add(attrs[key])
    return names


def count_canonical_mt_genes(names: set[str]) -> tuple[int, list[str]]:
    """How many of the 13 canonical genes are present, matched with or without `MT-`."""
    upper = {n.upper() for n in names}
    found = [
        full for full, bare in zip(CANONICAL_MT_GENES, _MT_GENE_BARE)
        if full.upper() in upper or bare.upper() in upper
    ]
    return len(found), sorted(set(CANONICAL_MT_GENES) - set(found))


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


@dataclass
class Provenance:
    """Accumulated across every subcommand run against one build directory.

    Loaded and re-saved on each invocation rather than written once at the
    end, so a build that stops partway (network failure, a person killing it
    to check something) still leaves a record of what actually happened
    up to that point — the same reasoning `audit.jsonl` is append-only for
    in the main pipeline.
    """

    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load_or_create(cls, build_dir: Path) -> "Provenance":
        path = build_dir / "BUILD_PROVENANCE.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {
                "script": "scripts/build_t2t_chm13_reference.py",
                "target": "T2T_CHM13v2_RefSeqLiftoff_v5_3",
                "host": platform.node(),
                "python": platform.python_version(),
                "steps": [],
            }
        return cls(path=path, data=data)

    def record_step(self, name: str, **fields: Any) -> None:
        from datetime import datetime, timezone

        self.data["steps"].append({
            "step": name,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **fields,
        })
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> int:
    print(__doc__)
    print("=" * 78)
    print(f"build directory:  {BUILD_DIR}")
    print(f"output directory: {OUTPUT_DIR}")
    print()
    print("sources:")
    for source in (FASTA_SOURCE, PRIMARY_ANNOTATION_SOURCE, *CHRM_CANDIDATES):
        checksum = f"md5={source.expected_md5}" if source.expected_md5 else "no published checksum"
        print(f"  {source.label:32} {source.approx_size_bytes / GB:5.2f} GB  {checksum}")
        print(f"    {source.url}")
        print(f"    {source.note}")
    print()
    print("nothing was downloaded, built, or modified. Every action below needs its own flag.")
    return 0


def _require(flag_value: bool, message: str) -> None:
    if not flag_value:
        print(f"refusing: {message}", file=sys.stderr)
        raise SystemExit(2)


def _download(source: Source, dest: Path, provenance: Provenance) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == source.approx_size_bytes:
        print(f"  have {dest.name} already ({dest.stat().st_size:,} bytes) — skipping download")
    else:
        print(f"  downloading {source.url}")
        urllib.request.urlretrieve(source.url, dest)  # noqa: S310 -- fixed https URL from a hardcoded source table, not user input

    got_md5 = md5_of(dest) if source.expected_md5 else None
    got_sha256 = sha256_of(dest)
    if source.expected_md5 is not None:
        if got_md5 != source.expected_md5:
            provenance.record_step(
                f"download:{source.label}", url=source.url, status="checksum_mismatch",
                expected_md5=source.expected_md5, got_md5=got_md5,
            )
            raise RuntimeError(
                f"{dest.name}: MD5 mismatch — expected {source.expected_md5}, got {got_md5}. "
                "This is very likely the wrong file (e.g. maskedY_rCRS instead of maskedY, "
                "which is the same size). Not proceeding."
            )
    provenance.record_step(
        f"download:{source.label}", url=source.url, status="ok",
        size_bytes=dest.stat().st_size, md5=got_md5, sha256=got_sha256,
        md5_verified_against_published=source.expected_md5 is not None,
    )
    return dest


def cmd_fetch_fasta(args: argparse.Namespace) -> int:
    _require(args.i_confirm_download, "pass --i-confirm-download to fetch ~980 MB")
    provenance = Provenance.load_or_create(BUILD_DIR)
    dest = BUILD_DIR / "chm13v2.0_maskedY.fa.gz"
    _download(FASTA_SOURCE, dest, provenance)
    print(f"FASTA verified against published MD5 {FASTA_SOURCE.expected_md5}")
    return 0


def cmd_fetch_primary_annotation(args: argparse.Namespace) -> int:
    _require(args.i_confirm_download, "pass --i-confirm-download to fetch ~44 MB")
    provenance = Provenance.load_or_create(BUILD_DIR)
    dest = BUILD_DIR / "chm13v2.0_RefSeq_Liftoff_v5.3.gff.gz"
    _download(PRIMARY_ANNOTATION_SOURCE, dest, provenance)
    print("Primary annotation downloaded. No published checksum exists upstream for this "
          "file; its sha256 is recorded in BUILD_PROVENANCE.json for future comparison.")
    return 0


def cmd_compare_chrm_candidates(args: argparse.Namespace) -> int:
    """Download both chrM candidates and decide between them from evidence, or fail closed."""
    _require(args.i_confirm_download, "pass --i-confirm-download to fetch up to ~4 GB")
    provenance = Provenance.load_or_create(BUILD_DIR)

    fasta_gz = BUILD_DIR / "chm13v2.0_maskedY.fa.gz"
    if not fasta_gz.exists():
        raise SystemExit(
            "run fetch-fasta first — the mitochondrial contig is detected from the FASTA, "
            "not assumed to be named 'chrM'."
        )
    mt_contig = detect_mitochondrial_contig(fasta_gz)
    print(f"mitochondrial contig detected from FASTA: {mt_contig!r}")

    results = []
    for source in CHRM_CANDIDATES:
        dest = BUILD_DIR / Path(source.url).name
        _download(source, dest, provenance)
        # This candidate's own contig for the same sequence may be named
        # differently (e.g. "chrM" here even where the primary FASTA's is
        # "MT" elsewhere) — both spellings are tried since detection here is
        # by name, not by re-deriving length from a second, differently
        # formatted FASTA this candidate does not ship.
        found_contig = None
        for candidate_name in {mt_contig, mt_contig.upper(), f"chr{mt_contig}",
                                mt_contig.removeprefix("chr"), mt_contig.removeprefix("chr").upper()}:
            names = gene_names_on_contig(dest, candidate_name)
            if names:
                found_contig = candidate_name
                break
        else:
            names = set()

        n_found, missing = count_canonical_mt_genes(names)
        results.append({
            "label": source.label,
            "file": dest.name,
            "contig_name_used": found_contig,
            "canonical_genes_found": n_found,
            "canonical_genes_missing": missing,
            "total_gene_names_on_contig": len(names),
        })

    print()
    print("chrM candidate comparison:")
    print(f"  {'candidate':32} {'contig':8} {'genes found':12} missing")
    for r in results:
        print(f"  {r['label']:32} {str(r['contig_name_used']):8} "
              f"{r['canonical_genes_found']:>3} / {len(CANONICAL_MT_GENES)}       "
              f"{', '.join(r['canonical_genes_missing']) or '-'}")

    provenance.record_step("compare_chrm_candidates", mitochondrial_contig=mt_contig, results=results)

    complete = [r for r in results if r["canonical_genes_found"] == len(CANONICAL_MT_GENES)]
    if len(complete) == 1:
        winner = complete[0]
        provenance.record_step("chrm_candidate_selected", **winner)
        print(f"\nunambiguous: {winner['label']} carries all {len(CANONICAL_MT_GENES)} "
              "canonical genes and is the only candidate that does.")
        return 0

    print(
        f"\nFAIL CLOSED: {len(complete)} candidate(s) carry all {len(CANONICAL_MT_GENES)} "
        "canonical mitochondrial genes. A build needs exactly one. Not selecting one "
        "automatically — inspect the comparison above (and BUILD_PROVENANCE.json) and "
        "decide by hand, or supply a third candidate.",
        file=sys.stderr,
    )
    return 1


#: What `cellranger mkref` actually needs to see in a GTF, distilled from
#: `reference/README.md` problem 3 and Cell Ranger's own documented
#: requirements: `gene`, `transcript` and `exon` feature rows, every exon
#: carrying both `gene_id` and `transcript_id` (STAR indexes off these, not
#: off the ID/Parent chain GFF3 uses), and a `gene_id` that never resolves to
#: two different `gene_name`s (the liftover failure `check_gene_id_consistency`
#: in the validator already guards against post-build).
MKREF_REQUIRED_FEATURE_TYPES = ("gene", "transcript", "exon")


@dataclass
class SchemaAudit:
    """One candidate annotation's shape, measured rather than assumed.

    Every field here is something `cellranger mkgtf`/`mkref` either depends
    on directly (gene_id/transcript_id on exons) or that a bad liftover would
    corrupt silently (gene_id -> multiple gene_name). Counting these is what
    "no structural problems" in the module docstring's build order actually
    means — a candidate is not judged by having complete chrM gene coverage
    alone (that is `compare-chrm-candidates`'s question), but by whether its
    *entire* file is shaped the way mkgtf/mkref require.
    """

    label: str
    file: str
    total_rows: int
    feature_counts: dict[str, int]
    contigs: list[str]
    gene_count: int
    gene_id_missing: int
    gene_name_missing: int
    transcript_count: int
    transcript_id_missing: int
    transcript_gene_id_missing: int
    exon_count: int
    exon_gene_id_missing: int
    exon_gene_name_missing: int
    exon_transcript_id_missing: int
    gene_id_conflicts: dict[str, list[str]]  # capped sample; see audit_annotation_schema
    gene_id_conflict_count: int
    biotype_key_used: str | None
    biotype_distribution: dict[str, int]
    blockers: list[str]

    @property
    def mkref_ready(self) -> bool:
        return not self.blockers


def audit_annotation_schema(path: Path, label: str) -> SchemaAudit:
    """One streaming pass over `path`. Never loads more than one row at a time —
    the whole-genome candidate is ~3.98 GB uncompressed, and this is run
    against it directly, the same way `iter_gff_rows` is used elsewhere."""
    feature_counts: dict[str, int] = {}
    contigs: set[str] = set()
    total_rows = 0
    gene_count = gene_id_missing = gene_name_missing = 0
    transcript_count = transcript_id_missing = transcript_gene_id_missing = 0
    exon_count = exon_gene_id_missing = exon_gene_name_missing = exon_transcript_id_missing = 0
    gene_id_to_names: dict[str, set[str]] = {}
    biotype_counts: dict[str, int] = {}
    biotype_key_used: str | None = None

    for fields in iter_gff_rows(path):
        total_rows += 1
        contigs.add(fields[0])
        ftype = fields[2]
        feature_counts[ftype] = feature_counts.get(ftype, 0) + 1
        attrs = parse_attributes(fields[8])
        gene_id = attrs.get("gene_id")
        gene_name = attrs.get("gene_name") or attrs.get("Name")

        if gene_id and gene_name:
            gene_id_to_names.setdefault(gene_id, set()).add(gene_name)

        if ftype == "gene":
            gene_count += 1
            if not gene_id:
                gene_id_missing += 1
            if not gene_name:
                gene_name_missing += 1
            biotype = attrs.get("gene_biotype") or attrs.get("biotype")
            if biotype:
                if biotype_key_used is None:
                    biotype_key_used = "gene_biotype" if "gene_biotype" in attrs else "biotype"
                biotype_counts[biotype] = biotype_counts.get(biotype, 0) + 1
        elif ftype == "transcript":
            transcript_count += 1
            if not attrs.get("transcript_id"):
                transcript_id_missing += 1
            if not gene_id:
                transcript_gene_id_missing += 1
        elif ftype == "exon":
            exon_count += 1
            if not gene_id:
                exon_gene_id_missing += 1
            if not gene_name:
                exon_gene_name_missing += 1
            if not attrs.get("transcript_id"):
                exon_transcript_id_missing += 1

    conflicts = {gid: sorted(names) for gid, names in gene_id_to_names.items() if len(names) > 1}
    conflict_sample = dict(list(conflicts.items())[:20])

    blockers: list[str] = []
    missing_types = [t for t in MKREF_REQUIRED_FEATURE_TYPES if feature_counts.get(t, 0) == 0]
    if missing_types:
        blockers.append(f"no {'/'.join(missing_types)} rows at all")
    if gene_id_missing:
        blockers.append(f"{gene_id_missing}/{gene_count} gene rows missing gene_id")
    if gene_name_missing:
        blockers.append(f"{gene_name_missing}/{gene_count} gene rows missing gene_name")
    if exon_gene_id_missing:
        blockers.append(f"{exon_gene_id_missing}/{exon_count} exon rows missing gene_id")
    if exon_transcript_id_missing:
        blockers.append(f"{exon_transcript_id_missing}/{exon_count} exon rows missing transcript_id")
    if conflicts:
        blockers.append(f"{len(conflicts)} gene_id(s) resolve to more than one gene_name")
    if biotype_key_used is None:
        blockers.append("no gene row carries gene_biotype or biotype")

    return SchemaAudit(
        label=label, file=path.name, total_rows=total_rows,
        feature_counts=dict(sorted(feature_counts.items(), key=lambda kv: -kv[1])),
        contigs=sorted(contigs),
        gene_count=gene_count, gene_id_missing=gene_id_missing, gene_name_missing=gene_name_missing,
        transcript_count=transcript_count, transcript_id_missing=transcript_id_missing,
        transcript_gene_id_missing=transcript_gene_id_missing,
        exon_count=exon_count, exon_gene_id_missing=exon_gene_id_missing,
        exon_gene_name_missing=exon_gene_name_missing, exon_transcript_id_missing=exon_transcript_id_missing,
        gene_id_conflicts=conflict_sample, gene_id_conflict_count=len(conflicts),
        biotype_key_used=biotype_key_used, biotype_distribution=dict(sorted(biotype_counts.items(), key=lambda kv: -kv[1])),
        blockers=blockers,
    )


#: StringTie names an assembled transcript it cannot match to a known gene
#: `MSTRG.<n>`. When CAT's `exRef` mode merges such a transcript into an
#: existing locus, the transcript keeps that placeholder as its `gene_name`
#: while the locus's other rows keep the real symbol — which is why one
#: `gene_id` ends up carrying two names. Every occurrence in both real
#: candidates matches this exact shape (checked: `MSTRG.` followed by digits,
#: with an optional further `.digits`), so the pattern is anchored rather
#: than a loose substring match: a real gene legitimately called something
#: containing "MSTRG" must not be silently treated as a placeholder.
PLACEHOLDER_GENE_NAME_RE = re.compile(r"^MSTRG\.\d+(\.\d+)*$")


def is_placeholder_gene_name(name: str) -> bool:
    return bool(PLACEHOLDER_GENE_NAME_RE.match(name))


@dataclass
class ConflictClassification:
    """Every `gene_id` that carries more than one `gene_name`, sorted into the
    three cases that need three different answers.

    The distinction that matters: a placeholder next to exactly one real
    symbol is a *naming* artifact with one defensible resolution, while two
    real symbols on one `gene_id` is a genuine ambiguity no rule can settle —
    those must stop the build rather than be resolved by picking whichever
    sorts first.
    """

    #: gene_id -> the single real symbol, with >=1 MSTRG.x alongside it.
    #: Normalizable: every row is rewritten to the real symbol.
    resolvable: dict[str, str]
    #: gene_id -> the >=2 real symbols found. Not resolvable by any rule here.
    hard_conflicts: dict[str, list[str]]
    #: gene_id -> the placeholder names, with no real symbol anywhere.
    #: Left exactly as they are — inventing a symbol would be a guess.
    placeholder_only: dict[str, list[str]]
    #: Per-category row counts, so the report says how much annotation each case covers.
    counts: dict[str, dict[str, int]]


def classify_gene_name_conflicts(path: Path) -> ConflictClassification:
    """One streaming pass: which `gene_id`s conflict, and into which of the
    three cases each falls, with gene/transcript/exon row counts per case."""
    names_by_id: dict[str, set[str]] = {}
    rows_by_id: dict[str, dict[str, int]] = {}

    for fields in iter_gff_rows(path):
        attrs = parse_attributes(fields[8])
        gene_id = attrs.get("gene_id")
        if not gene_id:
            continue
        gene_name = attrs.get("gene_name") or attrs.get("Name")
        if gene_name:
            names_by_id.setdefault(gene_id, set()).add(gene_name)
        ftype = fields[2]
        if ftype in MKREF_REQUIRED_FEATURE_TYPES:
            counts = rows_by_id.setdefault(gene_id, {"gene": 0, "transcript": 0, "exon": 0})
            counts[ftype] += 1

    resolvable: dict[str, str] = {}
    hard_conflicts: dict[str, list[str]] = {}
    placeholder_only: dict[str, list[str]] = {}

    for gene_id, names in names_by_id.items():
        if len(names) < 2:
            # Not a conflict at all. A gene_id repeating one name across
            # hundreds of exon rows is normal GTF/GFF3 structure, and the
            # single-name case includes a gene_id whose only name happens to
            # be a placeholder — that is handled below only when it conflicts.
            if len(names) == 1 and is_placeholder_gene_name(next(iter(names))):
                placeholder_only[gene_id] = sorted(names)
            continue
        real = sorted(n for n in names if not is_placeholder_gene_name(n))
        if len(real) == 1:
            resolvable[gene_id] = real[0]
        elif len(real) == 0:
            placeholder_only[gene_id] = sorted(names)
        else:
            hard_conflicts[gene_id] = real

    def _tally(gene_ids) -> dict[str, int]:
        total = {"gene_ids": 0, "gene": 0, "transcript": 0, "exon": 0}
        for gene_id in gene_ids:
            total["gene_ids"] += 1
            counts = rows_by_id.get(gene_id, {})
            for key in ("gene", "transcript", "exon"):
                total[key] += counts.get(key, 0)
        return total

    return ConflictClassification(
        resolvable=resolvable,
        hard_conflicts=hard_conflicts,
        placeholder_only=placeholder_only,
        counts={
            "resolvable": _tally(resolvable),
            "hard_conflicts": _tally(hard_conflicts),
            "placeholder_only": _tally(placeholder_only),
        },
    )


def _rewrite_attribute_column(column9: str, new_name: str) -> str:
    """Set `gene_name`/`Name` to `new_name`, keeping the old value as
    `original_gene_name`, and leaving every other attribute byte-identical.

    Rebuilt by splitting on `;` rather than by parsing into a dict and
    re-serialising: a dict round-trip would silently drop any attribute whose
    shape `parse_attributes`'s regex does not match, and this file has ~3.9M
    rows in which one such drop would be invisible.
    """
    parts = column9.split(";")
    original: str | None = None
    out: list[str] = []
    for part in parts:
        if part.startswith("gene_name="):
            original = original or part[len("gene_name="):]
            out.append(f"gene_name={new_name}")
        elif part.startswith("Name="):
            original = original or part[len("Name="):]
            out.append(f"Name={new_name}")
        else:
            out.append(part)
    if original is not None and original != new_name:
        out.append(f"original_gene_name={original}")
    return ";".join(out)


def normalize_annotation(source_path: Path, dest_path: Path, classification: ConflictClassification) -> dict[str, int]:
    """Write `source_path` to `dest_path` with resolvable placeholder names
    replaced by their locus's real symbol.

    Refuses outright if any hard conflict exists — the caller checks this too,
    but a function that can write a normalized file while unresolvable
    ambiguity remains is one an incautious future caller can misuse.
    """
    if classification.hard_conflicts:
        raise RuntimeError(
            f"{len(classification.hard_conflicts)} gene_id(s) carry two or more real gene_names; "
            "refusing to write a normalized annotation while any remain unresolved."
        )

    rewritten_rows = 0
    touched_gene_ids: set[str] = set()
    opener = gzip.open if dest_path.suffix == ".gz" else open
    with _open_maybe_gzip(source_path) as handle, opener(dest_path, "wt", encoding="utf-8") as out:
        for line in handle:
            if not line or line.startswith("#"):
                out.write(line)
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 9:
                out.write(line)
                continue
            attrs = parse_attributes(fields[8])
            gene_id = attrs.get("gene_id")
            target = classification.resolvable.get(gene_id) if gene_id else None
            if target is None:
                out.write(line)
                continue
            current = attrs.get("gene_name") or attrs.get("Name")
            if current == target:
                out.write(line)
                continue
            fields[8] = _rewrite_attribute_column(fields[8], target)
            out.write("\t".join(fields) + "\n")
            rewritten_rows += 1
            touched_gene_ids.add(gene_id)

    return {"rewritten_rows": rewritten_rows, "gene_ids_normalized": len(touched_gene_ids)}


def _print_classification(label: str, c: ConflictClassification) -> None:
    print(f"\n{label} — gene_name conflict classification:")
    print(f"  case 1, one real symbol + MSTRG.x  (normalizable): {c.counts['resolvable']}")
    print(f"  case 2, two or more real symbols   (fail closed):  {c.counts['hard_conflicts']}")
    print(f"  case 3, MSTRG.x only               (left as-is):   {c.counts['placeholder_only']}")
    if c.resolvable:
        sample = dict(list(c.resolvable.items())[:5])
        print(f"    case 1 sample: {sample}")
    if c.hard_conflicts:
        print(f"    case 2 FULL LIST ({len(c.hard_conflicts)}): {c.hard_conflicts}")
    if c.placeholder_only:
        sample = dict(list(c.placeholder_only.items())[:5])
        print(f"    case 3 sample: {sample}")


def cmd_classify_gene_name_conflicts(args: argparse.Namespace) -> int:
    """Sort every conflicting gene_id into the three cases, and report counts.
    Reads only; writes nothing but a provenance record."""
    provenance = Provenance.load_or_create(BUILD_DIR)
    exit_code = 0
    for source in CHRM_CANDIDATES:
        path = BUILD_DIR / Path(source.url).name
        if not path.exists():
            raise SystemExit(f"{path.name} not found — run compare-chrm-candidates first.")
        print(f"classifying {source.label} ({path.name}) — streaming the whole file")
        c = classify_gene_name_conflicts(path)
        _print_classification(source.label, c)
        provenance.record_step(
            "classify_gene_name_conflicts", candidate=source.label, file=path.name,
            counts=c.counts,
            hard_conflicts=c.hard_conflicts,
            resolvable_sample=dict(list(c.resolvable.items())[:50]),
            placeholder_only_sample=dict(list(c.placeholder_only.items())[:50]),
        )
        if c.hard_conflicts:
            print(f"FAIL CLOSED: {source.label} has {len(c.hard_conflicts)} gene_id(s) with two or "
                  "more real gene_names. No rule can pick between two real symbols; a human has to.",
                  file=sys.stderr)
            exit_code = 1
    return exit_code


def cmd_normalize_annotations(args: argparse.Namespace) -> int:
    """Classify, then write a normalized copy of each candidate, then say what changed.

    Fails closed before writing anything if any hard conflict exists, so a
    normalized file never exists in a state where some ambiguity was resolved
    by guesswork.
    """
    provenance = Provenance.load_or_create(BUILD_DIR)
    for source in CHRM_CANDIDATES:
        path = BUILD_DIR / Path(source.url).name
        if not path.exists():
            raise SystemExit(f"{path.name} not found — run compare-chrm-candidates first.")
        print(f"classifying {source.label} ({path.name})")
        c = classify_gene_name_conflicts(path)
        _print_classification(source.label, c)

        if c.hard_conflicts:
            provenance.record_step(
                "normalize_annotation", candidate=source.label, status="fail_closed",
                hard_conflicts=c.hard_conflicts, counts=c.counts,
            )
            print(f"FAIL CLOSED: {source.label} has {len(c.hard_conflicts)} unresolvable gene_id(s) "
                  "(two or more real gene_names). Not writing a normalized file.", file=sys.stderr)
            return 1

        dest = BUILD_DIR / f"normalized.{path.name}"
        print(f"  writing {dest.name}")
        stats = normalize_annotation(path, dest, c)
        print(f"  normalized {stats['gene_ids_normalized']} gene_id(s) across "
              f"{stats['rewritten_rows']:,} rewritten row(s); original names kept as original_gene_name")
        provenance.record_step(
            "normalize_annotation", candidate=source.label, status="ok",
            source_file=path.name, normalized_file=dest.name,
            rule=("a gene_id carrying exactly one non-placeholder gene_name and one or more "
                  "MSTRG.<n> placeholders has every row rewritten to that one real symbol; the "
                  "displaced name is preserved on the row as original_gene_name. gene_ids with "
                  "two or more real names fail closed. gene_ids with only placeholders are left "
                  "untouched — no symbol is invented."),
            counts=c.counts, **stats,
        )
    return 0


def cmd_schema_audit_chrm_candidates(args: argparse.Namespace) -> int:
    """Full-file structural audit of both chrM candidates — not just their
    chrM gene coverage, which `compare-chrm-candidates` already settled.

    A candidate can carry all 13 canonical mitochondrial genes and still be
    unusable as the primary annotation source if its genome-wide gene_id/
    gene_name/biotype attributes are inconsistent — this is the check that
    decides that question with evidence instead of assuming a smaller or
    newer-looking file is automatically fine.
    """
    provenance = Provenance.load_or_create(BUILD_DIR)
    normalized = getattr(args, "normalized", False)
    audits = []
    for source in CHRM_CANDIDATES:
        raw_path = BUILD_DIR / Path(source.url).name
        path = BUILD_DIR / f"normalized.{raw_path.name}" if normalized else raw_path
        if not path.exists():
            raise SystemExit(
                f"{path.name} not found — run "
                + ("normalize-annotations" if normalized else "compare-chrm-candidates")
                + " first."
            )
        print(f"auditing {source.label} ({path.name}) — this streams the whole file, "
              f"expect this to take a few minutes for the larger candidate")
        audit = audit_annotation_schema(path, source.label)
        audits.append(audit)

        print(f"\n{audit.label} ({audit.file}):")
        print(f"  total rows: {audit.total_rows:,}")
        print(f"  feature counts: {audit.feature_counts}")
        print(f"  contigs ({len(audit.contigs)}): {audit.contigs}")
        print(f"  genes: {audit.gene_count:,}  (missing gene_id: {audit.gene_id_missing}, "
              f"missing gene_name: {audit.gene_name_missing})")
        print(f"  transcripts: {audit.transcript_count:,}  (missing transcript_id: "
              f"{audit.transcript_id_missing}, missing gene_id: {audit.transcript_gene_id_missing})")
        print(f"  exons: {audit.exon_count:,}  (missing gene_id: {audit.exon_gene_id_missing}, "
              f"missing gene_name: {audit.exon_gene_name_missing}, missing transcript_id: "
              f"{audit.exon_transcript_id_missing})")
        print(f"  gene_id -> multiple gene_name conflicts: {audit.gene_id_conflict_count}"
              + (f" (sample: {audit.gene_id_conflicts})" if audit.gene_id_conflicts else ""))
        print(f"  biotype key used: {audit.biotype_key_used!r}")
        print(f"  biotype distribution: {audit.biotype_distribution}")
        print(f"  mkref-ready: {audit.mkref_ready}"
              + (f"  blockers: {audit.blockers}" if audit.blockers else ""))

    provenance.record_step(
        "schema_audit_chrm_candidates_normalized" if normalized else "schema_audit_chrm_candidates",
        candidates=[
            {
                "label": a.label, "file": a.file, "total_rows": a.total_rows,
                "feature_counts": a.feature_counts, "contigs": a.contigs,
                "gene_count": a.gene_count, "gene_id_missing": a.gene_id_missing,
                "gene_name_missing": a.gene_name_missing, "transcript_count": a.transcript_count,
                "transcript_id_missing": a.transcript_id_missing,
                "transcript_gene_id_missing": a.transcript_gene_id_missing,
                "exon_count": a.exon_count, "exon_gene_id_missing": a.exon_gene_id_missing,
                "exon_gene_name_missing": a.exon_gene_name_missing,
                "exon_transcript_id_missing": a.exon_transcript_id_missing,
                "gene_id_conflict_count": a.gene_id_conflict_count,
                "gene_id_conflicts_sample": a.gene_id_conflicts,
                "biotype_key_used": a.biotype_key_used, "biotype_distribution": a.biotype_distribution,
                "blockers": a.blockers, "mkref_ready": a.mkref_ready,
            }
            for a in audits
        ],
    )

    print()
    if all(a.mkref_ready for a in audits):
        print("no structural problems found in either candidate.")
        return 0
    for a in audits:
        if not a.mkref_ready:
            print(f"BLOCKED: {a.label} has structural problems: {a.blockers}", file=sys.stderr)
    return 1


def cmd_select_chrm_candidate(args: argparse.Namespace) -> int:
    """Record a human's choice of chrM candidate when `compare-chrm-candidates`
    itself failed closed (tied on canonical gene coverage) — never automatic.

    This does not run without `--i-confirm-human-selection`, and it refuses if
    `schema_audit_chrm_candidates` has not been recorded for the requested
    label, or found that label unready — this is a place to *record* a human
    decision that was actually informed by the audit, not a shortcut around it.
    """
    _require(
        args.i_confirm_human_selection,
        "pass --i-confirm-human-selection to record a candidate a human picked by hand "
        "(compare-chrm-candidates did not pick one automatically)",
    )
    provenance = Provenance.load_or_create(BUILD_DIR)

    # The normalized audit is the authority when one exists, because
    # normalization is precisely what resolves the MSTRG.x placeholder
    # conflicts the raw audit reports — judging a normalized build by the raw
    # file's blockers would refuse a candidate for a defect that no longer
    # exists in what will actually be built. The raw audit is still consulted
    # when nothing has been normalized, so this never *weakens* the gate:
    # a candidate with real (non-placeholder) problems fails either way,
    # since normalize-annotations itself fails closed on those.
    normalized_steps = [s for s in provenance.data["steps"] if s["step"] == "schema_audit_chrm_candidates_normalized"]
    raw_steps = [s for s in provenance.data["steps"] if s["step"] == "schema_audit_chrm_candidates"]
    audit_steps = normalized_steps or raw_steps
    which = "normalized" if normalized_steps else "raw"
    if not audit_steps:
        raise SystemExit("no schema audit recorded — run schema-audit-chrm-candidates first.")

    if normalized_steps:
        normalize_ok = [
            s for s in provenance.data["steps"]
            if s["step"] == "normalize_annotation" and s.get("status") == "ok"
            and s.get("candidate") == args.label
        ]
        if not normalize_ok:
            raise SystemExit(
                f"a normalized audit exists but no successful normalize_annotation step for "
                f"{args.label!r} — refusing to credit that audit to a candidate that was never "
                "normalized."
            )

    candidates_by_label = {c["label"]: c for c in audit_steps[-1]["candidates"]}
    audited = candidates_by_label.get(args.label)
    if audited is None:
        raise SystemExit(f"{args.label!r} was not one of the audited candidates: {list(candidates_by_label)}")
    if not audited["mkref_ready"]:
        raise SystemExit(f"{args.label!r} failed the {which} schema audit: {audited['blockers']}. Not recording it.")

    comparison_steps = [s for s in provenance.data["steps"] if s["step"] == "compare_chrm_candidates"]
    if not comparison_steps:
        raise SystemExit("no compare_chrm_candidates step recorded — run compare-chrm-candidates first.")
    comparison_results = {r["label"]: r for r in comparison_steps[-1]["results"]}
    comparison = comparison_results.get(args.label)
    if comparison is None:
        raise SystemExit(f"{args.label!r} was not one of the compared candidates: {list(comparison_results)}")

    winner = {
        "label": comparison["label"], "file": comparison["file"],
        "contig_name_used": comparison["contig_name_used"],
        "canonical_genes_found": comparison["canonical_genes_found"],
        "selected_by": "human",
        "audit_basis": which,
        "reason": args.reason or "tied on canonical mitochondrial gene coverage; human chose by schema audit",
    }
    provenance.record_step("chrm_candidate_selected", **winner)
    print(f"recorded human selection: {args.label} — {winner['reason']}")
    return 0


def cmd_merge_chrm(args: argparse.Namespace) -> int:
    provenance = Provenance.load_or_create(BUILD_DIR)
    selected = [s for s in provenance.data["steps"] if s["step"] == "chrm_candidate_selected"]
    if not selected:
        raise SystemExit(
            "no chrm_candidate_selected step in BUILD_PROVENANCE.json — run "
            "compare-chrm-candidates first, and only proceed here if it exited 0."
        )
    winner = selected[-1]
    print(f"merging chrM annotation from {winner['file']} "
          f"(contig {winner['contig_name_used']!r}) into the primary annotation")
    print("NOT IMPLEMENTED in this dry-run-only revision — the comparison and the fail-closed "
          "gate above are complete and tested; the merge itself is intentionally left for the "
          "build turn, once a human has reviewed which candidate compare-chrm-candidates picked.")
    return 1


def cmd_gff3_to_gtf(args: argparse.Namespace) -> int:
    print("NOT IMPLEMENTED in this dry-run-only revision. Design, for review:")
    print(
        "  Two passes over the merged GFF3. Pass 1 walks gene and transcript features and "
        "builds id -> (gene_id, gene_name, transcript_id) from the GFF3 ID/Parent chain. "
        "Pass 2 walks every exon (and other feature) row, resolves its Parent to that table, "
        "and writes a GTF line carrying gene_id, gene_name and transcript_id explicitly — "
        "which is problem 3 from the module docstring, solved rather than deferred to mkref, "
        "which does not do this propagation itself."
    )
    return 1


def cmd_mkgtf(args: argparse.Namespace) -> int:
    binary = shutil.which("cellranger") or "cellranger"
    attr_flags = " ".join(f'--attribute="{a}"' for a in REFSEQ_MKGTF_ATTRIBUTES)
    print("NOT IMPLEMENTED in this dry-run-only revision. The command this will run:")
    print(f"  {binary} mkgtf <merged>.gtf <filtered>.gtf {attr_flags}")
    print("  using the RefSeq biotype vocabulary above — a GENCODE-shaped filter list would "
          "silently drop every immune receptor segment (problem 4).")
    return 1


def cmd_mkref(args: argparse.Namespace) -> int:
    _require(args.i_confirm_build, "pass --i-confirm-build to run cellranger mkref")
    binary = shutil.which("cellranger") or "cellranger"
    cmd = [
        binary, "mkref",
        "--genome=T2T_CHM13v2_RefSeqLiftoff_v5_3",
        f"--fasta={BUILD_DIR / 'chm13v2.0_maskedY.fa'}",
        f"--genes={BUILD_DIR / 'filtered.gtf'}",
        f"--nthreads={args.nthreads}",
        f"--memgb={args.memgb}",
        "--ref-version=t2t-chm13v2.0-refseq-liftoff-v5.3+chrm",
    ]
    print("NOT IMPLEMENTED in this dry-run-only revision. The command this will run:")
    print("  " + " ".join(cmd))
    return 1


def cmd_all(args: argparse.Namespace) -> int:
    print("`all` chains every subcommand above, in order, stopping at the first non-zero exit. "
          "Not runnable in this revision because gff3-to-gtf, mkgtf and merge-chrm are not "
          "implemented yet — see their own --help output for why.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=False)

    sub.add_parser("plan", help="print the plan; the default action; touches nothing").set_defaults(func=cmd_plan)

    p = sub.add_parser("fetch-fasta", help="download and MD5-verify the maskedY FASTA (~980 MB)")
    p.add_argument("--i-confirm-download", action="store_true")
    p.set_defaults(func=cmd_fetch_fasta)

    p = sub.add_parser("fetch-primary-annotation", help="download the v5.3 RefSeq/Liftoff GFF3 (~44 MB)")
    p.add_argument("--i-confirm-download", action="store_true")
    p.set_defaults(func=cmd_fetch_primary_annotation)

    p = sub.add_parser(
        "compare-chrm-candidates",
        help="download both chrM candidates (~4 GB total) and pick one from evidence, or fail closed",
    )
    p.add_argument("--i-confirm-download", action="store_true")
    p.set_defaults(func=cmd_compare_chrm_candidates)

    p = sub.add_parser(
        "schema-audit-chrm-candidates",
        help="full-file structural audit of both chrM candidates (gene/transcript/exon counts, "
             "gene_id/gene_name completeness and conflicts, biotype coverage, mkref-readiness)",
    )
    p.add_argument("--normalized", action="store_true",
                   help="audit the normalized.* copies instead of the raw downloads")
    p.set_defaults(func=cmd_schema_audit_chrm_candidates)

    sub.add_parser(
        "classify-gene-name-conflicts",
        help="sort every conflicting gene_id into: one real symbol + MSTRG.x (normalizable), "
             "two or more real symbols (fail closed), or MSTRG.x only (left alone)",
    ).set_defaults(func=cmd_classify_gene_name_conflicts)

    sub.add_parser(
        "normalize-annotations",
        help="write normalized.<file> for each candidate, rewriting MSTRG.x placeholders to their "
             "locus's real symbol and preserving the old value as original_gene_name",
    ).set_defaults(func=cmd_normalize_annotations)

    p = sub.add_parser(
        "select-chrm-candidate",
        help="record a human's choice of chrM candidate when compare-chrm-candidates tied",
    )
    p.add_argument("--label", required=True, choices=[s.label for s in CHRM_CANDIDATES])
    p.add_argument("--reason", default=None)
    p.add_argument("--i-confirm-human-selection", action="store_true")
    p.set_defaults(func=cmd_select_chrm_candidate)

    sub.add_parser("merge-chrm", help="merge the winning candidate's chrM rows into the primary annotation").set_defaults(func=cmd_merge_chrm)
    sub.add_parser("gff3-to-gtf", help="convert the merged GFF3 to GTF, propagating gene_id/gene_name to every exon").set_defaults(func=cmd_gff3_to_gtf)
    sub.add_parser("mkgtf", help="filter the GTF with cellranger mkgtf, using RefSeq's biotype vocabulary").set_defaults(func=cmd_mkgtf)

    p = sub.add_parser("mkref", help="run cellranger mkref")
    p.add_argument("--i-confirm-build", action="store_true")
    p.add_argument("--nthreads", type=int, default=16)
    p.add_argument("--memgb", type=int, default=128)
    p.set_defaults(func=cmd_mkref)

    p = sub.add_parser("all", help="run every step above in order")
    p.add_argument("--i-confirm-download", action="store_true")
    p.add_argument("--i-confirm-build", action="store_true")
    p.add_argument("--nthreads", type=int, default=16)
    p.add_argument("--memgb", type=int, default=128)
    p.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "action", None):
        return cmd_plan(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
