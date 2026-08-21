# dcode_agentic_scrnaseq

[![tests](https://github.com/zxc29051617/dcode_agentic_scrnaseq/actions/workflows/tests.yml/badge.svg)](https://github.com/zxc29051617/dcode_agentic_scrnaseq/actions/workflows/tests.yml)

> 可稽核、可續跑的單細胞 RNA-seq agent workflow：deterministic 分析 + 地端模型評斷
> + human gate + 全程 provenance。

## 這是什麼

一條固定順序的 scRNA-seq 分析流程，由 LangGraph 驅動，26 個 step 從 FASTQ 或矩陣
一路做到報告。每個分析步驟跑完之後，地端模型讀該步的結構化證據給一份評分與建議；
有歧義或有後果的決定停在 human gate 等人回答；輸入、輸出、模型 verdict、人工決策
和環境快照全部進 audit log。

```text
輸入：FASTQ / MTX / .h5 / .h5ad
輸出：分析完的 AnnData + audit log + report.md + report.html
```

## 為什麼做這個

固定流程的部分不需要模型即興發揮，需要模型的是「這一步的結果合不合理」。所以這裡
把兩件事分開：**分析由程式做，評斷由模型做，決定由人做。** 模型永遠不寫入分析結果，
所以它答錯的時候不會靜默地污染資料，只會多一段要人看的文字。

同一個理由讓 QC 閾值、細胞數、CellTypist 模型都沒有程式碼預設值——config 裡的值會
進 audit log，程式碼裡的預設不會。

## 設計原則

1. **分析是 deterministic 的。** 模型不直接修改科學結果。
2. **評斷與執行分離。** 模型讀結構化證據，回傳 verdict 與建議。
3. **人的決策是明示的。** 預設走到 human gate 就停，不偷偷放行。
4. **每個決定都可稽核。** 含產生每筆 verdict 的模型與 prompt hash。
5. **續跑是一等公民。** 從 checkpoint 接續，或沿用仍然有效的 artifact。

## 運作方式

```text
        輸入
          ↓
      Preflight          偵測格式、選 reference、驗證物種、FastQC/MultiQC
          ↓
   Count / Load          Cell Ranger，或直接讀矩陣
          ↓
    Cell calling         raw 矩陣時：給證據，讓你決定細胞數
          ↓
         QC              列出每個閾值濾掉多少，讓你決定
          ↓
      Doublets           預設只標記不刪除
          ↓
  Normalization / PCA
          ↓
Integration / Clustering
          ↓
      Embedding          UMAP / t-SNE
          ↓
  Markers / Annotation   CellTypist，模型由你選
          ↓
    Human review
          ↓
       Report            report.md + report.html + 稽核章節
```

每一步之後都接一個 judge node；每一步的輸入輸出與 verdict 都寫進 provenance。
完整的 26 個 step → [`docs/workflow.md`](docs/workflow.md)。
編譯後的 graph → [`docs/graph.mmd`](docs/graph.mmd)。

## Quick start

```bash
# 1. 建環境（從 lockfile，不要從 environment.yml 重解）
pip install conda-lock==4.0.2
conda-lock install --micromamba --name dcode-scrna conda-lock.yml
conda activate dcode-scrna

# 2. 放 reference（FASTQ 路線才需要；見 reference/README.md）
ls reference/

# 3. 跑
python -m src.run --input /path/to/filtered_feature_bc_matrix

# 4. 看結果
ls runs/<run_id>/build_report/
```

環境的坑（尤其**不要**用 `micromamba -f conda-lock.yml`）→
[`docs/environment.md`](docs/environment.md)。

judge 預設是 `StubJudge`，不需要模型也不碰網路；要接真的模型 →
[`docs/judge_setup.md`](docs/judge_setup.md)。

## 輸入

`--input` 給什麼由 `ingest_validate` 自己偵測：

| 格式 | 走哪條線 |
|---|---|
| FASTQ 目錄 | preflight → FastQC/MultiQC → Cell Ranger count → 載入 |
| MTX 目錄 / `.h5` | `count_matrix_classify` 判斷 raw 還是 filtered，raw 走 cell calling |
| `.h5ad` | 直接載入 |

`--species` 決定用哪份 reference 和 QC 常數（`--reference` 可明確覆寫）；物種和
reference 對不上會在第二步就停，不會等 count 跑完才發現。多樣本、樣本級分流與
各種分析參數 → [`docs/workflow.md`](docs/workflow.md)。

## 輸出

```text
runs/<run_id>/
├── <step_name>/            每個 step 一個目錄，含該步的 adata.h5ad
│   └── ...
├── build_report/
│   ├── report.md           可進 git、agent 好讀
│   ├── report.html         圖內嵌成 data URI，單檔可寄
│   ├── report_model.json   兩份 rendering 的共同來源
│   └── figures/
├── audit.jsonl             append-only：輸入、輸出、verdict、人工決策
├── run_metadata.json       環境快照、套件版本、judge_sessions
└── checkpoint.sqlite       只有 --interactive 會寫
```

中間的 AnnData 是續跑能運作的原因，也是**一次執行約 410 MB** 的原因。沒有任何自動
刪除；清理方式見 [`docs/development.md`](docs/development.md#磁碟)。
報告的三層結構與契約 → [`docs/report_contract.md`](docs/report_contract.md)。

## 常見用法

```bash
# 矩陣輸入，先看證據再決定閾值
python -m src.run --input <matrix>
python -m src.run --input <matrix> --min-genes 200 --max-pct-mito 15

# FASTQ 輸入，走完整條線（--headless-decision accept 是明確的 opt-in）
python -m src.run --input <fastq_dir> --headless-decision accept

# 改了參數想重跑，但沿用還有效的結果
python -m src.run --input <matrix> --resume-from <run_id> --min-genes 200

# 上次停在 human gate，回來回答它
python -m src.run --continue-from <run_id> --interactive
```

完整 CLI 與 exit code → [`docs/cli.md`](docs/cli.md)。
兩種續跑的差別（`--resume-from` vs `--continue-from`）→
[`docs/resume.md`](docs/resume.md)。
瀏覽器介面 → [`docs/web.md`](docs/web.md)。

## 目前狀態

主線完整，跑得完真實資料。

- ✅ 26 個 workflow step 全部實作，`skills/` 底下一個資料夾一個 step，沒有空殼
- ✅ CLI 執行、human gate、`--resume-from` / `--continue-from`
- ✅ 地端 judge（任何 OpenAI 相容端點）、provenance / audit log
- ✅ Markdown + HTML 報告
- 🚧 judge 的**步驟專屬 prompt 只做了 8 步**，其餘 17 步用共用的 base prompt ——
  進度與量測見 [`docs/judge_prompt_plan.md`](docs/judge_prompt_plan.md)
- 🚧 Web 是 **local-development MVP**：SQLite、polling、沒有 authentication

## 文件

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 設計原則、目錄配置、`src/` 分層 |
| [`docs/workflow.md`](docs/workflow.md) | 26 個 step、輸入路線、每個由人決定的參數 |
| [`docs/cli.md`](docs/cli.md) | 完整 CLI、單一 skill 執行、exit code |
| [`docs/resume.md`](docs/resume.md) | 兩種續跑問的是不同的問題 |
| [`docs/environment.md`](docs/environment.md) | conda-lock、pin 了什麼、lock 蓋不到什麼 |
| [`docs/judge_setup.md`](docs/judge_setup.md) | judge backend、端點設定、模型實測、送出去的 payload |
| [`docs/judge_prompt_plan.md`](docs/judge_prompt_plan.md) | 步驟專屬 prompt 做到哪、量到什麼 |
| [`docs/report_contract.md`](docs/report_contract.md) | 報告三層結構，`build_report` 能與不能做什麼 |
| [`docs/web.md`](docs/web.md) | CLI 與 Web 的差別、最小可複製啟動 |
| [`docs/analysis_request_contract.md`](docs/analysis_request_contract.md) | 分析請求的 API 契約 |
| [`docs/development.md`](docs/development.md) | 測試、加一個新的 step、磁碟管理 |
| [`docs/decisions.md`](docs/decisions.md) | 為什麼現在不是別的樣子 |

`git log` 是第一手來源，這個 repo 的 commit message 是刻意寫長的。

## 開發

```bash
python tests/run_all.py
```

測試數量固定，但實際跑幾個取決於本機有沒有那幾十 GB 的資料集——缺的會乾淨地 skip
並說明它缺哪份資料。**通過數會變，兩件事不會變：沒有 failure（CI baseline 是 0 fail），
而且每個 skip 都指名缺什麼。** 當下的數字看上面那顆 badge。

step 註冊在 `src/registry.py`，**接線在 `src/graph.py`**，兩份都要改：只加 `StepSpec`
不會自動長出接線，而是被 `build_graph()` 裡的 `assert_registry_covered()` 擋成明確
錯誤。加一個新 step 的完整流程與磁碟管理見
[`docs/development.md`](docs/development.md)。
