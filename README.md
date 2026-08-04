# dcode_agentic_scrnaseq

這是新的單細胞 RNA-seq agent workflow 研究/實作區，只在這個資料夾內建立與修改，不動 `claude_agentic_scrna/`。

## 目標

把 `scrna-orchestrator` 的固定分析流程，包成一個可由 LangGraph / LangChain 驅動的工作流：

- deterministic analysis step：QC、count、cell calling、filter、doublets、normalization、PCA、integration、clustering、markers、annotation、report
- local judge step：每個分析步驟後接地端模型評斷結果好不好
- human gate：必要時停下來讓人看
- provenance：每一步都記錄輸入、輸出、judge 結果、人工決策

## 目前原則

- 分析與評斷分離
- 本地模型只做結構化評分，不直接改分析結果
- 任何可執行流程先設計成 state machine，再決定是否接 LangChain

## 目錄

- `docs/`：架構與 workflow 設計
- `data/`：測試資料集（內容 gitignore，見 `data/README.md`）
- `reference/`：Cell Ranger reference（內容 gitignore，見 `reference/README.md`）
- `tools/`：Cell Ranger 等第三方工具的 symlink（見 `tools/README.md`）
- `scripts/`：`get_test_data.sh`、`link_reference.sh` 等維運腳本
- `src/`：LangGraph orchestrator 實作
- `skills/`：每個 step 一個工具（`SKILL.md` 契約 + Python 實作）
- `schemas/`：judge / state / output 的 JSON schema
- `prompts/`：各 step 的 local judge prompt
- `workflows/`：LangGraph workflow 草圖與版本化設計

## 目前狀態

Orchestrator 可以跑。Skill **25 個裡實作了 15 個**，FASTQ 上游整段與 QC 都已經是真的：

| | |
|---|---|
| ✅ 已實作 | `ingest_validate`、`resolve_reference`、`matrix_preflight`、`fastq_preflight`、`fastq_qc`、`cellranger_count`、`count_matrix_classify`、`load_raw_counts`、`load_filtered_counts`、`cell_calling_review`、`merge_samples`、`post_load_validate`、`run_qc_metrics`、`apply_cell_qc_filter`、`detect_doublets` |
| ⬜ scaffold | 其餘 10 個（doublet 之後的 Scanpy 主線），`run()` 直接 raise `NotImplementedError` |

FASTQ 路線：偵測輸入 → 選 reference 並驗證物種 → 結構檢查 → **FastQC/MultiQC 品質評估** → count

Scaffold 不會讓流程崩潰，會被標成 `status="scaffold"`，summary 裡的 verdict 也寫成
`"pass (scaffold)"`、score 0，不會被誤讀成真的通過。

## 怎麼跑

```bash
conda activate dcode-scrna

# 一次性：把 reference 放進專案（symlink，不複製 32 GB）
bash scripts/link_reference.sh human /path/to/T2T_CHM13v2_RefSeqLiftoff_v5_3

# 預設 policy：走到 human gate 就停，不會偷偷放行
python -m src.run --input /path/to/filtered_feature_bc_matrix

# FASTQ 路線，走完整條線（--headless-decision accept 是明確的 opt-in）
python -m src.run --input ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs --headless-decision accept

# 只跑單一 skill，不進 graph
python skills/ingest_validate/ingest_validate.py ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs
python skills/resolve_reference/resolve_reference.py --species human --fastq
python skills/fastq_preflight/fastq_preflight.py ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs \
  --reference reference/T2T_CHM13v2_RefSeqLiftoff_v5_3

# 只跑定序品質評估（FastQC + MultiQC）
python skills/fastq_qc/fastq_qc.py --fastqs ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs \
  --run-dir runs/manual --threads 8

# 只跑 count（約 20-40 分鐘）。cellranger 路徑會自己找，不用給
python skills/cellranger_count/cellranger_count.py \
  --fastqs ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs --sample pbmc_1k_v3 \
  --transcriptome reference/T2T_CHM13v2_RefSeqLiftoff_v5_3 \
  --run-dir runs/manual --localcores 32 --localmem 128

# 測試
python tests/run_all.py
```

資料、reference、工具都在專案內，但**內容不進 git**（約 27 GB）。缺的時候相關測試會
乾淨地 skip，所以剛 clone 下來就能跑，只是測得比較少：

```bash
bash scripts/get_test_data.sh          # 列出需要什麼、有什麼
bash scripts/get_test_data.sh fastq    # 18 GB，FASTQ 路線
bash scripts/link_reference.sh         # reference 怎麼放
```

`--input` 給什麼由 `ingest_validate` 自己偵測（FASTQ / MTX / .h5 / .h5ad）。
`--species` 決定用哪份 reference 和 QC 常數（`--reference` 可以明確覆寫）；
物種和 reference 對不上會在第二步就停下來，不會等 count 跑完才發現。

`--matrix-kind` 已經不需要了——`count_matrix_classify` 會從矩陣本身判斷 raw/filtered。

**多樣本**：每個樣本各自 count → 各自載入 →（raw 路線各自決定細胞數）→
`merge_samples` 合併成一個 AnnData 並加 `sample` 標籤 → 之後共用同一條主線。
細胞數可以統一給一個值，也可以逐樣本給：

```bash
--force-cells 1500                      # 每個樣本都留 1500
--force-cells '{"A": 1500, "B": 2400}'  # 逐樣本（config 用 dict）
```

**QC 閾值也由你決定**。沒給閾值時 `apply_cell_qc_filter` 會列出每個候選值會濾掉多少細胞，
然後停下來——程式碼裡沒有任何預設閾值：

```bash
# 第一次：看證據
python -m src.run --input <matrix>

# 決定後
python -m src.run --input <matrix> --min-genes 200 --max-pct-mito 15
```

發表論文常見的 200 / 20% 是特定組織、特定 protocol 的值。你自己的標準值放 config，
不要放程式碼——config 裡的值會進 audit log，程式碼裡的預設不會。

**Doublet 預設只標記不刪除**。`detect_doublets` 每個 library 各自跑 Scrublet
（doublet 只在同一個 GEM well 裡形成），expected rate 從 10x 的 loading table 推，
不用 Scrublet 那個假設回收 8,000 顆細胞的 0.06 預設：

```bash
--remove-doublets                       # 真的刪掉（預設只加 obs 欄位）
--expected-doublet-rate 0.05            # 覆寫推算值
```

**細胞數由你決定**。走 raw 矩陣時，`cell_calling_review` 會先給你證據
（斷崖位置、各個候選細胞數對應的 UMI 門檻）然後停下來，不會替你挑數字：

```bash
# 第一次：看證據
python -m src.run --input <raw_feature_bc_matrix.h5>

# 決定後：--force-cells 留前 N 個，或 --min-umi 設門檻
python -m src.run --input <raw_feature_bc_matrix.h5> --force-cells 1500
```

`--force-cells` 等同 Cell Ranger 的 `--force-cells`，但直接套在已有的 raw 矩陣上——
秒級而不是重跑 20 分鐘。代價是**繞過 EmptyDrops**（那道用表現譜救回低 UMI barcode 的檢定），
工具會把跟 Cell Ranger 判定的差異列出來讓你判斷。

Judge 預設用 `StubJudge`（不需要模型，只看 status/warnings/errors）。
要接本地模型（任何 OpenAI 相容端點：Ollama、vLLM、llama.cpp）：

```bash
export SCRNA_JUDGE_BASE_URL=http://<host>:11434/v1   # 注意結尾的 /v1
export SCRNA_JUDGE_MODEL=gpt-oss:20b
python -m src.run --judge local ...

# 先確認端點通不通、模型在不在
python scripts/check_judge_endpoint.py
```

實驗室的 DGX 位址放在 `.env.example`，不進 git 的部分放 `.env`。
`gpt-oss:20b` 實測判一步約 12 秒，strict `json_schema` 結構化輸出可用。

⚠️ **模型給的建議數字要當成建議，不是設定值。** 實測 `gpt-oss:20b` 會建議
`max_pct_mito=0.1`——但這個欄位的單位是 0–100 的百分比，照做會砍掉幾乎所有細胞。
judge 不能寫入 `artifacts`（見 `src/nodes.py:109`），這條限制不是形式主義。

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
| `provenance.py` | append-only audit log |

## 下一步

把 skill 一個一個實作掉，順序見 `docs/tool_registry.md` 的 MVP implementation order。
每實作一個，`summarize()` 就會把它從 `scaffolds` 移到 `implemented`。

實作一個 skill 只需要改 `skills/<name>/<name>.py` 的 `run(payload) -> dict`，
不用動 `graph.py` — registry 已經知道要呼叫誰。詳見 `docs/next_step_recommendation.md`。

⚠️ `_generate_skills.py` 只會補沒有的檔案，不會覆寫已存在的（要覆寫得加 `--force`）。
