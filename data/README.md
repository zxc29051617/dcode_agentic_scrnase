# data/

Test and demo datasets. **Nothing here is in the repository** — it comes to
around 27 GB, and git is the wrong place for it.

Every test that needs a dataset skips when it is absent, so a fresh clone runs
green without any of this. It just tests less, and the skip messages say which.

## Get it

```bash
bash scripts/get_test_data.sh            # what is needed, and what is present
bash scripts/get_test_data.sh fastq      # 18 GB — the FASTQ route
bash scripts/get_test_data.sh matrices   # 54 MB — third-party count matrices
```

## Layout

```
data/
  pbmc_1k_v3/pbmc_1k_v3_fastqs/      human 3' v3   — the reference end-to-end run
  pbmc_1k_v2/pbmc_1k_v2_fastqs/      human 3' v2   — the chemistry check
  neuron_1k_v3/neuron_1k_v3_fastqs/  mouse 3' v3   — the non-human end-to-end run
  counted/<dataset>/outs/            Cell Ranger output, produced not downloaded
  10x_public/*.h5                    optional third-party matrices
```

`counted/` is what a real run produces. It is kept because the matrix-route
tests need a raw and a filtered matrix from the same library, and generating
them takes twenty minutes:

```bash
python skills/cellranger_count/cellranger_count.py \
  --fastqs data/pbmc_1k_v3/pbmc_1k_v3_fastqs --sample pbmc_1k_v3 \
  --transcriptome reference/T2T_CHM13v2_RefSeqLiftoff_v5_3 \
  --run-dir data/counted --localcores 32 --localmem 128
```

## Why the FASTQ sets are the ones that matter

Each covers something the others do not:

| | what it exercises |
|---|---|
| `pbmc_1k_v3` | the baseline; 1,218 cells against 10x's published 1,222 |
| `pbmc_1k_v2` | **R1 is 28bp on a v2 library** — the case that broke reading chemistry off read length |
| `neuron_1k_v3` | mouse: a second reference, a second species, and the cross-species guard |

Running any of them produces both a raw and a filtered matrix, which is what
the matrix route needs — so the FASTQ sets cover the matrix tests too.

## Optional: third-party matrices

`10x_public/` holds count matrices this pipeline did **not** produce, which is
the point of them: they catch assumptions that only hold for our own output.
One is worth calling out — `pbmc_1k_protein_v3` is a CITE-seq library whose 17
Antibody Capture features scanpy drops silently, and that is how the
feature-type reporting in `matrix_preflight` came to exist.

## Your own data

Put it wherever you like and pass `--input`. Nothing here is special-cased;
`ingest_validate` detects FASTQ, MTX, `.h5` and `.h5ad` from the filesystem.
