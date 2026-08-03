---
name: matrix_preflight
description: The matrix route's entry check — format, gene ID convention, species, and orientation, before anything downstream trusts the file.
version: 0.1.0
status: implemented
---

# matrix_preflight

## Purpose
The counterpart to `fastq_preflight` on the other route. A FASTQ run learns its
species from the reference it has to resolve anyway; a run that arrives holding
a count matrix never touches a reference, so until this step existed nothing on
that side asked the equivalent questions.

| | asks |
|---|---|
| format | can the file be read, and as what |
| gene ids | which naming convention, and are stable ids present at all |
| species | what organism the matrix actually contains |
| orientation | cells × genes, or transposed |

## Species, from whatever evidence the file carries
Strongest first:

1. **a recorded genome** — only a 10x `.h5` has one, in `var['genome']`
2. **Ensembl stable ids** — `ENSG` against `ENSMUSG`
3. **symbol casing** — `CD3E` against `Cd3e`

The third is a convention rather than a guarantee and is labelled as such. It
still earns its place: the T2T RefSeq annotation names genes `LOC124900618`,
which defeats the first two, and that is exactly what an mtx directory presents.

Stable ids are excluded from the casing test — `ENSMUSG00000001` is uppercase
because it is an id, not because the organism is human.

| situation | treatment |
|---|---|
| evidence agrees with the declared species | verified |
| evidence contradicts it | **error** |
| no usable evidence | note |
| two species (barnyard/PDX) | note |
| evidence available, no species declared | warning — actionable |

## Orientation
A transposed matrix — genes as rows — is an **error**, not something to silently
fix: `.T` on the wrong object gives a plausible-looking result and there is no
way to tell afterwards. Detected by 10x barcodes appearing in `var_names`.

## Shared constants
Emits the same `mito_prefix`, `erythroid_genes` and `marker_db` that
`resolve_reference` emits on the FASTQ side, both from
`species.constants_for`, so the mainline reads one shape whichever way the run
came in.

## Downstream routing
`count_matrix_classify`.

## Non-gene-expression features are reported, not silently dropped
`scanpy.read_10x_h5` defaults to `gex_only=True` and discards Antibody Capture,
CRISPR Guide Capture and the rest without a word. Dropping them is right for
this pipeline; doing it quietly is not — a CITE-seq library would lose its
antibody panel between the file and the AnnData with no trace.

That one is a **warning**, unlike the missing-gene-ids note: there is a decision
here. Either the extra modality was not wanted, or this is the wrong pipeline.

## Verified against
The in-house `pbmc_1k_v3` output in both formats, plus five of 10x's own public
matrices — third-party files this pipeline did not produce, which is what makes
them worth running:

| dataset | cells | what it exercises | verdict |
|---|---|---|---|
| `pbmc_1k_v3` mtx dir | 1,218 | no genome, `LOC...` ids → symbol casing | human |
| `pbmc_1k_v3` `.h5` | 1,218 | recorded genome `T2T_CHM13v2_RefSeqLiftoff_v5_3` | human |
| `pbmc_1k_v2` | 996 | 3' v2 chemistry, GRCh38 | human |
| `pbmc_10k_v3` | 11,769 | ten times the cells | human |
| `PBMC_LT_ChromiumX` | 587 | low-throughput kit on Chromium X | human |
| `pbmc_1k_protein_v3` | 713 | CITE-seq — 17 Antibody Capture features flagged | human |
| `neuron_1k_v3` | 1,301 | **mouse** (mm10), and blocked when declared human | mouse |
