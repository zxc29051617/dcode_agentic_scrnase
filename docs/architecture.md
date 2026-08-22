# 架構

## 這是什麼

把 `scrna-orchestrator` 的固定分析流程，包成一個由 LangGraph 驅動的工作流。
四種東西組成它：

- **分析步驟**（程式碼裡叫 deterministic analysis step）：固定、可重現的程式分析 ——
  QC、count、cell calling、filter、doublets、normalization、PCA、integration、
  clustering、markers、annotation、report
- **模型檢查**（judge step）：每個分析步驟完成後，本地模型看這一步的結果合不合理
- **人工確認**（human gate）：需要人決定時，流程停下來等人回答
- **執行紀錄**（provenance）：每一步都記錄輸入、輸出、模型判斷、人工決定

```text
    ┌────────────────────┐
    │ 輸入               │  FASTQ / MTX / .h5 / .h5ad
    └─────────┬──────────┘
              ↓
    ┌────────────────────┐
    │ 分析步驟（程式）   │  26 步，順序寫死在 graph 裡
    │                    │  → docs/workflow.md
    └─────────┬──────────┘
              ↓
    ┌────────────────────┐
    │ 模型檢查           │  看結果合不合理，給判斷與建議
    │                    │  不會寫入分析結果
    └─────────┬──────────┘  → docs/judge_setup.md
              ↓
    ┌────────────────────┐
    │ 人工確認           │  預設停下來，不會自動放行
    └─────────┬──────────┘
              ↓
    ┌────────────────────┐
    │ 執行紀錄 + 報告    │  audit.jsonl、report.md、report.html
    └────────────────────┘  → docs/report_contract.md
```

`docs/graph.mmd` 是編譯後 graph 的匯出，node/edge 數量以該檔為準。

## 設計原則

1. **分析由程式做，結果可重現。** 模型不會修改任何科學結果。
2. **檢查與執行分開。** 模型只讀整理好的結果與統計數值，回傳判斷與建議。檢查節點的
   回傳值只有 `judge_results`（`src/nodes.py`），沒有任何欄位能讓建議值走到
   `artifacts` 或設定裡。
3. **需要人判斷的決定一定停下來。** `DEFAULT_POLICY` 的
   `autocontinue_on_warn=False` 是預設值，不會自動放行。
4. **每個重要決定都有紀錄，可以回頭查。** 輸入、輸出、模型判斷、設定、人工決定
   都寫進執行紀錄，每筆判斷會帶上做出它的模型。
5. **流程中斷後可以繼續。** 從進度檔接著跑，或沿用還有效的結果檔，不必全部重來。
6. **閾值不寫死。** QC 閾值、細胞數、CellTypist 模型都由人給；程式碼裡沒有預設值，
   因為寫在設定裡的值會被記錄下來，寫死在程式碼裡的預設值不會。

另一條貫穿全案的原則：任何可執行流程先設計成 state machine，再決定是否接 LangChain。

## 目錄

**程式碼**（進 git）

| | |
|---|---|
| `src/` | LangGraph orchestrator 實作。`src/service.py` 是給非終端機前端用的薄接縫 |
| `skills/` | 每個分析步驟一個工具（`SKILL.md` 契約 + Python 實作），26 個，與 `src/registry.py` 一一對應 |
| `services/gateway/` | 唯讀 FastAPI projection，只有 GET，永不 import `src/` |
| `services/controller/` | 可寫的 analysis controller（驗證、確認、排程）+ scientific worker（唯一呼叫 executor 的地方） |
| `apps/web/` | Next.js / CopilotKit 前端 |
| `tests/` | `python tests/run_all.py` 全跑 |
| `scripts/` | 維運腳本（取測試資料、連 reference、匯出 graph、查磁碟用量） |

**資料與設定**（進 git）

| | |
|---|---|
| `prompts/` | 模型檢查用的提示詞。`local_judge_base.md` 是共用的，`steps/<step>.md` 是個別步驟的加註 |
| `marker_db/` | cell type 註解用的 marker 資料庫（scMayoMap，785 KB 純文字） |
| `schemas/` | 模型判斷 / 狀態 / 輸出的 JSON schema |
| `docs/` | 架構、報告契約、`graph.mmd`（編譯後的 graph 匯出） |
| `workflows/` | LangGraph workflow 草圖與版本化設計 |

**外部大檔**（只有 symlink 和 README 進 git，內容 gitignore）

| | |
|---|---|
| `data/` | 測試資料集（見 `data/README.md`） |
| `reference/` | Cell Ranger 的基因組 reference，20–32 GB（見 `reference/README.md`） |
| `tools/` | Cell Ranger 等第三方執行檔（見 `tools/README.md`） |

**執行產物**（完全 gitignore）

`runs/<run_id>/` 是每次執行的所有輸出。每一步各存一份 `adata.h5ad`，所以中斷後可以
從已完成的步驟繼續；代價是**一次執行約 410 MB**。清理方式見 [`development.md`](development.md#磁碟)。

> `reference/` 是基因組（幾十 GB、機器相關、不進 git）；`marker_db/` 是細胞型別的
> marker 表（不到 1 MB、進 git）。兩者無關。

## src/ 分層

| 檔案 | 負責 |
|---|---|
| `registry.py` | 有哪些步驟、對應哪個 skill、由哪個節點檢查 |
| `species.py` | 物種→reference / mito prefix / 紅血球基因的對照表（純資料） |
| `matrix_io.py` | 矩陣讀寫、barcode-rank 證據（knee / inflection）、細胞挑選 |
| `nodes.py` | graph 節點：跑一個步驟、檢查一個步驟、停下來等人 |
| `judge.py` | 模型檢查的契約與後端（stub / 本地模型） |
| `policy.py` | 什麼樣的判斷結果才能繼續往下走 |
| `graph.py` | `workflows/fastq_count_main_graph.md` 的接線 |
| `state.py` | 節點之間傳遞的狀態 |
| `provenance.py` | 執行紀錄（只新增、不修改舊紀錄）+ 執行開始時的環境快照 |
| `persistence.py` | 暫停（寫進度檔）與繼續（從 run_dir 既有的結果檔判斷）；磁碟成本的取捨寫在檔頭 |
| `plots.py` | 報告的 12 個圖組 |

**兩個真相來源，各管一半：**

| | 負責什麼 |
|---|---|
| `src/registry.py` | 有哪些步驟、各自的種類、由哪個節點檢查、可改哪些參數 |
| `src/graph.py` | **流程拓撲** —— 誰接誰、條件分支、人工確認接在哪 |

只在 `registry.py` 登記而不動 `graph.py` 不會「自動長出」接線，而是直接報錯。細節見
[`development.md`](development.md#加一個新的-step)。
