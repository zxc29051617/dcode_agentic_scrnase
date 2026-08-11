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
- **`config_keys` is a superset, on purpose**：`StepSpec.config_keys` 列出每個
  step「值變了就可能產出不同結果」的 config key，`--resume-from` 用它算出
  **最早不能再信任的 step**。多列一個只是白跑一次；**少列一個會沿用已經失效的
  結果**，所以它刻意寫成 superset，而且由 `tests/test_resume_validation.py`
  直接掃 skill 原始碼強制檢查，不靠人記得同步。

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

## Resume：逐 step 驗證，不是整份 hash

`--resume-from` 由 `persistence.plan_resume` 決定，輸出一個 **cut**：
最早不能再信任的 step。從它開始（含）全部重跑，它之前的逐一驗證後才 reuse。

會把 cut 往前推的有三件事：

| 觸發 | cut 落在 |
|---|---|
| 輸入資料變了（`provenance.input_digest`，逐檔 SHA-256） | 第一個 step |
| 某個 config key 變了 | `registry.earliest_step_reading` 算出的最早讀取者 |
| 某個跑過的 step 驗不過 | 那個 step 自己 |

每個 step 要能 reuse，六個條件全部要過，缺一就 fail closed 並連同下游一起重跑：

1. `run_metadata.json` 讀得到，而且有記 `source.config`（舊 run 沒有 → 全部重跑）
2. 輸入 digest 兩邊都算得出來且相同
3. `<step>/output.json` 讀得到
4. audit log 裡有這個 step 的 `step_end`，狀態是 `ok`（`scaffold` / `error` 不算）
5. `output.json` 裡沒有 `errors`
6. `ARTIFACT_PATH_KEYS` 記的每個檔案都還在

**沒有紀錄的 step 不算失敗**，也不會推動 cut —— filtered 路線上 `load_raw_counts`
本來就不會跑，它的缺席不代表 `merge_samples` 有問題。

決策會寫成一筆 `resume_plan` audit 事件（reused / rerun_from / reasons），
因為「reuse 了 18 個 step」跟「一個都沒 reuse」從外面看一模一樣，
但只有一個描述的是同一次分析。

## 兩種 resume，責任分開

| | 問的問題 | 用什麼回答 |
|---|---|---|
| `--resume-from RUN_ID` | 哪些**結果**還有效 | 磁碟上的 artifact + metadata + audit log |
| `--continue-from RUN_ID` | 這次執行**停在哪裡** | LangGraph checkpoint（SQLite） |

兩者不合併，理由沒有變：checkpoint 記的是 graph 做過什麼，它可能跟磁碟不一致
——刪掉一個 `.h5ad`、或用 standalone CLI 單獨重跑某個 step，checkpoint 仍然
認為那個 step 完成了。讓它回答「哪些結果還有效」就會讓這個失敗變成無聲的。

所以各自只回答自己看得到的事，不需要有人在衝突時當裁判。

```bash
# 停在 gate，process 可以直接關掉
python -m src.run --input <matrix> --species human --interactive

# 之後任何時候，另一個 process 接手回答
python -m src.run --continue-from <RUN_ID> --interactive
```

checkpoint 檔在 **`runs/<run_id>/checkpoint.sqlite`**，跟該次 run 的 artifact
放在一起——刪掉一次 run 就一併刪掉它的 checkpoint，兩次 run 也不可能共用
thread table。只有 `--interactive` 會寫；不會停下來等人的執行不需要付這個成本。

`--continue-from` 找不到東西時一律 `ResumeError` 並 **exit code 4**，**絕不從
START 重跑**：run 目錄不存在、checkpoint 檔不存在、thread_id 在資料庫裡找不到、
資料庫壞掉、或那次 run 根本沒有停在 gate（例如已經被回答過了）。每一種的替代
方案都是「用新的 state 呼叫 invoke 看看會怎樣」，而那會在第一次 run 的 id 底下
產生第二份分析。

exit code 的完整對照（`src/run.py` 的 `EXIT_CODES`）：`0` completed 或
needs_review、`1` failed、`2` halted（沒產出報告就停了）、`3` running、
`4` 無法 continue。`2` 跟 `4` 分開，是因為「分析停住」跟「找不到可回答的東西」
是兩種不同的問題。

## Registry overview

<!-- BEGIN GENERATED registry-table — python scripts/export_registry_docs.py -->

26 steps, in the order `src/registry.py` declares them — which is
also a valid topological order for both routes, and is what
`registry.steps_invalidated_by` reads to decide what a config change stales.

| # | step | kind | judge node | branches | revisable at its gate |
|---|---|---|---|---|---|
| 1 | `ingest_validate` | utility | `judge_ingest` | yes | — |
| 2 | `sample_qc_triage` | utility | `judge_sample_qc` | yes | — |
| 3 | `resolve_reference` | utility | `judge_reference` | — | — |
| 4 | `matrix_preflight` | utility | `judge_matrix_preflight` | — | — |
| 5 | `fastq_preflight` | upstream | `judge_fastq_preflight` | — | — |
| 6 | `fastq_qc` | upstream | `judge_fastq_qc` | — | — |
| 7 | `cellranger_count` | upstream | `judge_cellranger_count` | — | — |
| 8 | `count_matrix_classify` | router | `judge_matrix_classify` | yes | — |
| 9 | `load_raw_counts` | analysis | `judge_raw_counts` | yes | — |
| 10 | `load_filtered_counts` | analysis | `judge_filtered_counts` | — | — |
| 11 | `cell_calling_review` | analysis | `judge_cell_calling` | yes | `force_cells`, `min_umi` |
| 12 | `merge_samples` | analysis | `judge_merge` | — | — |
| 13 | `post_load_validate` | analysis | `judge_post_load` | — | — |
| 14 | `run_qc_metrics` | analysis | `judge_qc` | — | — |
| 15 | `apply_cell_qc_filter` | analysis | `judge_cell_qc_filter` | yes | `min_genes`, `min_counts`, `max_pct_mito` |
| 16 | `detect_doublets` | analysis | `judge_doublets` | — | — |
| 17 | `normalize_hvg_prepare` | analysis | `judge_preprocess` | — | — |
| 18 | `run_pca` | analysis | `judge_pca` | — | — |
| 19 | `run_integration` | analysis | `judge_integration` | — | — |
| 20 | `run_clustering` | analysis | `judge_clustering` | — | — |
| 21 | `run_umap` | analysis | `judge_umap` | — | — |
| 22 | `find_markers` | analysis | `judge_markers` | — | — |
| 23 | `annotate_cells` | analysis | `judge_annotation` | — | `celltypist_model` |
| 24 | `cross_check_annotation` | analysis | `judge_cross_check` | — | `scmayomap_tissue` |
| 25 | `human_review_decision` | gate | — | — | — |
| 26 | `build_report` | utility | `judge_report` | — | — |

25 of 26 steps are judged; `human_review_decision` is a gate,
not a scored step. 6 own their outgoing edges in `graph.py` rather than
having a single successor. 4 accept a value from a person at their
gate — the four that stop rather than guess.

`judge node` is a **node name in the graph and a label in the audit log**, not a
module: there is one judge implementation in `src/judge.py` and every step hands
it a different payload. Inputs, outputs and failure modes are per step and live in
`skills/<step>/SKILL.md`; the exact topology is `docs/graph.mmd`, generated from
the compiled graph by `scripts/export_graph.py`.

<!-- END GENERATED registry-table -->

The table is written by `scripts/export_registry_docs.py` from `src/registry.py`
and CI checks it with `--check`. Editing it by hand will be overwritten; change
the registry instead.

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

### 4. Judging

There is no judge tool, and no `skills/judge_*`. This section used to list
nineteen of them, contradicting the design rule six screens above — an early
MCP-first design that was dropped, whose folders were deleted and whose
documentation stayed.

One implementation in `src/judge.py`, one `JudgeResult` contract, and each step
hands it a different payload. `REGISTRY[step].judge` is the **name of the node
in the graph and the label in the audit log**, which is why the generated table
calls that column "judge node".

Which steps are judged, and under what node name, is in the generated table
above rather than repeated here. `build_report` is judged like every other step
and its verdict reaches the same gate — it used to edge straight to `END`, which
made it the one judge whose `fail` was recorded and then ignored.

### Judge contract

Every verdict is the same JSON shape, validated against
`schemas/judge_result.schema.json`:

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
- A step is added in three places, not two: `skills/<name>/<name>.py` with a
  `run(payload) -> dict`, one `StepSpec` in `src/registry.py`, **and its edges in
  `src/graph.py`**. This used to say the wiring was generated from the registry.
  It never has been. The registry says which steps exist and what judges them;
  `graph.py` says what follows what, and it has to, because a conditional edge
  is a Python predicate over state and no table can hold one.
  `assert_registry_covered()` catches a step that was registered and never
  wired, which is the failure that claim would otherwise hide.
- Because the call is a plain import, every step is also a standalone CLI:
  `python skills/<name>/<name>.py --help`. The count is in the generated table
  above rather than written here, because it was wrong the last three times.
