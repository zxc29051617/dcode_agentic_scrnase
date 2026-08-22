# dcode_agentic_scrnaseq

[![tests](https://github.com/zxc29051617/dcode_agentic_scrnaseq/actions/workflows/tests.yml/badge.svg)](https://github.com/zxc29051617/dcode_agentic_scrnaseq/actions/workflows/tests.yml)

> **程式做 scRNA-seq 分析，AI 檢查分析結果，人決定需要判斷的地方，
> 而且每一步都留下紀錄、可以中斷後繼續。**

## 這是什麼

一條固定順序的單細胞 RNA-seq 分析流程，26 個步驟從 FASTQ 或表現矩陣一路做到報告。
分析本身完全由程式執行，順序是寫死的，不由 AI 決定。

每個步驟完成後，本地模型會讀取這一步產生的結果與統計數值，判斷結果是否合理，
並提出建議。如果這一步需要人決定，例如 QC 閾值或 CellTypist 模型，流程就會停下來
等你確認，不會自動替你決定。

同時，程式會記錄每一步的輸入、輸出、模型判斷、人工決定，以及當時的軟體環境，
方便之後追查。

```text
輸入：FASTQ / MTX / .h5 / .h5ad
輸出：分析完的 AnnData + 執行紀錄 + report.md + report.html
```

## 為什麼做這個

**這個專案不讓 AI 自己決定怎麼分析資料。**

scRNA-seq 的分析步驟由固定的程式執行 —— QC、normalization、PCA、clustering、
annotation 這些步驟該怎麼跑，是已知的，不需要模型即席發揮。AI 的工作是看這些步驟
產生的結果，回答「這個結果看起來合理嗎？」。

如果 AI 認為有問題，它只能提出建議，不能直接修改分析結果。所以模型判斷錯誤時，
不會不知不覺改壞你的資料，只會多一段要人看的文字。真正要做決定的時候，交給人確認。

同樣的理由，QC 閾值、細胞數、CellTypist 模型都沒有寫死在程式碼裡的預設值 ——
寫在設定裡的值會被記錄下來，寫死在程式碼裡的預設值不會。

## 設計原則

1. **分析由程式做，結果可重現。** 模型不會修改任何科學結果。
2. **檢查與執行分開。** 模型只讀整理好的結果與統計數值，回傳判斷與建議。
3. **需要人判斷的決定一定停下來。** 預設不會自動放行。
4. **每個重要決定都有紀錄，可以回頭查。** 包含做出每個判斷的模型與提示詞版本。
5. **流程中斷後可以繼續。** 從已完成的步驟接著跑，不必全部重來。

## 運作方式

整條流程可以簡單理解成三種工作：

> **程式負責分析 → 模型負責檢查 → 人負責需要人工決定的地方。**

每一個分析步驟都走一次這個循環：

```text
    程式：執行一個分析步驟
              ↓
    模型：檢查這一步的結果合不合理
              ↓
       需要人決定嗎？
        ├─ 否 ──→ 繼續下一步
        └─ 是 ──→ 流程停下來，等你確認 ──→ 你做決定 ──→ 繼續下一步
```

26 個步驟串起來大致是這樣（右邊是那一步由誰決定）：

```text
        輸入
          ↓
     執行前檢查        程式：判斷檔案格式、選 reference、驗證物種、跑 FastQC/MultiQC
          ↓
   Count / 載入        程式：Cell Ranger，或直接讀矩陣
          ↓
   Cell calling        **你決定**：程式給你證據，你決定留幾顆細胞
          ↓
         QC            **你決定**：程式列出每個閾值會濾掉多少，你決定閾值
          ↓
      Doublets         程式：預設只標記不刪除
          ↓
Normalization / PCA    程式
          ↓
Integration / Clustering  程式
          ↓
      Embedding        程式：UMAP / t-SNE
          ↓
Markers / Annotation   **你決定**：CellTypist 用哪個模型
          ↓
      人工確認         **你決定**：看完模型的判斷與建議，決定繼續或停止
          ↓
       報告            程式：report.md + report.html，含流程查核章節
```

完整的 26 個步驟 → [`docs/workflow.md`](docs/workflow.md)。
編譯後的流程圖 → [`docs/graph.mmd`](docs/graph.mmd)。

### 名詞對照

程式碼和 `docs/` 底下用的是英文名稱，這份 README 用白話。對照表：

| 程式裡叫 | 意思 |
|---|---|
| judge | 檢查該步結果的模型 |
| verdict | 模型的判斷結果（pass / warn / fail） |
| advice | 模型給人看的建議，**永遠不會被自動套用** |
| human gate | 需要人確認時，流程停下來等你決定的地方 |
| provenance / audit log | 執行紀錄：每一步用了什麼、產生什麼、誰做了什麼決定 |
| artifact | 已經產生、可以拿來繼續跑的結果檔 |
| checkpoint | 流程中斷時保存下來的進度 |
| preflight | 執行前檢查 |

## Quick start

```bash
# 1. 建環境（從 lockfile，不要從 environment.yml 重新解析）
pip install conda-lock==4.0.2
conda-lock install --micromamba --name dcode-scrna conda-lock.yml
conda activate dcode-scrna

# 2. 放 reference（只有 FASTQ 輸入需要；見 reference/README.md）
ls reference/

# 3. 跑
python -m src.run --input /path/to/filtered_feature_bc_matrix

# 4. 看結果
ls runs/<run_id>/build_report/
```

環境的坑（尤其**不要**用 `micromamba -f conda-lock.yml`）→
[`docs/environment.md`](docs/environment.md)。

檢查結果的模型預設是 `StubJudge`：不需要任何模型、也不連網路，只看步驟自己回報的
狀態。要接真的模型 → [`docs/judge_setup.md`](docs/judge_setup.md)。

## 輸入

`--input` 給什麼，由第一個步驟自己判斷：

| 格式 | 走哪條路 |
|---|---|
| FASTQ 目錄 | 執行前檢查 → FastQC/MultiQC → Cell Ranger count → 載入 |
| MTX 目錄 / `.h5` | 程式判斷是 raw 還是 filtered，raw 會停下來讓你決定細胞數 |
| `.h5ad` | 直接載入 |

`--species` 決定用哪份 reference 和 QC 常數（`--reference` 可以明確指定）；物種和
reference 對不上會在第二步就停，不會等 count 跑完 20–40 分鐘才發現。
多樣本、樣本篩選與各種分析參數 → [`docs/workflow.md`](docs/workflow.md)。

## 輸出

```text
runs/<run_id>/
├── <步驟名稱>/             每個步驟一個目錄，含這一步完成時的 adata.h5ad
│   └── ...
├── build_report/
│   ├── report.md           純文字，可以進 git、AI 也讀得動
│   ├── report.html         圖片直接內嵌，單一檔案可以寄給人
│   ├── report_model.json   上面兩份報告的共同資料來源
│   └── figures/
├── audit.jsonl             執行紀錄：只新增、不修改舊紀錄
├── run_metadata.json       執行環境、套件版本、用了哪個模型檢查
└── checkpoint.sqlite       只有 --interactive 模式會寫
```

每個步驟都會把當時的 AnnData 保存下來，所以流程中斷後可以從已完成的步驟繼續，
不必從頭分析。代價是中間檔案不少，**一次執行約 410 MB**。程式不會自動刪任何東西；
清理方式見 [`docs/development.md`](docs/development.md#磁碟)。
報告的三層結構與規格 → [`docs/report_contract.md`](docs/report_contract.md)。

## 常見用法

```bash
# 表現矩陣輸入：先看證據，再決定閾值
python -m src.run --input <matrix>
python -m src.run --input <matrix> --min-genes 200 --max-pct-mito 15

# FASTQ 輸入，一路跑完不停（--headless-decision accept 表示「不經人工確認直接採用」，
# 必須明確指定才會啟用）
python -m src.run --input <fastq_dir> --headless-decision accept

# 改了參數想重跑，但沿用還有效的結果
python -m src.run --input <matrix> --resume-from <run_id> --min-genes 200

# 上次停在人工確認，回來回答它
python -m src.run --continue-from <run_id> --interactive
```

完整 CLI 與結束代碼 → [`docs/cli.md`](docs/cli.md)。
兩種「繼續執行」的差別（`--resume-from` 沿用還有效的結果，`--continue-from` 回答上次
停在哪）→ [`docs/resume.md`](docs/resume.md)。
瀏覽器介面 → [`docs/web.md`](docs/web.md)。

## 目前狀態

主線完整，跑得完真實資料。

- ✅ 26 個分析步驟全部實作，`skills/` 底下一個資料夾一個步驟，沒有空殼
- ✅ CLI 執行、人工確認、`--resume-from` / `--continue-from`
- ✅ 本地模型檢查（任何 OpenAI 相容端點）、執行紀錄
- ✅ Markdown + HTML 報告
- 🚧 **檢查用的提示詞還沒寫完。** 26 個步驟裡有 25 個會被模型檢查，但其中只有 8 個
  有專門為那一步設計的提示詞，另外 17 個暫時共用一份通用提示詞 —— 進度與實測見
  [`docs/judge_prompt_plan.md`](docs/judge_prompt_plan.md)
- 🚧 網頁介面是**本機開發用的 MVP**：SQLite、輪詢、沒有登入驗證，不要對外開放

## 文件

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 設計原則、目錄配置、`src/` 分層 |
| [`docs/workflow.md`](docs/workflow.md) | 26 個步驟、輸入路線、每個需要你決定的參數 |
| [`docs/cli.md`](docs/cli.md) | 完整 CLI、單獨執行一個步驟、結束代碼 |
| [`docs/resume.md`](docs/resume.md) | 兩種「繼續執行」問的是不同的問題 |
| [`docs/environment.md`](docs/environment.md) | conda-lock、釘住了什麼、lockfile 蓋不到什麼 |
| [`docs/judge_setup.md`](docs/judge_setup.md) | 檢查用的模型怎麼接、實測比較、送出去的資料有什麼 |
| [`docs/judge_prompt_plan.md`](docs/judge_prompt_plan.md) | 專屬提示詞做到哪、量到什麼 |
| [`docs/report_contract.md`](docs/report_contract.md) | 報告三層結構，產生報告時能做與不能做什麼 |
| [`docs/web.md`](docs/web.md) | 網頁介面與 CLI 的差別、最小可複製啟動 |
| [`docs/analysis_request_contract.md`](docs/analysis_request_contract.md) | 分析請求的 API 規格 |
| [`docs/development.md`](docs/development.md) | 測試、新增一個步驟、磁碟管理 |
| [`docs/decisions.md`](docs/decisions.md) | 為什麼現在不是別的樣子 |

`git log` 是第一手來源，這個 repo 的 commit message 是刻意寫長的。

## 開發

```bash
python tests/run_all.py
```

測試數量固定，但實際會跑幾個取決於本機有沒有那幾十 GB 的資料集 —— 缺的會乾淨地
跳過（skip）並說明它缺哪份資料。**通過數會變，兩件事不會變：沒有 failure
（CI 基準是 0 fail），而且每個跳過的測試都指名缺什麼。** 當下的數字看上面那顆 badge。

新增一個分析步驟時，需要改兩個地方：

- `src/registry.py` —— 登記這個步驟的資訊
- `src/graph.py` —— 把它接進實際的流程

**只在 `registry.py` 登記是不夠的。** `build_graph()` 裡的 `assert_registry_covered()`
會檢查有沒有步驟登記了卻沒接進流程，漏接就直接報錯，不會靜靜地不執行。
完整流程與磁碟管理見 [`docs/development.md`](docs/development.md)。
