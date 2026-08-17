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


# --- gene_name conflict classification and normalization --------------------
#
# The real CAT+Liftoff annotations carry 1,384 gene_ids with two gene_names,
# caused by StringTie's `MSTRG.<n>` placeholder surviving on novel-isoform
# transcripts merged into an existing locus. These fixtures reproduce each of
# the three cases that needs a different answer, at a size a test can assert on.


def _gff3_row(contig: str, ftype: str, start: int, attrs: str) -> str:
    return f"{contig}\tCAT\t{ftype}\t{start}\t{start + 100}\t.\t+\t.\t{attrs}"


def _annotation(rows: list[str]) -> str:
    return "##gff-version 3\n" + "\n".join(rows) + "\n"


def test_repeated_gene_id_across_exons_is_not_a_conflict():
    # The normal shape of any annotation: one gene, one name, many exon rows
    # all repeating that gene_id. Must not be reported as anything at all.
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_gff3_row("chr1", "gene", 100, "gene_id=G1;gene_name=REAL1")]
        rows += [
            _gff3_row("chr1", "exon", 100 + i, f"gene_id=G1;transcript_id=T1;gene_name=REAL1")
            for i in range(12)
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        assert c.resolvable == {}, c.resolvable
        assert c.hard_conflicts == {}, c.hard_conflicts
        assert c.placeholder_only == {}, c.placeholder_only


def test_real_symbol_plus_placeholder_is_resolvable():
    with tempfile.TemporaryDirectory() as tmp:
        rows = [
            _gff3_row("chr1", "gene", 100, "gene_id=G1;gene_name=CCNL2"),
            _gff3_row("chr1", "transcript", 100, "gene_id=G1;transcript_id=T1;gene_name=CCNL2"),
            _gff3_row("chr1", "exon", 100, "gene_id=G1;transcript_id=T1;gene_name=CCNL2"),
            _gff3_row("chr1", "transcript", 300, "gene_id=G1;transcript_id=T2;gene_name=MSTRG.9"),
            _gff3_row("chr1", "exon", 300, "gene_id=G1;transcript_id=T2;gene_name=MSTRG.9"),
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        assert c.resolvable == {"G1": "CCNL2"}, c.resolvable
        assert c.hard_conflicts == {}
        assert c.counts["resolvable"] == {"gene_ids": 1, "gene": 1, "transcript": 2, "exon": 2}


def test_two_real_symbols_is_a_hard_conflict():
    # No rule can pick between two real symbols, so this must fail closed
    # rather than resolve to whichever sorts first.
    with tempfile.TemporaryDirectory() as tmp:
        rows = [
            _gff3_row("chr1", "gene", 100, "gene_id=G1;gene_name=REAL1"),
            _gff3_row("chr1", "exon", 100, "gene_id=G1;transcript_id=T1;gene_name=REAL1"),
            _gff3_row("chr1", "exon", 300, "gene_id=G1;transcript_id=T2;gene_name=REAL2"),
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        assert c.hard_conflicts == {"G1": ["REAL1", "REAL2"]}, c.hard_conflicts
        assert c.resolvable == {}


def test_normalization_refuses_to_write_while_a_hard_conflict_exists():
    with tempfile.TemporaryDirectory() as tmp:
        rows = [
            _gff3_row("chr1", "gene", 100, "gene_id=G1;gene_name=REAL1"),
            _gff3_row("chr1", "exon", 300, "gene_id=G1;transcript_id=T2;gene_name=REAL2"),
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        dest = Path(tmp) / "normalized.a.gff3"
        try:
            builder.normalize_annotation(path, dest, c)
        except RuntimeError:
            assert not dest.exists(), "a normalized file must not exist after a refusal"
        else:
            raise AssertionError("normalize_annotation must refuse while hard conflicts remain")


def test_placeholder_only_gene_id_keeps_its_placeholder():
    # No real symbol exists anywhere for this locus, so inventing one would
    # be a guess. It stays MSTRG.x.
    with tempfile.TemporaryDirectory() as tmp:
        rows = [
            _gff3_row("chr1", "gene", 100, "gene_id=G2;gene_name=MSTRG.77"),
            _gff3_row("chr1", "exon", 100, "gene_id=G2;transcript_id=T9;gene_name=MSTRG.77"),
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        assert c.placeholder_only == {"G2": ["MSTRG.77"]}, c.placeholder_only
        assert c.resolvable == {} and c.hard_conflicts == {}

        dest = Path(tmp) / "normalized.a.gff3"
        builder.normalize_annotation(path, dest, c)
        text = dest.read_text(encoding="utf-8")
        assert "gene_name=MSTRG.77" in text
        assert "original_gene_name" not in text, "nothing should have been rewritten"


def test_normalization_leaves_one_gene_name_per_gene_id():
    # The property the whole step exists to establish, asserted on the
    # normalized output rather than inferred from the classification.
    with tempfile.TemporaryDirectory() as tmp:
        rows = [
            _gff3_row("chr1", "gene", 100, "gene_id=G1;gene_name=CCNL2"),
            _gff3_row("chr1", "transcript", 300, "gene_id=G1;transcript_id=T2;gene_name=MSTRG.9"),
            _gff3_row("chr1", "exon", 300, "gene_id=G1;transcript_id=T2;gene_name=MSTRG.9;Name=MSTRG.9"),
            _gff3_row("chr1", "gene", 900, "gene_id=G2;gene_name=MSTRG.77"),
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        dest = Path(tmp) / "normalized.a.gff3"
        stats = builder.normalize_annotation(path, dest, c)
        assert stats["gene_ids_normalized"] == 1, stats

        after = builder.classify_gene_name_conflicts(dest)
        assert after.resolvable == {} and after.hard_conflicts == {}, (after.resolvable, after.hard_conflicts)

        # And directly: no gene_id in the output carries two names.
        names_by_id: dict[str, set[str]] = {}
        for fields in builder.iter_gff_rows(dest):
            attrs = builder.parse_attributes(fields[8])
            gid, name = attrs.get("gene_id"), attrs.get("gene_name") or attrs.get("Name")
            if gid and name:
                names_by_id.setdefault(gid, set()).add(name)
        assert all(len(v) == 1 for v in names_by_id.values()), names_by_id
        assert names_by_id["G1"] == {"CCNL2"}
        assert names_by_id["G2"] == {"MSTRG.77"}


def test_normalization_preserves_the_displaced_name_and_other_attributes():
    with tempfile.TemporaryDirectory() as tmp:
        rows = [
            _gff3_row("chr1", "gene", 100, "gene_id=G1;gene_name=CCNL2"),
            _gff3_row("chr1", "exon", 300,
                      "source_gene=ENSG1;gene_id=G1;transcript_id=T2;gene_name=MSTRG.9;tag=basic"),
        ]
        path = _write(Path(tmp) / "a.gff3", _annotation(rows))
        c = builder.classify_gene_name_conflicts(path)
        dest = Path(tmp) / "normalized.a.gff3"
        builder.normalize_annotation(path, dest, c)
        text = dest.read_text(encoding="utf-8")
        assert "original_gene_name=MSTRG.9" in text, text
        # Every unrelated attribute survives byte-for-byte.
        assert "source_gene=ENSG1" in text and "tag=basic" in text and "transcript_id=T2" in text


def test_normalized_audit_outranks_the_raw_one_when_selecting_a_candidate():
    # The raw audit reports the MSTRG placeholder conflicts that normalization
    # exists to remove. Judging a normalized build by the raw file's blockers
    # would refuse a candidate for a defect that is no longer in what gets
    # built — this asserts the normalized audit is what the gate reads.
    import argparse

    with tempfile.TemporaryDirectory() as tmp:
        build_dir = Path(tmp)
        label = builder.CHRM_CANDIDATES[0].label
        provenance = builder.Provenance.load_or_create(build_dir)
        provenance.record_step(
            "compare_chrm_candidates", mitochondrial_contig="chrM",
            results=[{"label": label, "file": "x.gff3", "contig_name_used": "chrM",
                      "canonical_genes_found": 13, "canonical_genes_missing": [],
                      "total_gene_names_on_contig": 43}],
        )
        provenance.record_step(
            "schema_audit_chrm_candidates",
            candidates=[{"label": label, "mkref_ready": False,
                         "blockers": ["1384 gene_id(s) resolve to more than one gene_name"]}],
        )
        provenance.record_step("normalize_annotation", candidate=label, status="ok")
        provenance.record_step(
            "schema_audit_chrm_candidates_normalized",
            candidates=[{"label": label, "mkref_ready": True, "blockers": []}],
        )

        original_build_dir = builder.BUILD_DIR
        builder.BUILD_DIR = build_dir
        try:
            args = argparse.Namespace(label=label, reason="test", i_confirm_human_selection=True)
            assert builder.cmd_select_chrm_candidate(args) == 0
            reloaded = builder.Provenance.load_or_create(build_dir)
            selected = [s for s in reloaded.data["steps"] if s["step"] == "chrm_candidate_selected"]
            assert selected and selected[-1]["audit_basis"] == "normalized", selected
        finally:
            builder.BUILD_DIR = original_build_dir


def test_selection_refuses_when_a_normalized_audit_has_no_matching_normalize_step():
    # Guards against crediting a normalized audit to a candidate that was
    # never actually normalized.
    import argparse

    with tempfile.TemporaryDirectory() as tmp:
        build_dir = Path(tmp)
        label = builder.CHRM_CANDIDATES[0].label
        provenance = builder.Provenance.load_or_create(build_dir)
        provenance.record_step(
            "compare_chrm_candidates", mitochondrial_contig="chrM",
            results=[{"label": label, "file": "x.gff3", "contig_name_used": "chrM",
                      "canonical_genes_found": 13, "canonical_genes_missing": []}],
        )
        provenance.record_step(
            "schema_audit_chrm_candidates_normalized",
            candidates=[{"label": label, "mkref_ready": True, "blockers": []}],
        )
        original_build_dir = builder.BUILD_DIR
        builder.BUILD_DIR = build_dir
        try:
            args = argparse.Namespace(label=label, reason="test", i_confirm_human_selection=True)
            try:
                builder.cmd_select_chrm_candidate(args)
            except SystemExit:
                pass
            else:
                raise AssertionError("must refuse without a matching normalize_annotation step")
        finally:
            builder.BUILD_DIR = original_build_dir


# --- merge-chrm and gff3-to-gtf ---------------------------------------------
#
# The RefSeq primary annotation writes exon rows as `Parent=<transcript>;gene=...`
# with no gene_id and no gene_name; the CAT/Liftoff chrM rows carry both
# explicitly. Both have to survive the conversion, which is why these fixtures
# use both spellings rather than one.


def _fake_build_dir(tmp: Path):
    """Point the module's BUILD_DIR at a temp dir for the duration of a test."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        original = builder.BUILD_DIR
        builder.BUILD_DIR = tmp
        try:
            yield tmp
        finally:
            builder.BUILD_DIR = original

    return _ctx()


def test_gff3_to_gtf_resolves_refseq_exons_that_carry_no_gene_id():
    import argparse

    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        rows = [
            # RefSeq shape: gene names itself with ID=, exon only has Parent=.
            _gff3_row("chr1", "gene", 100, "ID=GENE1;gene_name=REAL1;gene_biotype=protein_coding"),
            _gff3_row("chr1", "transcript", 100, "ID=TX1;Parent=GENE1;transcript_biotype=mRNA"),
            _gff3_row("chr1", "exon", 100, "Parent=TX1;gene=REAL1;exon_number=1"),
            _gff3_row("chr1", "exon", 300, "Parent=TX1;gene=REAL1;exon_number=2"),
            # CAT shape: everything explicit, plus a normalization record.
            _gff3_row("chrM", "gene", 10, "gene_id=G2;gene_name=MT-ND1;gene_biotype=protein_coding"),
            _gff3_row("chrM", "transcript", 10, "gene_id=G2;transcript_id=T2;gene_name=MT-ND1"),
            _gff3_row("chrM", "exon", 10,
                      "gene_id=G2;transcript_id=T2;gene_name=MT-ND1;original_gene_name=MSTRG.5"),
        ]
        _write(build / builder.MERGED_GFF3, _annotation(rows))
        with _fake_build_dir(build):
            assert builder.cmd_gff3_to_gtf(argparse.Namespace()) == 0

        gtf = build / builder.MERGED_GTF
        exons = [f for f in builder.iter_gff_rows(gtf) if f[2] == "exon"]
        assert len(exons) == 3, exons
        for fields in exons:
            attrs = builder.parse_attributes(fields[8])
            assert attrs.get("gene_id"), fields
            assert attrs.get("gene_name"), fields
            assert attrs.get("transcript_id"), fields
        # The RefSeq exon, which had no gene_id at all, got one from the chain.
        chr1_exon = builder.parse_attributes(exons[0][8])
        assert chr1_exon["gene_id"] == "GENE1"
        assert chr1_exon["gene_name"] == "REAL1"
        assert chr1_exon["transcript_id"] == "TX1"
        # original_gene_name survives into the GTF where normalization set it.
        assert "original_gene_name" in gtf.read_text(encoding="utf-8")


def test_gff3_to_gtf_fails_closed_on_an_exon_it_cannot_resolve():
    import argparse

    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        rows = [
            _gff3_row("chr1", "gene", 100, "ID=GENE1;gene_name=REAL1;gene_biotype=protein_coding"),
            # Parent names a transcript that no transcript row declares.
            _gff3_row("chr1", "exon", 100, "Parent=TX_MISSING;gene=REAL1"),
        ]
        _write(build / builder.MERGED_GFF3, _annotation(rows))
        with _fake_build_dir(build):
            assert builder.cmd_gff3_to_gtf(argparse.Namespace()) != 0, (
                "an exon that cannot be attributed to a gene must fail the step, "
                "not be dropped silently"
            )


def test_merge_chrm_refuses_when_the_primary_already_annotates_chrm():
    # Appending would duplicate every mitochondrial gene. The guarantee of no
    # duplicates is checked, not assumed.
    import argparse

    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        _write(build / "chm13v2.0_maskedY.fa.gz.tmp", "")  # placeholder, replaced below
        import gzip as _gzip

        with _gzip.open(build / "chm13v2.0_maskedY.fa.gz", "wt", encoding="utf-8") as fh:
            fh.write(_fasta({"chr1": 500, "chrM": 16569}))
        with _gzip.open(build / "chm13v2.0_RefSeq_Liftoff_v5.3.gff.gz", "wt", encoding="utf-8") as fh:
            fh.write(_annotation([
                _gff3_row("chr1", "gene", 100, "ID=GENE1;gene_name=REAL1"),
                _gff3_row("chrM", "gene", 10, "ID=GENEM;gene_name=MT-ND1"),  # <- the problem
            ]))
        _write(build / "normalized.cand.gff3",
               _annotation([_gff3_row("chrM", "gene", 10, "gene_id=G2;gene_name=MT-ND1")]))

        provenance = builder.Provenance.load_or_create(build)
        provenance.record_step("chrm_candidate_selected", label="cand", file="cand.gff3",
                               contig_name_used="chrM", canonical_genes_found=13)

        with _fake_build_dir(build):
            assert builder.cmd_merge_chrm(argparse.Namespace()) != 0
        assert not (build / builder.MERGED_GFF3).exists(), "must not write a merged file"


def test_merge_chrm_uses_the_normalized_candidate_not_the_raw_download():
    import argparse
    import gzip as _gzip

    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        with _gzip.open(build / "chm13v2.0_maskedY.fa.gz", "wt", encoding="utf-8") as fh:
            fh.write(_fasta({"chr1": 500, "chrM": 16569}))
        with _gzip.open(build / "chm13v2.0_RefSeq_Liftoff_v5.3.gff.gz", "wt", encoding="utf-8") as fh:
            fh.write(_annotation([_gff3_row("chr1", "gene", 100, "ID=GENE1;gene_name=REAL1")]))
        # Only the RAW candidate exists — normalization was never run.
        _write(build / "cand.gff3",
               _annotation([_gff3_row("chrM", "gene", 10, "gene_id=G2;gene_name=MSTRG.5")]))

        provenance = builder.Provenance.load_or_create(build)
        provenance.record_step("chrm_candidate_selected", label="cand", file="cand.gff3",
                               contig_name_used="chrM", canonical_genes_found=13)

        with _fake_build_dir(build):
            try:
                builder.cmd_merge_chrm(argparse.Namespace())
            except SystemExit:
                pass
            else:
                raise AssertionError("must refuse to merge from the un-normalized candidate")


def test_mkgtf_attribute_list_keeps_the_mitochondrial_biotypes():
    # The chrM rows come from CAT/Liftoff, which spells the rRNAs and tRNAs
    # Mt_rRNA / Mt_tRNA. Leaving them out of the filter would drop MT-RNR1 and
    # MT-RNR2 and quietly understate pct_counts_mt.
    attrs = set(builder.REFSEQ_MKGTF_ATTRIBUTES)
    assert "gene_biotype:Mt_rRNA" in attrs
    assert "gene_biotype:Mt_tRNA" in attrs
    # And it must still be RefSeq's vocabulary, not GENCODE's, for the bulk.
    assert "gene_biotype:V_segment" in attrs and "gene_biotype:C_region" in attrs
    assert not any(a.endswith(":IG_V_gene") or a.endswith(":TR_V_gene") for a in attrs)


def test_placeholder_pattern_is_anchored_not_a_substring_match():
    # A real symbol that merely contains the letters MSTRG must never be
    # treated as a placeholder and silently overwritten.
    assert builder.is_placeholder_gene_name("MSTRG.9")
    assert builder.is_placeholder_gene_name("MSTRG.12.3")
    assert not builder.is_placeholder_gene_name("MSTRG")
    assert not builder.is_placeholder_gene_name("NOTMSTRG.9")
    assert not builder.is_placeholder_gene_name("MSTRG.9x")
    assert not builder.is_placeholder_gene_name("CCNL2")


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
