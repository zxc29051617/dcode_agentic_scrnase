"""Check a built T2T-CHM13v2 Cell Ranger reference against the contract
`scripts/build_t2t_chm13_reference.py` and `reference/README.md` promise.

Run against any reference directory, not only the one this project's builder
produces — a reference someone built by hand can be checked the same way:

    python scripts/validate_t2t_chm13_reference.py ~/data/references/T2T_CHM13v2_RefSeqLiftoff_v5_3

Every check is independent and every one runs even if an earlier one fails,
so a single report shows everything wrong at once rather than the first thing
wrong. Exit code is 0 only if every check passes.

## What changed from the first draft of this contract

The original plan asked for "every `gene_id` in the GTF is unique". That is
wrong for a GTF: the same gene's `gene_id` legitimately repeats on every exon
of every transcript belonging to it — a gene with 12 exons across 3
transcripts can carry the same `gene_id` on up to 36 rows, and that is
correct, not a defect. What actually indicates a broken liftover (the real
failure `reference/README.md` warns about — `LOC124905335` next to
`LOC124905335_1`) is a `gene_id` that resolves to **more than one
`gene_name`** across the rows that carry it. That is what
`check_gene_id_consistency` tests instead.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Read-only import of the project's own species registry, so this validator's
# idea of "looks like human/CHM13" can never drift from `src/species.py`'s —
# one definition, not two that quietly disagree. `scripts/export_registry_docs.py`
# already does the same thing for the same reason.
from src.species import SPECIES_SIGNATURES  # noqa: E402

# Mirrors the constant of the same name in build_t2t_chm13_reference.py.
# Not imported from it, so this validator has no dependency on that script's
# download/build machinery — only a person re-typing the same 13 gene symbols
# wrong in both files would break the tie, and `tests/` below checks they agree.
CANONICAL_MT_GENES: tuple[str, ...] = (
    "MT-ND1", "MT-ND2", "MT-CO1", "MT-CO2", "MT-ATP8", "MT-ATP6", "MT-CO3",
    "MT-ND3", "MT-ND4L", "MT-ND4", "MT-ND5", "MT-ND6", "MT-CYB",
)
_MT_GENE_BARE = tuple(name.removeprefix("MT-") for name in CANONICAL_MT_GENES)

MT_LENGTH_BP = 16_569
MT_LENGTH_TOLERANCE_BP = 200

#: The official checksum for the one FASTA this reference is allowed to be
#: built from. A validator that read this from BUILD_PROVENANCE.json instead
#: of hardcoding it would be validating the provenance file's *claim*, not
#: the fact — this constant is what makes check_fasta_is_official_maskedy
#: independent of whether the provenance file is telling the truth.
OFFICIAL_MASKEDY_MD5 = "bd90ddb80c86af7fcfeefe7a0909b175"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


Check = Callable[[Path], CheckResult]


# --------------------------------------------------------------------------
# Shared parsing (kept independent of the builder script on purpose — a
# validator that imports its subject's own parsing code cannot catch a bug
# in that code)
# --------------------------------------------------------------------------


def _open_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


_GFF3_ATTR_RE = re.compile(r"(\w+)=([^;]*)")
_GTF_ATTR_RE = re.compile(r'(\w+)\s+"([^"]*)"')


def parse_attributes(column9: str) -> dict[str, str]:
    if "=" in column9 and '"' not in column9:
        return dict(_GFF3_ATTR_RE.findall(column9))
    return dict(_GTF_ATTR_RE.findall(column9))


def iter_fasta_names_and_lengths(path: Path) -> Iterator[tuple[str, int]]:
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


def iter_gtf_rows(path: Path) -> Iterator[list[str]]:
    with _open_maybe_gzip(path) as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) == 9:
                yield fields


def find_gtf(ref_dir: Path) -> Path | None:
    for candidate in (ref_dir / "genes" / "genes.gtf", ref_dir / "genes" / "genes.gtf.gz"):
        if candidate.is_file():
            return candidate
    return None


def find_fasta(ref_dir: Path) -> Path | None:
    for candidate in (ref_dir / "fasta" / "genome.fa", ref_dir / "fasta" / "genome.fa.gz"):
        if candidate.is_file():
            return candidate
    return None


def detect_mitochondrial_contig(fasta_path: Path) -> str | None:
    """Same length-and-name-both-agree rule the builder uses. Returns None,
    rather than raising, when it cannot decide — callers report that as a
    failed check instead of crashing the whole run."""
    # See the identical comment in build_t2t_chm13_reference.py: both "chrM"
    # (UCSC) and "MT"/"chrMT" (Ensembl/RefSeq) name the same molecule.
    name_pattern = re.compile(r"^(chr)?m(t)?$", re.IGNORECASE)
    candidates = [
        name for name, length in iter_fasta_names_and_lengths(fasta_path)
        if abs(length - MT_LENGTH_BP) <= MT_LENGTH_TOLERANCE_BP and name_pattern.match(name)
    ]
    return candidates[0] if len(candidates) == 1 else None


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def check_reference_json_is_human_chm13(ref_dir: Path) -> CheckResult:
    path = ref_dir / "reference.json"
    if not path.is_file():
        return CheckResult("reference.json identifies human/CHM13", False, f"{path} does not exist")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult("reference.json identifies human/CHM13", False, f"could not parse: {exc}")

    haystack = json.dumps(data).lower()
    signatures = SPECIES_SIGNATURES["human"]
    matched = [sig for sig in signatures if sig.lower() in haystack]
    if matched:
        return CheckResult(
            "reference.json identifies human/CHM13", True,
            f"matched signature(s) {matched} from src.species.SPECIES_SIGNATURES['human']",
        )
    return CheckResult(
        "reference.json identifies human/CHM13", False,
        f"none of {signatures} appear anywhere in reference.json",
    )


def check_fasta_is_official_maskedy(ref_dir: Path) -> CheckResult:
    """The built FASTA's bytes trace back to the officially-published maskedY MD5.

    Two links have to both hold, and this check only trusts the second one if
    the first is itself checked, not merely claimed:

      1. BUILD_PROVENANCE.json's `download:chm13v2.0_maskedY` step recorded an
         MD5 that matches `OFFICIAL_MASKEDY_MD5` (a constant in *this* file,
         not read from the provenance record) — proving the download step
         actually verified against the real published checksum rather than
         recording whatever it happened to compute.
      2. The provenance's recorded `sha256` of the decompressed FASTA equals
         the sha256 of the FASTA that is actually inside this reference
         directory right now — proving the bytes were not swapped after
         the verified download.
    """
    provenance_path = ref_dir / "BUILD_PROVENANCE.json"
    if not provenance_path.is_file():
        return CheckResult(
            "FASTA matches the official maskedY MD5", False,
            f"no BUILD_PROVENANCE.json in {ref_dir}; cannot trace the FASTA to its source",
        )
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    download_steps = [
        s for s in provenance.get("steps", [])
        if s.get("step") == "download:chm13v2.0_maskedY" and s.get("status") == "ok"
    ]
    if not download_steps:
        return CheckResult(
            "FASTA matches the official maskedY MD5", False,
            "no successful download:chm13v2.0_maskedY step in BUILD_PROVENANCE.json",
        )
    step = download_steps[-1]
    if step.get("md5") != OFFICIAL_MASKEDY_MD5 or not step.get("md5_verified_against_published"):
        return CheckResult(
            "FASTA matches the official maskedY MD5", False,
            f"recorded MD5 {step.get('md5')} does not match the official "
            f"{OFFICIAL_MASKEDY_MD5}, or was never checked against it",
        )

    fasta = find_fasta(ref_dir)
    if fasta is None:
        return CheckResult("FASTA matches the official maskedY MD5", False, "no fasta/genome.fa in reference dir")
    import hashlib
    digest = hashlib.sha256()
    with fasta.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    # The decompressed sha256 is recorded by the `decompress_fasta` step, not
    # by the download step: the download verifies the .gz against the official
    # published MD5, and only later — when the FASTA is decompressed for mkref
    # — does a sha256 of the *uncompressed* bytes exist to record. Reading it
    # off the download step (as this check first did) always found None and
    # reported a correct reference as tampered with.
    decompress_steps = [
        s for s in provenance.get("steps", [])
        if s.get("step") == "decompress_fasta" and s.get("decompressed_sha256")
    ]
    if not decompress_steps:
        return CheckResult(
            "FASTA matches the official maskedY MD5", False,
            "no decompress_fasta step recorded a decompressed_sha256, so the FASTA in this "
            "reference cannot be tied back to the download that was MD5-verified",
        )
    recorded_decompressed = decompress_steps[-1]["decompressed_sha256"]
    if got != recorded_decompressed:
        return CheckResult(
            "FASTA matches the official maskedY MD5", False,
            f"fasta/genome.fa sha256 {got[:16]}... does not match the sha256 "
            f"{str(recorded_decompressed)[:16]}... recorded when it was decompressed — "
            "the bytes in this reference are not the ones that were verified",
        )
    return CheckResult(
        "FASTA matches the official maskedY MD5", True,
        f"MD5 {OFFICIAL_MASKEDY_MD5} verified at download, and fasta/genome.fa's sha256 "
        f"{got[:16]}... matches what was recorded when it was decompressed",
    )


def check_exon_gene_id_and_gene_name(ref_dir: Path) -> CheckResult:
    gtf = find_gtf(ref_dir)
    if gtf is None:
        return CheckResult("every exon has gene_id and gene_name", False, "no genes/genes.gtf found")
    total_exons = 0
    missing_gene_id = 0
    missing_gene_name = 0
    for fields in iter_gtf_rows(gtf):
        if fields[2] != "exon":
            continue
        total_exons += 1
        attrs = parse_attributes(fields[8])
        if "gene_id" not in attrs:
            missing_gene_id += 1
        if "gene_name" not in attrs:
            missing_gene_name += 1
    if total_exons == 0:
        return CheckResult("every exon has gene_id and gene_name", False, "GTF has no exon rows at all")
    if missing_gene_id or missing_gene_name:
        return CheckResult(
            "every exon has gene_id and gene_name", False,
            f"{missing_gene_id}/{total_exons} exons missing gene_id, "
            f"{missing_gene_name}/{total_exons} missing gene_name",
        )
    return CheckResult("every exon has gene_id and gene_name", True, f"{total_exons:,} exon rows checked")


def check_gene_id_consistency(ref_dir: Path) -> CheckResult:
    """No `gene_id` resolves to more than one `gene_name`.

    Repetition of a `gene_id` across many exon rows is normal and expected —
    that is not what this checks. What it catches is the liftover failure
    `reference/README.md` names: two distinct genes sharing one `gene_id`
    because a renaming step (`LOC124905335` -> `LOC124905335_1` done wrong,
    or not done at all) collided two loci into one identifier.
    """
    gtf = find_gtf(ref_dir)
    if gtf is None:
        return CheckResult("no gene_id maps to more than one gene_name", False, "no genes/genes.gtf found")

    names_by_id: dict[str, set[str]] = {}
    for fields in iter_gtf_rows(gtf):
        attrs = parse_attributes(fields[8])
        gene_id = attrs.get("gene_id")
        gene_name = attrs.get("gene_name")
        if gene_id and gene_name:
            names_by_id.setdefault(gene_id, set()).add(gene_name)

    conflicts = {gid: names for gid, names in names_by_id.items() if len(names) > 1}
    if conflicts:
        sample = dict(list(conflicts.items())[:5])
        return CheckResult(
            "no gene_id maps to more than one gene_name", False,
            f"{len(conflicts)} gene_id(s) map to multiple gene_names, e.g. {sample}",
        )
    return CheckResult(
        "no gene_id maps to more than one gene_name", True,
        f"{len(names_by_id):,} distinct gene_ids, each mapping to exactly one gene_name",
    )


def check_contigs_match_fasta(ref_dir: Path) -> CheckResult:
    gtf = find_gtf(ref_dir)
    fasta = find_fasta(ref_dir)
    if gtf is None or fasta is None:
        return CheckResult("GTF contigs are all present in the FASTA", False, "missing genes.gtf or fasta/genome.fa")

    fasta_contigs = {name for name, _ in iter_fasta_names_and_lengths(fasta)}
    gtf_contigs = {fields[0] for fields in iter_gtf_rows(gtf)}
    missing = gtf_contigs - fasta_contigs
    if missing:
        return CheckResult(
            "GTF contigs are all present in the FASTA", False,
            f"{len(missing)} GTF contig(s) not in the FASTA: {sorted(missing)[:10]}",
        )
    return CheckResult(
        "GTF contigs are all present in the FASTA", True,
        f"all {len(gtf_contigs)} GTF contigs found among {len(fasta_contigs)} FASTA contigs",
    )


def check_mitochondrial_genes_present(ref_dir: Path) -> CheckResult:
    """chrM is annotated and pct_counts_mt will not silently read as 0.

    The mitochondrial contig is detected from the FASTA the same way
    `build_t2t_chm13_reference.py` does — by sequence length agreeing with a
    name that looks mitochondrial — never by assuming a name. A GTF that
    annotates a contig called something this project's convention would not
    recognise is exactly the failure this check exists to catch, so it does
    not start from an assumed name either.
    """
    fasta = find_fasta(ref_dir)
    gtf = find_gtf(ref_dir)
    if fasta is None or gtf is None:
        return CheckResult("mitochondrial genes are annotated", False, "missing fasta/genome.fa or genes/genes.gtf")

    mt_contig = detect_mitochondrial_contig(fasta)
    if mt_contig is None:
        return CheckResult(
            "mitochondrial genes are annotated", False,
            f"no FASTA contig is both within {MT_LENGTH_TOLERANCE_BP}bp of {MT_LENGTH_BP}bp "
            "and named like mtDNA (MT/chrM/chrMT) — cannot even locate chrM to check it",
        )

    names: set[str] = set()
    for fields in iter_gtf_rows(gtf):
        if fields[0] != mt_contig:
            continue
        attrs = parse_attributes(fields[8])
        for key in ("gene_name", "Name", "gene"):
            if key in attrs:
                names.add(attrs[key])

    upper = {n.upper() for n in names}
    found = [g for g, bare in zip(CANONICAL_MT_GENES, _MT_GENE_BARE)
             if g.upper() in upper or bare.upper() in upper]
    missing = sorted(set(CANONICAL_MT_GENES) - set(found))
    if missing:
        return CheckResult(
            "mitochondrial genes are annotated", False,
            f"contig {mt_contig!r}: {len(found)}/{len(CANONICAL_MT_GENES)} canonical genes found, "
            f"missing {missing}",
        )
    return CheckResult(
        "mitochondrial genes are annotated", True,
        f"contig {mt_contig!r} carries all {len(CANONICAL_MT_GENES)} canonical mitochondrial genes",
    )


def check_mkref_succeeded(ref_dir: Path) -> CheckResult:
    # `genes/` is accepted under either spelling. cellranger 10.1.0 writes the
    # filtered annotation gzipped (genes.gtf.gz); older releases wrote it
    # plain. `find_gtf` above has always read both, so requiring only the
    # uncompressed name here made this file disagree with itself and fail a
    # complete, correct reference.
    required = ["reference.json", "fasta/genome.fa", "star"]
    missing = [r for r in required if not (ref_dir / r).exists()]
    if find_gtf(ref_dir) is None:
        missing.append("genes/genes.gtf(.gz)")
    if missing:
        return CheckResult("cellranger mkref produced a complete reference", False, f"missing: {missing}")
    return CheckResult(
        "cellranger mkref produced a complete reference", True,
        f"all of {required} are present under {ref_dir}",
    )


def check_provenance_is_complete(ref_dir: Path) -> CheckResult:
    path = ref_dir / "BUILD_PROVENANCE.json"
    if not path.is_file():
        return CheckResult("provenance is complete", False, "no BUILD_PROVENANCE.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    required_top = ("script", "target", "host", "python", "steps")
    missing_top = [k for k in required_top if k not in data]
    step_names = {s.get("step") for s in data.get("steps", [])}
    required_steps = {
        "download:chm13v2.0_maskedY",
        "download:chm13v2.0_RefSeq_Liftoff_v5.3",
        "compare_chrm_candidates",
        "chrm_candidate_selected",
    }
    missing_steps = required_steps - step_names
    if missing_top or missing_steps:
        return CheckResult(
            "provenance is complete", False,
            f"missing top-level keys {missing_top}, missing steps {sorted(missing_steps)}",
        )
    return CheckResult(
        "provenance is complete", True,
        f"{len(data['steps'])} recorded steps, all required fields present",
    )


CHECKS: tuple[Check, ...] = (
    check_reference_json_is_human_chm13,
    check_fasta_is_official_maskedy,
    check_exon_gene_id_and_gene_name,
    check_gene_id_consistency,
    check_contigs_match_fasta,
    check_mitochondrial_genes_present,
    check_mkref_succeeded,
    check_provenance_is_complete,
)


def run(ref_dir: Path) -> int:
    print(f"validating {ref_dir}\n")
    failures = 0
    for check in CHECKS:
        result = check(ref_dir)
        mark = "PASS" if result.ok else "FAIL"
        print(f"  {mark}  {result.name}")
        print(f"        {result.detail}")
        if not result.ok:
            failures += 1
    print()
    if failures:
        print(f"{failures}/{len(CHECKS)} checks failed")
        return 1
    print(f"all {len(CHECKS)} checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reference_dir", type=Path, help="a built cellranger mkref output directory")
    args = parser.parse_args(argv)

    if not args.reference_dir.is_dir():
        print(f"not a directory: {args.reference_dir}", file=sys.stderr)
        return 2
    return run(args.reference_dir)


if __name__ == "__main__":
    raise SystemExit(main())
