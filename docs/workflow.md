# Workflow

26 個 workflow step 全部實作完成——`skills/` 底下就是這 26 個，一個資料夾一個 step，
沒有空殼。順序由 `src/graph.py` 固定，不是由模型決定。

| | |
|---|---|
| ✅ 已實作 | `ingest_validate`、`resolve_reference`、`matrix_preflight`、`fastq_preflight`、`fastq_qc`、`cellranger_count`、`count_matrix_classify`、`load_raw_counts`、`load_filtered_counts`、`cell_calling_review`、`merge_samples`、`post_load_validate`、`run_qc_metrics`、`apply_cell_qc_filter`、`detect_doublets`、`normalize_hvg_prepare`、`run_pca`、`run_integration`、`run_clustering`、`run_umap`、`find_markers`、`annotate_cells`、`cross_check_annotation`、`build_report`、`human_review_decision`、`sample_qc_triage` |

## 輸入怎麼進來

`--input` 給什麼由 `ingest_validate` 自己偵測（FASTQ / MTX / .h5 / .h5ad）。
`--species` 決定用哪份 reference 和 QC 常數（`--reference` 可以明確覆寫）；
物種和 reference 對不上會在第二步就停下來，不會等 count 跑完才發現。

`--matrix-kind` 已經不需要了——`count_matrix_classify` 會從矩陣本身判斷 raw/filtered。

**FASTQ 路線**：偵測輸入 → 選 reference 並驗證物種 → 結構檢查 →
**FastQC/MultiQC 品質評估** → count。

`fastq_qc` 用 `shutil.which("fastqc")` 找 FastQC。**找不到時最常見的原因是環境沒
activate** —— 這些執行檔在 env 的 `bin/` 裡，不在系統 PATH 上；直接呼叫
`<env>/bin/python` 而不 activate 也一樣找不到。真的缺了也不會擋住流程
（`fastq_qc` 記一筆 warning 就往下走到 `cellranger_count`，Cell Ranger 自己的
web_summary 仍然有 Q30 和 mapping rate），但 `DEFAULT_POLICY` 的
`autocontinue_on_warn=False` 會讓那個 warning 停在 human gate。

## 樣本級分流（選用，預設關閉）

在任何樣本被 count 之前決定哪些進入分析——Cell Ranger 一個 library 要 20–40 分鐘，
讓壞掉的 library 進來比慢更糟。它只報告不自己刪除（跟 `apply_cell_qc_filter`
同一個形狀）：

```bash
--sample-qc-triage                      # 開啟
# config: qc_metrics_csv / sample_thresholds / exclude_samples
```

## 多樣本

每個樣本各自 count → 各自載入 →（raw 路線各自決定細胞數）→ `merge_samples`
合併成一個 AnnData 並加 `sample` 標籤 → 之後共用同一條主線。
細胞數可以統一給一個值，也可以逐樣本給：

```bash
--force-cells 1500                      # 每個樣本都留 1500
--force-cells '{"A": 1500, "B": 2400}'  # 逐樣本（config 用 dict）
```

## 由人決定的參數

程式碼裡沒有預設閾值。以下每一項都是「先給證據，再停下來」。

### 細胞數（raw 矩陣路線）

`cell_calling_review` 會先給你證據（斷崖位置、各個候選細胞數對應的 UMI 門檻）
然後停下來，不會替你挑數字：

```bash
# 第一次：看證據
python -m src.run --input <raw_feature_bc_matrix.h5>

# 決定後：--force-cells 留前 N 個，或 --min-umi 設門檻
python -m src.run --input <raw_feature_bc_matrix.h5> --force-cells 1500
```

`--force-cells` 等同 Cell Ranger 的 `--force-cells`，但直接套在已有的 raw 矩陣上——
秒級而不是重跑 20 分鐘。代價是**繞過 EmptyDrops**（那道用表現譜救回低 UMI barcode
的檢定），工具會把跟 Cell Ranger 判定的差異列出來讓你判斷。

### QC 閾值

沒給閾值時 `apply_cell_qc_filter` 會列出每個候選值會濾掉多少細胞，然後停下來：

```bash
# 第一次：看證據
python -m src.run --input <matrix>

# 決定後
python -m src.run --input <matrix> --min-genes 200 --max-pct-mito 15
```

發表論文常見的 200 / 20% 是特定組織、特定 protocol 的值。你自己的標準值放 config，
不要放程式碼——config 裡的值會進 audit log，程式碼裡的預設不會。

### Doublet

**預設只標記不刪除**。`detect_doublets` 每個 library 各自跑 Scrublet
（doublet 只在同一個 GEM well 裡形成），expected rate 從 10x 的 loading table 推，
不用 Scrublet 那個假設回收 8,000 顆細胞的 0.06 預設：

```bash
--remove-doublets                       # 真的刪掉（預設只加 obs 欄位）
--expected-doublet-rate 0.05            # 覆寫推算值
```

### Embedding

`run_umap` 預設只算 UMAP（讀 clustering 用的 neighbor graph），
t-SNE 直接讀 embedding、不需要先跑過 clustering：

```bash
--embedding-method tsne      # 只算 t-SNE
--embedding-method both      # 兩個都算，互不覆蓋（分別存在 X_umap / X_tsne）
--embedding-dimensions 2 3   # 同時產生 2D 和 3D（X_*_3d）
--embedding-max-cells 50000  # browser display 上限；完整結果仍保留在 AnnData
```

### 細胞類型標註

用 CellTypist，但**模型要你選**。用錯組織/物種的模型不會報錯，只會給你一堆很有自信
的錯答案，所以沒指定模型時 `annotate_cells` 什麼都不標，直接把 61 個候選模型和說明
列出來讓你（或之後的 advisor）挑：

```bash
# 先看有哪些模型
python skills/annotate_cells/annotate_cells.py --list-models x

# 決定後
python -m src.run --input <matrix> --celltypist-model Immune_All_Low.pkl
```

會輸出 `cell_type`（每群共識標籤）、`cell_type_per_cell`（逐細胞預測）、
`conf_score`（信心分數），以及把三者畫在 UMAP/t-SNE 上的 PNG。

## 報告

`build_report` 產出 `report.md`（可進 git、agent 好讀）與 `report.html`
（圖內嵌成 data URI，單檔可直接寄給人），兩者由同一份 ReportModel 產生。
分三層 17 個條件式章節——論文主圖 / QC 附錄 / **pipeline 稽核**（每個閾值的來源、
judge verdict、人工決策、套件版本與模型 hash）。條件不成立的章節會寫明原因，
不會靜默消失。

完整契約見 [`report_contract.md`](report_contract.md)。
