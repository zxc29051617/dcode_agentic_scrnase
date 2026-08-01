# scRNA-seq tool registry v1

這份 registry 的目的，是把 pipeline 的每個 meaningful step 拆成獨立的 **tool / skill**，讓 workflow orchestrator 只負責路由、checkpoint、resume 與 provenance。

## Design rules

- **One step, one tool**：每個明確分析步驟都是一個工具。
- **Judge is separate**：分析工具與評分工具分開。
- **Human gate is explicit**：warn/fail 不能暗中放行。
- **MVP inputs**：先只支援 `FASTQ` 與 `Count matrix`。
- **Count matrix split**：分成 `raw` 與 `filtered` 兩種路徑。
- **MCP-first**：workflow 透過 MCP 呼叫工具；LangGraph 只做 orchestration。

## Registry overview

| Stage | Tool name | Type | Input | Output | Judge tool | Next step |
|---|---|---|---|---|---|---|
| ingest | `ingest_validate` | utility | input bundle | normalized state + detected input type | `judge_ingest` | route |
| sample QC | `sample_qc_triage` | optional pre-route | QC metrics CSV | sample flags + summary | `judge_sample_qc` | FASTQ or matrix route |
| FASTQ preflight | `fastq_preflight` | upstream | FASTQ bundle + samplesheet + references + chemistry config | preflight report + readiness | `judge_fastq_preflight` | `cellranger_count` |
| cellranger count | `cellranger_count` | upstream | FASTQ bundle + references + samplesheet + chemistry config | BAM + raw_feature_bc_matrix + filtered_feature_bc_matrix + run metrics + optional preferred_h5ad | `judge_cellranger_count` | count matrix route |
| matrix classify | `count_matrix_classify` | router | count matrix / h5ad | `raw` / `filtered` / `unknown` | `judge_matrix_classify` | raw or filtered route |
| raw matrix load | `load_raw_counts` | analysis | raw matrix / raw-count h5ad | AnnData + source state | `judge_raw_counts` | cell calling review or mainline |
| filtered matrix load | `load_filtered_counts` | analysis | filtered matrix / filtered-count h5ad | AnnData + source state | `judge_filtered_counts` | mainline |
| cell calling review | `cell_calling_review` | analysis | raw matrix summary | cell calling decision payload | `judge_cell_calling` | mainline / human gate |
| QC | `run_qc_metrics` | analysis | AnnData | QC metrics table | `judge_qc` | cell QC filter |
| cell QC filter | `apply_cell_qc_filter` | analysis | AnnData + thresholds | filtered AnnData | `judge_cell_qc_filter` | doublet detection |
| doublet detection | `detect_doublets` | analysis | AnnData | doublet calls + filtered AnnData | `judge_doublets` | preprocess |
| preprocess | `normalize_hvg_prepare` | analysis | AnnData | normalized AnnData + HVGs | `judge_preprocess` | PCA |
| PCA | `run_pca` | analysis | AnnData | PCA embedding + loadings | `judge_pca` | integration / clustering |
| integration | `run_integration` | analysis | AnnData | integrated embedding | `judge_integration` | clustering |
| clustering | `run_clustering` | analysis | AnnData | cluster labels | `judge_clustering` | UMAP |
| UMAP | `run_umap` | analysis | AnnData | UMAP coordinates | `judge_umap` | markers |
| markers | `find_markers` | analysis | AnnData + cluster labels | marker table | `judge_markers` | annotation |
| annotation | `annotate_cells` | analysis | marker table + evidence | labels + confidence | `judge_annotation` | human review |
| human review | `human_review_decision` | gate | judge payload + candidate labels | accept / revise / stop | n/a | report or reroute |
| report | `build_report` | utility | final state + artifacts | HTML / PDF / JSON summary | `judge_report` optional | done |

## Tool groups

### 1. Ingestion and routing tools

#### `ingest_validate`
- **Purpose**: identify whether the input is FASTQ, raw matrix, filtered matrix, or h5ad.
- **MCP role**: first tool called by the orchestrator.
- **Important outputs**:
  - `input_type`
  - `artifact_kind`
  - `needs_cell_calling`
  - `needs_upstream_preprocessing`

#### `count_matrix_classify`
- **Purpose**: classify matrix-like input into `raw`, `filtered`, or `unknown`.
- **Why separate**: raw and filtered matrices imply different biological assumptions.

#### `sample_qc_triage`
- **Purpose**: optional pre-route sample-level QC outlier detection.
- **Borrowed from**: ClawBio `sample-qc-triage`.

### 2. FASTQ upstream tools

#### `fastq_preflight`
- **Purpose**: validate FASTQ structure, sample sheet, references, and run readiness.
- **Borrowed from**: ClawBio `nfcore-scrnaseq-wrapper`.

#### `cellranger_count`
- **Purpose**: run Cell Ranger count on validated FASTQ and produce BAM, raw matrix, filtered matrix, and run metrics.
- **Scope**: upstream alignment/counting only; no clustering or annotation.
- **Borrowed from**: ClawBio `nfcore-scrnaseq-wrapper`.

### 3. Count-matrix analysis tools

#### `load_raw_counts`
- **Purpose**: load raw matrix or raw-count h5ad into AnnData.
- **Special rule**: if cell calling is not resolved, force review before downstream analysis.

#### `load_filtered_counts`
- **Purpose**: load filtered matrix or filtered-count h5ad into AnnData.
- **Special rule**: assumed post-cell-calling unless evidence says otherwise.

#### `cell_calling_review`
- **Purpose**: decide whether the raw matrix already reflects cell calling, or whether a review is needed.
- **Note**: this is a biological/operational judgment, so the human gate must stay explicit.

#### `run_qc_metrics`
- **Purpose**: compute QC stats from AnnData.
- **Judge focus**: low-quality burden, mitochondrial fraction, counts/gene distribution.

#### `apply_cell_qc_filter`
- **Purpose**: apply QC thresholds and produce filtered AnnData.

#### `detect_doublets`
- **Purpose**: run doublet detection and tag/remove cells.

#### `normalize_hvg_prepare`
- **Purpose**: normalization, log transform, HVG selection, and PCA-ready matrix prep.

#### `run_pca`
- **Purpose**: compute PCA embedding and loadings.

#### `run_integration`
- **Purpose**: batch correction or latent integration when multiple batches exist.
- **Later extension**: may delegate to `scrna-embedding` style scVI branch.

#### `run_clustering`
- **Purpose**: Leiden or equivalent clustering.

#### `run_umap`
- **Purpose**: UMAP visualization.

#### `find_markers`
- **Purpose**: cluster marker discovery.

#### `annotate_cells`
- **Purpose**: assign putative cell labels using markers / reference evidence.

### 4. Judge tools

Each stage gets its own judge tool:

- `judge_ingest`
- `judge_sample_qc`
- `judge_fastq_preflight`
- `judge_cellranger_count`
- `judge_matrix_classify`
- `judge_raw_counts`
- `judge_filtered_counts`
- `judge_cell_calling`
- `judge_qc`
- `judge_cell_qc_filter`
- `judge_doublets`
- `judge_preprocess`
- `judge_pca`
- `judge_integration`
- `judge_clustering`
- `judge_umap`
- `judge_markers`
- `judge_annotation`
- `judge_report` (optional)

### Judge contract

Every judge tool should return the same JSON shape:

```json
{
  "step": "run_qc_metrics",
  "verdict": "pass",
  "score": 84,
  "reasons": ["mitochondrial burden is acceptable"],
  "evidence": {
    "cells_before": 12000,
    "cells_after": 11450,
    "median_mito": 0.041
  },
  "suggested_action": "continue",
  "needs_human_review": false
}
```

## MCP tool naming convention

Recommended naming pattern:

- analysis tools: `run_*`, `apply_*`, `load_*`, `detect_*`, `annotate_*`, `find_*`
- routing tools: `ingest_validate`, `count_matrix_classify`
- judges: `judge_*`
- gates: `human_review_decision`
- report: `build_report`

## Skill packaging direction

If later turned into ClawBio-style skills, the same logical split can be used:

- one folder per tool
- one `SKILL.md` per tool family
- one deterministic Python implementation per step
- one JSON schema per tool output
- one shared workflow orchestrator on top

## MVP implementation order

1. `ingest_validate`
2. `count_matrix_classify`
3. `fastq_preflight`
4. `cellranger_count`
5. `load_raw_counts` / `load_filtered_counts`
6. `run_qc_metrics`
7. `apply_cell_qc_filter`
8. `detect_doublets`
9. `normalize_hvg_prepare`
10. `run_pca`
11. `run_clustering`
12. `find_markers`
13. `annotate_cells`
14. `judge_*` tools for each step
15. `build_report`

## Notes

- This registry keeps the workflow modular enough for MCP, LangChain, or LangGraph.
- The orchestrator should not know implementation details of each tool.
- The orchestrator only needs the registry to know **what to call next**.
