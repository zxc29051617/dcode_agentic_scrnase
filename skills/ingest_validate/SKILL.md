---
name: ingest_validate
description: Identify whether the input bundle is FASTQ, raw matrix, filtered matrix, or h5ad and normalize the state for routing.
version: 0.2.0
status: implemented
---

# ingest_validate

## Purpose
First tool the orchestrator calls. Classifies an input bundle from the filesystem
so the graph knows which route to take, and refuses anything it cannot resolve.

## Input
`run(payload)` where payload contains:

| key | meaning |
|---|---|
| `input_bundle` | `{"paths": [...]}` — directories or files. A bare string also works. |
| `config` | `sample_qc_triage: bool`, `qc_metrics_csv: str` (both optional) |
| `sample_metadata` | optional; only used to decide whether triage should run first |

## Output

| key | meaning |
|---|---|
| `input_type` | `fastq` \| `matrix` \| `unknown` — what `branch_input_type` routes on |
| `artifact_kind` | `fastq` \| `mtx_dir` \| `tenx_h5` \| `h5ad` |
| `matrix_kind_hint` | `raw` \| `filtered` \| `unknown` — a hint for `count_matrix_classify`, not the decision |
| `needs_cell_calling` | `True` for raw, `False` for filtered, `None` when undetermined |
| `needs_upstream_preprocessing` | `True` only for FASTQ |
| `matrix_path` | the artifact chosen for the downstream route |
| `sample_ids` | samples parsed from the FASTQ names |
| `fastq_layout` | `{sample: {lanes, reads, n_files}}` — consumed by `fastq_preflight` and `cellranger_count` |
| `detected` | every recognized artifact with its own evidence |
| `recommended_next_tool` | `sample_qc_triage` \| `fastq_preflight` \| `count_matrix_classify` |
| `metrics` | counts surfaced to the judge |
| `warnings`, `errors` | judged by `judge_ingest`; errors stop the run at the human gate |

## Behavior
- Detects from the filesystem only; never reads a count matrix.
- Recognizes 10x MTX triplets (plain or `.gz`), `.h5`, `.h5ad`, and Illumina-named FASTQ.
- Reads raw vs filtered from the Cell Ranger naming convention. When a bundle holds
  both (a normal `outs/`), it routes on filtered and says the raw matrix is still there.
- For h5ad it reads `n_obs`/`n_vars` through h5py without loading the matrix, and
  infers `raw` only above 100k barcodes. A filename that states the kind wins over the count.
- Returns `unknown` rather than guessing when the layout gives no raw/filtered signal.

## Failure modes
Each returns a populated `errors` list, which `judge_ingest` turns into `fail`:

- input path missing or `input_bundle` has no path
- mixed assay types in one bundle (FASTQ and matrix together)
- unsupported file type, or a directory with nothing recognizable in it

## Downstream routing
`sample_qc_triage` (when enabled and metadata exists), otherwise `fastq_preflight`
for FASTQ or `count_matrix_classify` for matrices.

## Standalone

```bash
python skills/ingest_validate/ingest_validate.py <path>...
```

## Verified against
`pbmc_1k_v3` (10x official 3' v3 test set): 1 sample, 2 lanes, I1/R1/R2, no warnings.
