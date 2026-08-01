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

T2T-CHM13v2.0 with the JHU RefSeq/Liftoff v5.3 annotation, 39,048 genes.
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

The build script and its provenance record live with the reference itself.

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
