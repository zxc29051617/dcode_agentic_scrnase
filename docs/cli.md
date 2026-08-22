# CLI

## 每次執行前

```bash
conda activate dcode-scrna
```

reference 直接放在 `reference/` 底下（實體目錄，不是 symlink —— 專案外的路徑是別人
刪得掉的路徑，理由見 `tools/README.md`）。人類的用
`scripts/build_t2t_chm13_reference.py` 建，見 `reference/README.md`。

## 跑整條 graph

```bash
# 預設行為：需要人決定時就停下來，不會自動放行
python -m src.run --input /path/to/filtered_feature_bc_matrix

# FASTQ 路線，一路跑完不停
#（--headless-decision accept 表示「不經人工確認直接採用」，必須明確指定才會啟用）
python -m src.run --input data/pbmc_1k_v3/pbmc_1k_v3_fastqs --headless-decision accept

# 互動模式：需要確認時直接在終端機問你，並把進度寫進 checkpoint.sqlite
python -m src.run --input <matrix> --interactive
```

各種分析參數（`--force-cells`、`--min-genes`、`--celltypist-model`…）見
[`workflow.md`](workflow.md#由人決定的參數)。要用哪個模型檢查結果見
[`judge_setup.md`](judge_setup.md)。

## 只跑單一 skill，不進 graph

```bash
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
```

## 測試資料

```bash
bash scripts/get_test_data.sh          # 列出需要什麼、有什麼
bash scripts/get_test_data.sh fastq    # 18 GB，FASTQ 路線
cat reference/README.md                # reference 怎麼放、人類的怎麼建
```

## 中斷後繼續

兩種，問的是不同的問題，不要混用。見 [`resume.md`](resume.md)。

## 結束代碼（exit code）

| 代碼 | 意思 |
|---|---|
| `0` | `completed`（有產出報告），或 `needs_review`（停下來等人，可以用 `--continue-from` 回答） |
| `1` | `failed` —— 過程中出錯 |
| `2` | `halted` —— **停了而且沒有產出報告**（例如 QC 閾值沒給、或人選了停止） |
| `3` | `running` —— 不該漏出來 |
| `4` | `--continue-from` 找不到可以回答的東西 |

`2` 跟 `4` 分開，因為「分析停住」跟「找不到那次執行」是兩種不同的問題。
腳本要問「我拿到報告了嗎」，判斷代碼是不是 `0` 就夠。
