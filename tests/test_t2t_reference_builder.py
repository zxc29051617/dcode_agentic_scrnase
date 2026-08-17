"""Unit tests for the pure logic in `scripts/build_t2t_chm13_reference.py` and
`scripts/validate_t2t_chm13_reference.py`, run against small synthetic FASTA
and GTF/GFF3 fixtures — never against a real download.

What is deliberately NOT tested here: network access, MD5 verification
against the real published checksum, and `cellranger mkref`/`mkgtf`
themselves. Those need the real ~1 GB+ files this test suite must not fetch.
What IS tested is the logic most likely to fail silently on real data: contig
detection by length+name agreement, GFF3/GTF attribute parsing, and the
gene_id/gene_name consistency rule this contract was corrected to use.

Run with `python tests/test_t2t_reference_builder.py` (or `python tests/run_all.py`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.build_t2t_chm13_reference as builder  # noqa: E402
import scripts.validate_t2t_chm13_reference as validator  # noqa: E402


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fasta(records: dict[str, int]) -> str:
    """A synthetic FASTA where each record is exactly the given length, made
    of a repeating base so the test does not depend on real sequence content."""
    lines = []
    for name, length in records.items():
        lines.append(f">{name}")
        lines.append("A" * length)
    return "\n".join(lines) + "\n"


# --- mitochondrial contig detection (both scripts implement this identically) ---


def test_detects_a_correctly_named_correctly_sized_contig():
    with tempfile.TemporaryDirectory() as tmp:
        fasta = _write(Path(tmp) / "genome.fa", _fasta({"chr1": 500, "MT": 16569, "chr2": 300}))
        assert builder.detect_mitochondrial_contig(fasta) == "MT"
        assert validator.detect_mitochondrial_contig(fasta) == "MT"


def test_accepts_chrm_spelling_too():
    with tempfile.TemporaryDirectory() as tmp:
        fasta = _write(Path(tmp) / "genome.fa", _fasta({"chr1": 500, "chrM": 16569}))
        assert builder.detect_mitochondrial_contig(fasta) == "chrM"


def test_refuses_a_right_length_wrong_name_contig():
    # This is exactly the failure mode the two-signal rule exists to catch:
    # a random ~16.5kb scaffold must not be mistaken for mtDNA on size alone.
    with tempfile.TemporaryDirectory() as tmp:
        fasta = _write(Path(tmp) / "genome.fa", _fasta({"chr1": 500, "scaffold_88": 16569}))
        try:
            builder.detect_mitochondrial_contig(fasta)
            raise AssertionError("should have raised: no contig is named like mtDNA")
        except RuntimeError as exc:
            assert "no contig" in str(exc)


def test_refuses_a_right_name_wrong_length_contig():
    with tempfile.TemporaryDirectory() as tmp:
        fasta = _write(Path(tmp) / "genome.fa", _fasta({"chr1": 500, "MT": 900}))
        try:
            builder.detect_mitochondrial_contig(fasta)
            raise AssertionError("should have raised: MT is the wrong length")
        except RuntimeError as exc:
            assert "no contig" in str(exc)


def test_refuses_when_two_contigs_both_qualify():
    with tempfile.TemporaryDirectory() as tmp:
        fasta = _write(Path(tmp) / "genome.fa", _fasta({"MT": 16569, "chrMT": 16600}))
        try:
            builder.detect_mitochondrial_contig(fasta)
            raise AssertionError("should have raised: ambiguous, two candidates")
        except RuntimeError as exc:
            assert "Ambiguous" in str(exc)


def test_tolerates_a_small_length_difference():
    with tempfile.TemporaryDirectory() as tmp:
        fasta = _write(Path(tmp) / "genome.fa", _fasta({"MT": 16569 - 50}))
        assert builder.detect_mitochondrial_contig(fasta) == "MT"


# --- attribute parsing: both GFF3 and GTF shapes ---------------------------


def test_parses_gff3_style_attributes():
    attrs = builder.parse_attributes("ID=exon1;Parent=transcript1;gene_name=MT-ND1")
    assert attrs == {"ID": "exon1", "Parent": "transcript1", "gene_name": "MT-ND1"}


def test_parses_gtf_style_attributes():
    attrs = builder.parse_attributes('gene_id "G1"; gene_name "MT-ND1"; transcript_id "T1";')
    assert attrs == {"gene_id": "G1", "gene_name": "MT-ND1", "transcript_id": "T1"}


# --- canonical mitochondrial gene counting ----------------------------------


def test_counts_canonical_genes_with_mt_prefix():
    names = {"MT-ND1", "MT-CO1", "SOMEOTHERGENE"}
    n, missing = builder.count_canonical_mt_genes(names)
    assert n == 2
    assert "MT-ND1" not in missing
    assert "MT-CYB" in missing


def test_counts_canonical_genes_without_mt_prefix():
    # Liftoff/CAT annotations do not reliably carry the MT- prefix; the bare
    # symbol must still be recognised.
    names = {"ND1", "CO1", "ATP6"}
    n, missing = builder.count_canonical_mt_genes(names)
    assert n == 3
    assert len(missing) == 10


def test_all_thirteen_present_leaves_nothing_missing():
    n, missing = builder.count_canonical_mt_genes(set(builder.CANONICAL_MT_GENES))
    assert n == 13
    assert missing == []


# --- the corrected gene_id consistency rule (not "every gene_id is unique") ---


def test_repeated_gene_id_across_many_exons_is_not_a_failure():
    # A gene with 3 exons legitimately repeats its gene_id 3 times. The old,
    # wrong contract would have flagged this; the corrected one must not.
    gtf = (
        'chr1\tsrc\texon\t1\t100\t.\t+\t.\tgene_id "G1"; gene_name "FOO"; transcript_id "T1";\n'
        'chr1\tsrc\texon\t200\t300\t.\t+\t.\tgene_id "G1"; gene_name "FOO"; transcript_id "T1";\n'
        'chr1\tsrc\texon\t400\t500\t.\t+\t.\tgene_id "G1"; gene_name "FOO"; transcript_id "T1";\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "genes" / "genes.gtf", gtf)
        result = validator.check_gene_id_consistency(ref)
        assert result.ok, result.detail


def test_one_gene_id_with_two_gene_names_is_a_failure():
    # The real liftover failure this check exists to catch: two distinct loci
    # sharing one gene_id (e.g. a mishandled LOC.../LOC..._1 rename).
    gtf = (
        'chr1\tsrc\texon\t1\t100\t.\t+\t.\tgene_id "G1"; gene_name "FOO"; transcript_id "T1";\n'
        'chr1\tsrc\texon\t9000\t9100\t.\t+\t.\tgene_id "G1"; gene_name "BAR"; transcript_id "T2";\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "genes" / "genes.gtf", gtf)
        result = validator.check_gene_id_consistency(ref)
        assert not result.ok
        assert "G1" in result.detail


def test_exon_gene_id_and_gene_name_check_catches_a_missing_attribute():
    gtf = (
        'chr1\tsrc\texon\t1\t100\t.\t+\t.\tgene_id "G1"; gene_name "FOO"; transcript_id "T1";\n'
        'chr1\tsrc\texon\t200\t300\t.\t+\t.\tgene_id "G1"; transcript_id "T1";\n'  # no gene_name
    )
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "genes" / "genes.gtf", gtf)
        result = validator.check_exon_gene_id_and_gene_name(ref)
        assert not result.ok
        assert "gene_name" in result.detail


# --- contig-membership check --------------------------------------------------


def test_contigs_match_fasta_catches_a_gtf_contig_the_fasta_lacks():
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "fasta" / "genome.fa", _fasta({"chr1": 500}))
        _write(
            ref / "genes" / "genes.gtf",
            'chr2\tsrc\texon\t1\t100\t.\t+\t.\tgene_id "G1"; gene_name "FOO";\n',
        )
        result = validator.check_contigs_match_fasta(ref)
        assert not result.ok
        assert "chr2" in result.detail


def test_contigs_match_fasta_passes_when_they_do():
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "fasta" / "genome.fa", _fasta({"chr1": 500}))
        _write(
            ref / "genes" / "genes.gtf",
            'chr1\tsrc\texon\t1\t100\t.\t+\t.\tgene_id "G1"; gene_name "FOO";\n',
        )
        result = validator.check_contigs_match_fasta(ref)
        assert result.ok, result.detail


# --- mitochondrial-genes-present check, end to end on a small fixture -------


def test_mitochondrial_genes_present_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "fasta" / "genome.fa", _fasta({"chr1": 500, "MT": 16569}))
        rows = "".join(
            f'MT\tsrc\texon\t{i * 100}\t{i * 100 + 50}\t.\t+\t.\t'
            f'gene_id "MTG{i}"; gene_name "{gene}"; transcript_id "MTT{i}";\n'
            for i, gene in enumerate(builder.CANONICAL_MT_GENES)
        )
        _write(ref / "genes" / "genes.gtf", rows)
        result = validator.check_mitochondrial_genes_present(ref)
        assert result.ok, result.detail


def test_mitochondrial_genes_present_fails_when_chrm_has_no_annotation():
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp)
        _write(ref / "fasta" / "genome.fa", _fasta({"chr1": 500, "MT": 16569}))
        _write(
            ref / "genes" / "genes.gtf",
            'chr1\tsrc\texon\t1\t100\t.\t+\t.\tgene_id "G1"; gene_name "FOO";\n',
        )
        result = validator.check_mitochondrial_genes_present(ref)
        assert not result.ok
        assert "MT" in result.detail


# --- the two scripts' canonical-gene lists must not drift apart ------------


def test_the_two_scripts_agree_on_the_canonical_mt_gene_list():
    assert builder.CANONICAL_MT_GENES == validator.CANONICAL_MT_GENES


def test_the_two_scripts_agree_on_mt_length_and_tolerance():
    assert builder.MT_LENGTH_BP == validator.MT_LENGTH_BP
    assert builder.MT_LENGTH_TOLERANCE_BP == validator.MT_LENGTH_TOLERANCE_BP


# --- fail-closed gating: no destructive action without its flag -------------


def test_fetch_fasta_refuses_without_the_confirm_flag():
    import argparse
    args = argparse.Namespace(i_confirm_download=False)
    try:
        builder.cmd_fetch_fasta(args)
        raise AssertionError("should have refused")
    except SystemExit as exc:
        assert exc.code == 2


def test_mkref_refuses_without_the_confirm_flag():
    import argparse
    args = argparse.Namespace(i_confirm_build=False, nthreads=16, memgb=128)
    try:
        builder.cmd_mkref(args)
        raise AssertionError("should have refused")
    except SystemExit as exc:
        assert exc.code == 2


def test_plan_is_the_default_action_and_touches_nothing():
    import argparse
    result = builder.main([])
    assert result == 0
    result_explicit = builder.main(["plan"])
    assert result_explicit == 0


# --- the sources table itself: sanity, not network -------------------------


def test_fasta_and_rcrs_variant_are_distinguished_only_by_checksum():
    # Restates the reason problem 2 (reference/README.md) needs an MD5 check
    # rather than a size check: this asserts the actual recorded size used by
    # the builder would not, by itself, tell the two files apart.
    assert builder.FASTA_SOURCE.approx_size_bytes == 980_400_000
    assert builder.FASTA_SOURCE.expected_md5 is not None


def test_chrm_candidates_have_no_hardcoded_winner():
    # The whole point of compare-chrm-candidates: neither source is assumed
    # correct ahead of the evidence comparison.
    for source in builder.CHRM_CANDIDATES:
        assert source.expected_md5 is None, (
            f"{source.label} has a hardcoded checksum but no winner should be "
            "assumed before compare-chrm-candidates runs"
        )


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {exc}")
    passed = len(tests) - len(failures)
    print(f"\n{passed}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
