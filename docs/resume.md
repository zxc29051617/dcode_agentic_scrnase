# 兩種續跑，問的是不同的問題

名字很像，做的事完全不同，**不要混用**：

| | 問題 | 依據 | 什麼時候用 |
|---|---|---|---|
| `--resume-from RUN_ID` | 哪些**結果**還有效？ | 磁碟上的 artifact、`run_metadata.json`、audit log | 改了參數或資料，想重跑但不想從頭 |
| `--continue-from RUN_ID` | 這次執行**停在哪裡**？ | `runs/<run_id>/checkpoint.sqlite` | 上次停在 human gate，要回答它 |

```bash
# 改了 QC threshold，重跑受影響的部分，前面沒受影響的沿用
python -m src.run --input <matrix> --resume-from 20260810T065058Z-f34afde0 --min-genes 200

# 上次 --interactive 停在 gate，process 已經關掉了，現在回來回答
python -m src.run --continue-from 20260810T065058Z-f34afde0 --interactive
```

## `--resume-from` 怎麼決定沿用什麼

它會算出一個 **cut**：最早不能再信任的 step，由 `registry.earliest_step_reading()`
從改動的 config key 反查得出。cut 從哪裡切，決定了什麼能沿用——兩個例子切在很不
一樣的地方：

| 改了什麼 | cut（從這裡重跑） | 沿用 | PCA / clustering |
|---|---|---|---|
| `--min-genes` | `apply_cell_qc_filter` | 它以前的每一步，含 `cellranger_count` | **都要重跑** |
| `--celltypist-model` | `annotate_cells` | 它以前的每一步 | 沿用 |

改 QC 閾值省下的是 count 那 20–40 分鐘，**不是** PCA 和 clustering——濾掉的細胞
不同，後面每一步的輸入就都不同了。只有改 `--celltypist-model` 這種下游的旋鈕才
沿用得到 embedding。改輸入資料則整份重跑。

## `--continue-from` 的前提

只有 `--interactive` 跑過的 run 才有 checkpoint 可以接（那是唯一會寫
`checkpoint.sqlite` 的模式）。找不到就明確報錯，**絕不從頭重跑**，並以
exit code `4` 結束。
