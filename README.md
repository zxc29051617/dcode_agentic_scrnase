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

**程式碼**（進 git）
- `src/`：LangGraph orchestrator 實作
- `skills/`：每個 workflow step 一個工具（`SKILL.md` 契約 + Python 實作），26 個，與 `src/registry.py` 一一對應
- `tests/`：452 個測試，`python tests/run_all.py` 全跑
- `scripts/`：維運腳本（取測試資料、連 reference、匯出 graph、查磁碟用量）

**資料與設定**（進 git）
- `prompts/`：judge 的提示詞。`local_judge_base.md` 是共用的，`steps/<step>.md` 是個別步驟的加註
- `marker_db/`：cell type 註解用的 marker 資料庫（scMayoMap，785 KB 純文字）
- `schemas/`：judge / state / output 的 JSON schema
- `docs/`：架構、報告契約、`graph.mmd`（編譯後的 graph 匯出）
- `workflows/`：LangGraph workflow 草圖與版本化設計

**外部大檔**（只有 symlink 和 README 進 git，內容 gitignore）
- `data/`：測試資料集（見 `data/README.md`）
- `reference/`：Cell Ranger 的基因組 reference，20–32 GB（見 `reference/README.md`）
- `tools/`：Cell Ranger 等第三方執行檔（見 `tools/README.md`）

**執行產物**（完全 gitignore）
- `runs/<run_id>/`：每次執行的所有輸出。每一步各存一份 `adata.h5ad` 以支援斷點續跑，
  所以**一次執行約 400 MB**。跑多了要清：`bash scripts/run_disk_usage.sh` 看用量，
  值得留的複製到 `results/` 再把 run 刪掉

> `reference/` 是基因組（幾十 GB、機器相關、不進 git）；`marker_db/` 是細胞型別的
> marker 表（不到 1 MB、進 git）。兩者無關。

## 目前狀態

Orchestrator 可以跑。**26 個 workflow step 全部實作完成**——`skills/` 底下就是這 26 個，一個資料夾一個 step，沒有空殼：

| | |
|---|---|
| ✅ 已實作 | `ingest_validate`、`resolve_reference`、`matrix_preflight`、`fastq_preflight`、`fastq_qc`、`cellranger_count`、`count_matrix_classify`、`load_raw_counts`、`load_filtered_counts`、`cell_calling_review`、`merge_samples`、`post_load_validate`、`run_qc_metrics`、`apply_cell_qc_filter`、`detect_doublets`、`normalize_hvg_prepare`、`run_pca`、`run_integration`、`run_clustering`、`run_umap`、`find_markers`、`annotate_cells`、`cross_check_annotation`、`build_report`、`human_review_decision`、`sample_qc_triage` |

FASTQ 路線：偵測輸入 → 選 reference 並驗證物種 → 結構檢查 → **FastQC/MultiQC 品質評估** → count

**樣本級分流**（選用，預設關閉）。在任何樣本被 count 之前決定哪些進入分析——
Cell Ranger 一個 library 要 20–40 分鐘，讓壞掉的 library 進來比慢更糟。
它只報告不自己刪除（跟 `apply_cell_qc_filter` 同一個形狀）：

```bash
--sample-qc-triage                      # 開啟
# config: qc_metrics_csv / sample_thresholds / exclude_samples
```

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
export SCRNA_JUDGE_MODEL=gpt-oss:120b
python -m src.run --judge local ...

# 先確認端點通不通、模型在不在
python scripts/check_judge_endpoint.py
```

實驗室的 DGX 位址放在 `.env.example`，不進 git 的部分放 `.env`。
模型實測（同一個真實 `apply_cell_qc_filter` payload，都支援 strict `json_schema`）：

| 模型 | 一步耗時 | 品質 |
|---|---|---|
| `gpt-oss:120b` | 100 秒 | 引用數字**並推出結論**（推薦）|
| `gpt-oss:20b` | 74 秒 | 引用數字，不下結論 |
| `medgemma:27b` | 514 秒 | 只是換句話說 warning，且把等待決定的 step 判成 `fail` |

⚠️ **模型給的建議數字要當成建議，不是設定值。** 舊版 prompt 下 `gpt-oss:20b` 曾建議
`max_pct_mito=0.1`——但這個欄位的單位是 0–100 的百分比，照做會砍掉幾乎所有細胞。
judge 不能寫入 `artifacts`（見 `src/nodes.py:109`），這條限制不是形式主義。

判斷品質**取決於 prompt 遠大於取決於模型**。`prompts/local_judge_base.md` 要求每條
理由都要引用 payload 裡的數字；沒有這條要求時，三個模型都只會把 warning 換句話說。

**judge 同時給建議**（`advice`），不是另一個 node。verdict 給機器路由用、
建議給人看，兩者出自同一次呼叫——同一份證據問兩次只會讓 `--judge local`
的時間翻倍。真實資料上它會說：

```
max_pct_mito = 15   [medium]  15% 只移除 72 顆（3.2%），修掉高粒線體尾巴
min_genes    = 1000 [medium]  移除 145 顆（6.5%），落在觀察範圍 14–7919 內
run_umap                      0 條建議 —— 沒有東西要選就不要編
```

⚠️ **建議永遠不會被套用。** judge node 的回傳值只有 `judge_results`
（`src/nodes.py`），沒有任何 key 能讓建議值走到 `artifacts` 或 config。
它出現在 human gate 和報告的 P2,由人決定。

**UMAP/t-SNE 都可以選**。`run_umap` 預設只算 UMAP（讀 clustering 用的 neighbor graph），
t-SNE 直接讀 embedding、不需要先跑過 clustering：

```bash
--embedding-method tsne    # 只算 t-SNE
--embedding-method both    # 兩個都算，互不覆蓋（分別存在 X_umap / X_tsne）
```

**細胞類型標註用 CellTypist，但模型要你選**。用錯組織/物種的模型不會報錯，
只會給你一堆很有自信的錯答案，所以沒指定模型時 `annotate_cells` 什麼都不標，
直接把 61 個候選模型和說明列出來讓你（或之後的 advisor）挑：

```bash
# 先看有哪些模型
python skills/annotate_cells/annotate_cells.py --list-models x

# 決定後
python -m src.run --input <matrix> --celltypist-model Immune_All_Low.pkl
```

會輸出 `cell_type`（每群共識標籤）、`cell_type_per_cell`（逐細胞預測）、
`conf_score`（信心分數），以及把三者畫在 UMAP/t-SNE 上的 PNG。

**報告**。`build_report` 產出 `report.md`（可進 git、agent 好讀）與 `report.html`
（圖內嵌成 data URI，單檔可直接寄給人），兩者由同一份 ReportModel 產生。
分三層 17 個條件式章節——論文主圖 / QC 附錄 / **pipeline 稽核**（每個閾值的來源、
judge verdict、人工決策、套件版本與模型 hash）。條件不成立的章節會寫明原因，
不會靜默消失。詳見 `docs/report_contract.md`。

**磁碟**。每一步各寫一份 AnnData——這是續跑能運作的原因，也是一次執行約 **410 MB**
的原因（雙樣本 PBMC 測試，幾乎全是 `.h5ad`）。沒有任何自動刪除：哪一次執行還值得留
是對工作的判斷，不是對位元組的判斷。

```bash
bash scripts/run_disk_usage.sh        # 各次執行多大、跑完了沒
find runs/<run_id> -name adata.h5ad -delete   # 留報告，丟中間檔（就不能再續跑）
```

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

## 加一個新的 step

26 個 registry step 都已實作，沒有空殼要填。要新增一步，**以現有的 skill 為模板**：

1. **挑一個結構最接近的現有 skill。** 讀矩陣算東西的挑 `run_pca`；要停下來等人
   決定閾值的挑 `apply_cell_qc_filter`；只讀不算的挑 `build_report`
2. **複製整個目錄** — `cp -r skills/run_pca skills/my_new_step`
3. **改名**：目錄名、`.py` 檔名、`TOOL_NAME`
4. **改契約**：`INPUT_FIELDS`（從 payload 的哪些鍵讀，如
   `artifacts.run_pca`、`config.n_comps`）、`OUTPUT_FIELDS`、`_result()` 的欄位、
   `main()` 的 argparse，以及 `SKILL.md`
5. **在 `src/registry.py` 加一個 `StepSpec`**，並補上 `tests/test_my_new_step.py`
   （測試風格見任一個現有的測試檔）

**不用動 `graph.py`** — 接線是從 registry 生成的。加一個 `StepSpec` 會長出
step node 跟 judge node 各一個,以及 `step → judge`、judge 的 `continue` /
`human_gate` 兩條條件邊、還有人工 gate 回到這一步的回程。

`docs/graph.mmd` 是編譯後 graph 的匯出(54 node / 110 edge),改完用

```bash
python scripts/export_graph.py            # 重新產生
python scripts/export_graph.py --check    # 只檢查有沒有過期
```

> 以前有一支 `_generate_skills.py` 產生空殼，已經刪掉。它產出的形狀停在專案早期
> ——例如 `INPUT_FIELDS` 寫的是 `"AnnData"` 這種散文，而現在是
> `"artifacts.normalize_hvg_prepare"` 這種實際的 payload 路徑——所以拿它開新 step
> 得到的骨架是錯的，還要先拆掉。從真的在運作的 skill 複製，形狀永遠是對的，
> 也不會多出一份會跟實作漂移的描述。
