# reference/

Cell Ranger references live here. **Only this directory ever points outside the
project** — every path in code and config is `reference/<dirname>`, so moving the
bytes (another disk, another machine, a real copy instead of a link) never
touches Python.

The contents are gitignored; a reference is 20–32 GB. What is committed is how
to get one.

## Put a reference here

```bash
bash scripts/link_reference.sh                       # list registered species
bash scripts/link_reference.sh human /path/to/ref    # symlink (default)
bash scripts/link_reference.sh human --copy /path/to/ref
```

The script refuses a directory with no `reference.json`, and prints which
species the reference says it is so a wrong link is caught immediately.

## Registered species

The table is `src/species.py` — the script asks it rather than keeping its own
copy of the directory names.

| species | directory | how |
|---|---|---|
| human | `T2T_CHM13v2_RefSeqLiftoff_v5_3` | built in-house (see below) |
| mouse | `refdata-gex-GRCm39-2024-A` | 10x prebuilt tarball |

Anything else runs, but needs `reference.transcriptome` set explicitly and its
own QC gene lists in config — see "another species" below.

## The human reference

T2T-CHM13v2.0 with the JHU RefSeq/Liftoff v5.3 annotation plus a merged chrM,
43,258 genes after `mkgtf` filtering. (The reference this project shipped with
before the rebuild had 39,048; the difference is chrM, the two mitochondrial
biotypes named below, and a filter list that no longer drops them.)
Four things had to be fixed before Cell Ranger would take it, and all four
matter:

1. **v5.3 annotates 24 contigs and chrM is not one of them.** The 13 canonical
   mitochondrial genes are merged in from the 2022 CAT/Liftoff GFF3, which is on
   the same coordinates. Without this, `pct_counts_mt` is always 0 and the
   mitochondrial QC filter silently does nothing.
2. **The FASTA must be `chm13v2.0_maskedY.fa`, not the `_rCRS` variant.** The
   chrM annotation above is on CHM13's own mitochondrion; rCRS is a different
   sequence, so the coordinates would be wrong without erroring.
   `maskedY` also hard-masks chrY's PARs — those are identical to chrX's copy,
   and leaving both unmasked turns every PAR read into a discarded multi-mapper.
3. **The GFF3's exon rows carry no `gene_id`.** They have to be propagated down
   the `ID`/`Parent` chain, since Cell Ranger builds gene models from exons.
4. **RefSeq's biotype vocabulary is not GENCODE's** — immune receptor segments
   are `V_segment` / `J_segment` / `C_region`, not `IG_V_gene` / `TR_V_gene`,
   so a GENCODE-shaped `mkgtf` filter drops them all.

The build script and its provenance record live with the reference itself —
**that directory (`claude_agentic_scrna`) was later deleted, and both went
with it.** What survived was this description. `scripts/build_t2t_chm13_reference.py`
and `scripts/validate_t2t_chm13_reference.py` exist so that does not happen
again: every source is a URL plus a checksum (published, or computed and
recorded rather than left implicit), every step is a named subcommand, and a
rebuilt reference carries its own `BUILD_PROVENANCE.json` inside
`reference/T2T_CHM13v2_RefSeqLiftoff_v5_3/` rather than depending on a
directory elsewhere continuing to exist.

```bash
# prints the full plan — every source, size and checksum — and touches
# nothing. Safe to run any time.
python scripts/build_t2t_chm13_reference.py plan

# each step needs its own explicit flag before it downloads or builds anything
python scripts/build_t2t_chm13_reference.py fetch-fasta --i-confirm-download
python scripts/build_t2t_chm13_reference.py fetch-primary-annotation --i-confirm-download
python scripts/build_t2t_chm13_reference.py compare-chrm-candidates --i-confirm-download

# after a built reference exists:
python scripts/validate_t2t_chm13_reference.py reference/T2T_CHM13v2_RefSeqLiftoff_v5_3
```

**Where chrM comes from is not hardcoded.** Two CAT/Liftoff candidates exist
upstream and neither is assumed correct; `compare-chrm-candidates` downloads
both, detects the mitochondrial contig from the FASTA itself (by sequence
length agreeing with a name that looks mitochondrial — never by assuming a
name like "chrM"), counts how many of the 13 canonical mitochondrial
protein-coding genes each candidate annotates on that contig, and **fails
closed** — exits non-zero and prints the comparison — unless exactly one
candidate is the unambiguous answer. The rest of the build will not proceed
past that point until a person has looked at the comparison.

Every subcommand has now been run against the real sources, end to end, and
the reference in this directory is what came out. What the build actually
found, recorded in `_build_T2T_CHM13v2_v5_3/BUILD_PROVENANCE.json`:

- **The FASTA already carries chrM.** `compare-chrm-candidates` detected the
  mitochondrial contig from the sequence itself and found it named `chrM`.
  What v5.3 is missing is the chrM *annotation*, not the sequence — a
  different problem, with a different fix, than splicing in a sequence.
- **Both chrM candidates tied**, each annotating all 13 canonical genes, so
  the comparison failed closed rather than picking one. The whole-genome
  CAT+Liftoff candidate was then chosen by hand and recorded with
  `select-chrm-candidate`, which refuses to record a choice the schema audit
  has not cleared.
- **1,384 `gene_id`s carried two `gene_name`s**, identically in both
  candidates — StringTie's `MSTRG.<n>` placeholder surviving on
  novel-isoform transcripts merged into an existing locus. Classified as
  1,384 resolvable (one real symbol beside a placeholder), 0 unresolvable,
  and 48 placeholder-only; `normalize-annotations` rewrote 1,384 gene_ids
  across 99,963 rows and kept each displaced name as `original_gene_name`.
- **The RefSeq primary annotates 24 contigs and none is chrM**, checked
  before merging rather than assumed, so appending the candidate's 156 chrM
  rows could not duplicate anything.
- **`mkgtf` needed `Mt_rRNA` and `Mt_tRNA` on top of RefSeq's vocabulary.**
  The chrM rows come from CAT/Liftoff, which spells them that way; without
  those two, the 13 protein-coding genes would survive the filter and
  MT-RNR1/MT-RNR2 would not — understating `pct_counts_mt` rather than
  erroring, since `src/species.py` derives it from the `MT-` name prefix.

The resulting reference: 43,258 genes, 179,065 transcripts, 2,079,240 exons,
25 contigs, chrM 13/13. Built by `cellranger mkref` 10.1.0 in 37.8 minutes.
`validate_t2t_chm13_reference.py` passes 8/8 against it.

## Another species

Two files, and their versions must match:

| | format | where |
|---|---|---|
| genome sequence | FASTA (`.fa` / `.fa.gz`) | Ensembl / NCBI / UCSC |
| gene annotation | GTF (`.gtf` / `.gtf.gz`) | same source, same assembly |

Then `cellranger mkgtf` to filter biotypes and `cellranger mkref` to build.
No Python changes — `reference.transcriptome` is a config value.

**The GTF has to satisfy seven things.** The first four are 10x's; the last three
are what the T2T build taught us, and each fails silently rather than loudly:

1. a third column with `exon` features — Cell Ranger assigns UMIs by exon
2. `gene_id` in the attributes
3. **`gene_name` in the attributes** — without it every downstream marker is
   `ENSMUSG...` and cell annotation is worthless
4. contig names identical to the FASTA (`chr1` vs `1` is the usual mistake)
5. **chrM actually annotated** — liftovers and non-model assemblies drop it, and
   mitochondrial QC then measures nothing instead of failing
6. **biotype vocabulary matched to the source** — Ensembl, RefSeq and GENCODE
   use different names, and `mkgtf --attribute` has to use the right ones
7. **unique `gene_id`s** — liftover tools emit duplicate copies (`LOC124905335`
   and `LOC124905335_1`); `mkref` will not tell you which one a count came from

**Cost**: roughly 30 GB of disk per species, and a large-memory machine to build
(the human build used 128 GB / 16 threads).

10x ships prebuilt human, mouse, and a human–mouse barnyard reference for PDX.
Rat, pig, macaque and zebrafish have to be built. Non-model organisms without
good annotation are the genuinely hard case.

## GEX only

These are `cellranger mkref` references for 3' gene expression. Multiome
(ATAC + GEX) needs `cellranger-arc mkref`, a different config-file format, and
the two are **not interchangeable**.
