# scRNA-seq tool registry

這份 registry 的目的，是把 pipeline 的每個 meaningful step 拆成獨立的 **tool / skill**，讓 workflow orchestrator 只負責路由、checkpoint、resume 與 provenance。

> **這份文件描述已實作的架構。** 早期版本寫的是一個 MCP-first、每個 step
> 各配一個 `judge_*` 工具的設計；實作時兩者都改了，而文件一度沒跟上，留下
> 19 個從未被呼叫的 `judge_*` 資料夾。那些資料夾已刪除，這裡記錄實際的做法。

## Design rules

- **One step, one tool**：每個明確分析步驟都是一個工具。
- **Judge is separate**：分析工具與評分工具分開。
- **Human gate is explicit**：warn/fail 不能暗中放行。
- **MVP inputs**：先只支援 `FASTQ` 與 `Count matrix`。
- **Count matrix split**：分成 `raw` 與 `filtered` 兩種路徑。
- **Direct import, not MCP**：orchestrator 用 `src/registry.py` 的
  `call_skill(name, payload)` 直接 import `skills/<name>/<name>.py` 並呼叫它的
  `run(payload) -> dict`。沒有 MCP server、沒有 RPC、沒有 subprocess。契約是
  函式簽名本身，這也是每個 skill 都能獨立當 CLI 跑的原因。
- **One shared judge contract**：judge 不是每個 step 一個工具。`src/judge.py`
  定義一份 `JudgeResult` 契約與一個 backend（`StubJudge` 或 `LocalLLMJudge`），
  每個 step 傳不同 payload 進去。registry 的 `judge` 欄位是 **graph 裡的 node
  名稱與 audit log 標籤**，不是模組路徑。
- **Revisable is an allowlist**：`StepSpec.revisable` 列出這個 step 的 gate
  允許人在 `revise` 時設定的 config key，其他一律拒絕並附理由。gate 是 run 開始
  之後唯一能寫進 `config` 的地方，所以它必須是白名單而不是「任何 key」。

## Revisable parameters

`revise` 如果不能改任何東西，就只是用同一份 config 重跑同一個 deterministic
step——同樣的結果、同樣的 verdict、同樣的問題。所以 gate 的答案可以帶
`overrides`，只接受下表的 key：

| step | 可改的參數 |
|---|---|
| `cell_calling_review` | `force_cells`、`min_umi` |
| `apply_cell_qc_filter` | `min_genes`、`min_counts`、`max_pct_mito` |
| `annotate_cells` | `celltypist_model` |
| `cross_check_annotation` | `scmayomap_tissue` |

這四個正好是**「沒有人決定就不自己猜」**的四個 step——它們本來就會停下來把候選
列成 evidence，所以 gate 就是那個答案該進來的地方。每個名稱都已經是一個
documented CLI flag，值從命令列來或從 gate 來走同一條路。

`human_review_decision`（主線 gate）不同：它的 `revise` 是回到
`annotate_cells`，所以它開放的是**從那裡之後會重跑的所有參數**的聯集
（`celltypist_model` + `scmayomap_tissue`），而不是它剛剛評的那一個 step 的。

改了參數之後會發生三件事，缺一不可：

1. 值寫進 `config`（`merge_dicts` reducer，只加不覆蓋整份）
2. 從 revise target 之後的每個 step 的 resume flag 都被清掉，**續跑時不能沿用**
3. `run_metadata.json` 追加一筆 `revisions`，並且**改寫 `source.config_sha256`**
   ——否則之後用原本的命令列 `--resume-from`，hash 會對得上，然後沿用那些已經
   被取代掉的 artifact

`GatePolicy.max_revisions_per_step`（預設 10）是防跑掉用的：`recursion_limit`
擋不住 revise 迴圈，因為它是 per-`invoke` 計數，而每次 `Command(resume=...)`
都會重新開始。超過就記成 `stop` 並寫明原因，不會變成默默 `accept`。

## Registry overview

| Stage | Tool name | Type | Input | Output | Judge node | Next step |
|---|---|---|---|---|---|---|
| ingest | `ingest_validate` | utility | input bundle | normalized state + detected input type | `judge_ingest` | route |
| sample QC | `sample_qc_triage` | optional pre-route | QC metrics CSV | sample flags + summary | `judge_sample_qc` | FASTQ or matrix route |
| reference | `resolve_reference` | utility | species or explicit path | transcriptome path + QC constants + species check | `judge_reference` | `fastq_preflight` |
| matrix preflight | `matrix_preflight` | utility | count matrix | readability + species + orientation + gene id convention | `judge_matrix_preflight` | `count_matrix_classify` |
| FASTQ preflight | `fastq_preflight` | upstream | FASTQ bundle + samplesheet + references + chemistry config | preflight report + readiness | `judge_fastq_preflight` | `cellranger_count` |
| cellranger count | `cellranger_count` | upstream | FASTQ bundle + references + samplesheet + chemistry config | BAM + raw_feature_bc_matrix + filtered_feature_bc_matrix + run metrics + optional preferred_h5ad | `judge_cellranger_count` | count matrix route |
| sequencing QC | `fastq_qc` | upstream | FASTQ bundle | FastQC + MultiQC report + per-read metrics | `judge_fastq_qc` | `cellranger_count` |
| matrix classify | `count_matrix_classify` | router | count matrix / h5ad | `raw` / `filtered` / `unknown` | `judge_matrix_classify` | raw or filtered route |
| raw matrix load | `load_raw_counts` | analysis | raw matrix / raw-count h5ad | AnnData + source state | `judge_raw_counts` | cell calling review or mainline |
| filtered matrix load | `load_filtered_counts` | analysis | filtered matrix / filtered-count h5ad | AnnData + source state | `judge_filtered_counts` | mainline |
| cell calling review | `cell_calling_review` | analysis | raw matrix summary | cell calling decision payload | `judge_cell_calling` | mainline / human gate |
| merge | `merge_samples` | analysis | one AnnData per library | one AnnData with a `sample` column | `judge_merge` | `post_load_validate` |
| standardize | `post_load_validate` | analysis | merged AnnData | one shape for the mainline + raw counts layer | `judge_post_load` | `run_qc_metrics` |
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
- **Called first**: the orchestrator's entry node, before any route is chosen.
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

## Naming convention

- analysis tools: `run_*`, `apply_*`, `load_*`, `detect_*`, `annotate_*`, `find_*`
- routing tools: `ingest_validate`, `count_matrix_classify`
- gates: `human_review_decision`（graph 裡另有一個 `human_gate` escalation node，
  它不是 registry step）
- report: `build_report`

判官沒有命名慣例，因為沒有判官工具——`REGISTRY[step].judge` 是 graph node 的
名稱（`judge_qc` 之類），由 `make_judge_node()` 建立，不對應任何檔案。

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
15. `build_report`

## Notes

- The orchestrator does not know implementation details of any tool; it needs
  the registry only to know **what to call next**.
- A step is added by writing `skills/<name>/<name>.py` with a
  `run(payload) -> dict` and one `StepSpec` in `src/registry.py`. `graph.py`
  does not change — the wiring is generated from the registry.
- Because the call is a plain import, every step is also a standalone CLI:
  `python skills/<name>/<name>.py --help`. All 25 work.
