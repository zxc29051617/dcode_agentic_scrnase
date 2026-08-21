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
# 預設 policy：走到 human gate 就停，不會偷偷放行
python -m src.run --input /path/to/filtered_feature_bc_matrix

# FASTQ 路線，走完整條線（--headless-decision accept 是明確的 opt-in）
python -m src.run --input data/pbmc_1k_v3/pbmc_1k_v3_fastqs --headless-decision accept

# 互動模式：gate 阻塞在終端機的 input()，並寫 checkpoint.sqlite
python -m src.run --input <matrix> --interactive
```

各種分析參數（`--force-cells`、`--min-genes`、`--celltypist-model`…）見
[`workflow.md`](workflow.md#由人決定的參數)。judge 的選擇見
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

## 續跑

兩種，問的是不同的問題，不要混用。見
[`resume.md`](resume.md)。

## exit code

| code | 意思 |
|---|---|
| `0` | `completed`（有產出報告），或 `needs_review`（停在 gate 等人，可以 `--continue-from`） |
| `1` | `failed` —— 過程中有 error |
| `2` | `halted` —— **停了而且沒有產出報告**（例如 QC threshold 沒給、或人選了 stop） |
| `3` | `running` —— 不該漏出來 |
| `4` | `--continue-from` 找不到可以回答的東西 |

`2` 跟 `4` 分開，因為「分析停住」跟「找不到那次執行」是兩種不同的問題。
腳本要問「我拿到報告了嗎」，判斷 exit code 是不是 `0` 就夠。
