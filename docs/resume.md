# 兩種「繼續執行」，問的是不同的問題

名字很像，做的事完全不同，**不要混用**：

| | 問題 | 依據 | 什麼時候用 |
|---|---|---|---|
| `--resume-from RUN_ID` | 哪些**結果**還有效？ | 磁碟上已產生的結果檔、`run_metadata.json`、執行紀錄 | 改了參數或資料，想重跑但不想從頭 |
| `--continue-from RUN_ID` | 這次執行**停在哪裡**？ | `runs/<run_id>/checkpoint.sqlite`（中斷時保存的進度） | 上次停下來等人確認，現在要回答它 |

```bash
# 改了 QC threshold，重跑受影響的部分，前面沒受影響的沿用
python -m src.run --input <matrix> --resume-from 20260810T065058Z-f34afde0 --min-genes 200

# 上次 --interactive 停下來等人確認，終端機已經關掉了，現在回來回答
python -m src.run --continue-from 20260810T065058Z-f34afde0 --interactive
```

## `--resume-from` 怎麼決定沿用什麼

它會算出一個**切點**（程式裡叫 cut）：最早不能再信任的步驟，由
`registry.earliest_step_reading()` 從你改動的設定項反查得出。切點在哪裡，決定了
什麼能沿用——兩個例子切在很不一樣的地方：

| 改了什麼 | 切點（從這裡重跑） | 沿用 | PCA / clustering |
|---|---|---|---|
| `--min-genes` | `apply_cell_qc_filter` | 它之前的每一步，含 `cellranger_count` | **都要重跑** |
| `--celltypist-model` | `annotate_cells` | 它之前的每一步 | 沿用 |

改 QC 閾值省下的是 count 那 20–40 分鐘，**不是** PCA 和 clustering——濾掉的細胞
不同，後面每一步的輸入就都不同了。只有改 `--celltypist-model` 這種下游的旋鈕才
沿用得到 embedding。改輸入資料則整份重跑。

## `--continue-from` 的前提

只有 `--interactive` 跑過的執行才有進度可以接（那是唯一會寫 `checkpoint.sqlite`
的模式）。找不到就明確報錯，**絕不從頭重跑**，並以結束代碼 `4` 結束。
