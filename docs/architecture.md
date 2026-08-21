# 架構

## 這是什麼

把 `scrna-orchestrator` 的固定分析流程，包成一個由 LangGraph 驅動的工作流：

- **deterministic analysis step**：QC、count、cell calling、filter、doublets、
  normalization、PCA、integration、clustering、markers、annotation、report
- **local judge step**：每個分析步驟後接地端模型評斷結果好不好
- **human gate**：必要時停下來讓人看
- **provenance**：每一步都記錄輸入、輸出、judge 結果、人工決策

```text
        ┌──────────────────────┐
        │ Input                │
        │ FASTQ / MTX / h5 /   │
        │ h5ad                 │
        └──────────┬───────────┘
                   ↓
        ┌──────────────────────┐
        │ Deterministic        │  26 個 step，順序由 graph 固定
        │ analysis step        │  → docs/workflow.md
        └──────────┬───────────┘
                   ↓
        ┌──────────────────────┐
        │ Judge                │  結構化評分 + 建議，不寫入結果
        │ (local / stub)       │  → docs/judge_setup.md
        └──────────┬───────────┘
                   ↓
        ┌──────────────────────┐
        │ Human gate           │  預設停下來，不偷偷放行
        └──────────┬───────────┘
                   ↓
        ┌──────────────────────┐
        │ Provenance + report  │  audit log、report.md、report.html
        │                      │  → docs/report_contract.md
        └──────────────────────┘
```

`docs/graph.mmd` 是編譯後 graph 的匯出，node/edge 數量以該檔為準。

## 設計原則

1. **分析是 deterministic 的。** 模型不直接修改科學結果。
2. **評斷與執行分離。** 模型讀結構化證據，回傳 verdict 與建議；judge node 的回傳值
   只有 `judge_results`（`src/nodes.py`），沒有任何 key 能讓建議值走到 `artifacts`
   或 config。
3. **人的決策是明示的。** 有歧義或有後果的決定停在 human gate；
   `DEFAULT_POLICY` 的 `autocontinue_on_warn=False` 是預設。
4. **每個決定都可稽核。** 輸入、輸出、模型 verdict、設定、人工決策都進 provenance，
   verdict 會帶上產生它的 model。
5. **續跑是一等公民。** run 可以從 checkpoint 接續，或沿用仍然有效的 artifact。
6. **閾值不寫死。** QC threshold、細胞數、CellTypist 模型都由人給；程式碼裡沒有
   預設值，因為 config 裡的值會進 audit log，程式碼裡的預設不會。

另一條貫穿全案的原則：任何可執行流程先設計成 state machine，再決定是否接 LangChain。

## 目錄

**程式碼**（進 git）

| | |
|---|---|
| `src/` | LangGraph orchestrator 實作。`src/service.py` 是給非終端機前端用的薄接縫 |
| `skills/` | 每個 workflow step 一個工具（`SKILL.md` 契約 + Python 實作），26 個，與 `src/registry.py` 一一對應 |
| `services/gateway/` | 唯讀 FastAPI projection，只有 GET，永不 import `src/` |
| `services/controller/` | 可寫的 analysis controller（驗證、確認、排程）+ scientific worker（唯一呼叫 executor 的地方） |
| `apps/web/` | Next.js / CopilotKit 前端 |
| `tests/` | `python tests/run_all.py` 全跑 |
| `scripts/` | 維運腳本（取測試資料、連 reference、匯出 graph、查磁碟用量） |

**資料與設定**（進 git）

| | |
|---|---|
| `prompts/` | judge 的提示詞。`local_judge_base.md` 是共用的，`steps/<step>.md` 是個別步驟的加註 |
| `marker_db/` | cell type 註解用的 marker 資料庫（scMayoMap，785 KB 純文字） |
| `schemas/` | judge / state / output 的 JSON schema |
| `docs/` | 架構、報告契約、`graph.mmd`（編譯後的 graph 匯出） |
| `workflows/` | LangGraph workflow 草圖與版本化設計 |

**外部大檔**（只有 symlink 和 README 進 git，內容 gitignore）

| | |
|---|---|
| `data/` | 測試資料集（見 `data/README.md`） |
| `reference/` | Cell Ranger 的基因組 reference，20–32 GB（見 `reference/README.md`） |
| `tools/` | Cell Ranger 等第三方執行檔（見 `tools/README.md`） |

**執行產物**（完全 gitignore）

`runs/<run_id>/` 是每次執行的所有輸出。每一步各存一份 `adata.h5ad` 以支援斷點續跑，
所以**一次執行約 410 MB**。清理方式見 [`development.md`](development.md#磁碟)。

> `reference/` 是基因組（幾十 GB、機器相關、不進 git）；`marker_db/` 是細胞型別的
> marker 表（不到 1 MB、進 git）。兩者無關。

## src/ 分層

| 檔案 | 負責 |
|---|---|
| `registry.py` | 有哪些 step、對應哪個 skill、誰來 judge |
| `species.py` | 物種→reference / mito prefix / 紅血球基因的對照表（純資料） |
| `matrix_io.py` | 矩陣讀寫、barcode-rank 證據（knee / inflection）、細胞挑選 |
| `nodes.py` | graph node：跑一個 step、judge 一個 step、停下來等人 |
| `judge.py` | judge 契約與 backend（stub / 本地模型） |
| `policy.py` | 什麼樣的 verdict 才能繼續 |
| `graph.py` | `workflows/fastq_count_main_graph.md` 的接線 |
| `state.py` | node 之間傳遞的狀態 |
| `provenance.py` | append-only audit log + run 開始時的環境快照 |
| `persistence.py` | 暫停（checkpointer）與續跑（從 run_dir 的 artifact 判斷）；磁碟成本的取捨寫在檔頭 |
| `plots.py` | 報告的 12 個圖組 |

**兩個真相來源，各管一半：**

| | 負責什麼 |
|---|---|
| `src/registry.py` | 有哪些 step、各自的 kind、由哪個 judge node 評分、可改哪些參數 |
| `src/graph.py` | **topology** —— 誰接誰、conditional routing、human gate 的接線 |

只加 `StepSpec` 而不動 `graph.py` 不會「自動長出」接線，而是直接失敗。細節見
[`development.md`](development.md#加一個新的-step)。
