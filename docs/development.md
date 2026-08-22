# 開發

`src/` 的分層與兩個真相來源（`registry.py` / `graph.py`）見
[`architecture.md`](architecture.md#src-分層)。

## 測試

```bash
python tests/run_all.py
```

### 會跑出幾個測試，取決於你手上有什麼資料

**不要期待每台電腦都是同一個數字。** 測試數量固定，但有多少會實際執行，取決於
本機有沒有那幾十 GB 的公開資料集、reference transcriptome 和 Cell Ranger 的輸出。
缺的時候相關測試會**乾淨地跳過（skip）並說明缺什麼**，不會失敗。

| 環境 | 結果 |
|---|---|
| **CI 基準**（GitHub runner，沒有任何大型資料） | **0 fail，跳過的全部是缺資料** |
| 有實驗室資料的機器 | 更多需要資料的測試會真的跑，跳過的數字下降 |

**會變的是通過數，不會變的是那兩件事** —— 沒有 failure，而且每個跳過的測試都指名
它缺哪份資料。這裡刻意不把通過數寫死，理由見 [`decisions.md`](decisions.md)。要看
當下的數字，點 README 上那顆 badge，它讀的是 master 的實際狀態。

CI 會另外檢查**為什麼跳過**：缺資料可以接受，缺套件不行，那會讓 build 失敗 ——
一套大部分沒跑到卻是綠的測試，比紅的更糟。

取測試資料見 [`cli.md`](cli.md#測試資料)。

## 加一個新的 step

26 個步驟都已實作，沒有空殼要填。要新增一步，**以現有的 skill 為模板**：

1. **挑一個結構最接近的現有 skill。** 讀矩陣算東西的挑 `run_pca`；要停下來等人
   決定閾值的挑 `apply_cell_qc_filter`；只讀不算的挑 `build_report`
2. **複製整個目錄** — `cp -r skills/run_pca skills/my_new_step`
3. **改名**：目錄名、`.py` 檔名、`TOOL_NAME`
4. **改契約**：`INPUT_FIELDS`（從傳進來的資料的哪些欄位讀，如
   `artifacts.run_pca`、`config.n_comps`）、`OUTPUT_FIELDS`、`_result()` 的欄位、
   `main()` 的 argparse，以及 `SKILL.md`
5. **在 `src/registry.py` 加一個 `StepSpec`** —— 登記這個步驟的資訊，並補上
   `tests/test_my_new_step.py`（測試風格見任一個現有的測試檔）
6. **在 `src/graph.py` 接線** —— 把它接進實際的流程。這一步不能省略。

只在 `registry.py` 登記是不夠的。漏接不會「自動長出」接線，而是**直接報錯**：

```
AssertionError: registry steps missing from the graph: ['my_new_step']
```

那是 `assert_registry_covered()` 在 `build_graph()` 裡擋下來的，正是為了讓
「註冊了但沒接線」變成明確錯誤而不是無聲的漏接。

要改多少 `graph.py` 取決於這一步怎麼進流程：接在主線尾端用 `linear()` 一行；
自己決定下一步的（像 `count_matrix_classify`）要寫分支判斷式和路徑對照表。
條件分支是一段讀 state 的 Python 判斷式，沒有表格裝得下它。

改完重新匯出 graph：

```bash
python scripts/export_graph.py            # 重新產生 docs/graph.mmd
python scripts/export_graph.py --check    # 只檢查有沒有過期
```

> 不要用已刪除的 `_generate_skills.py` 那類產生器開新 step，理由見
> [`decisions.md`](decisions.md)。

## 模型檢查用的提示詞

`prompts/local_judge_base.md` 是每個步驟共用的；`prompts/steps/<step>.md` 是個別
步驟的加註。要新增一份步驟專屬提示詞，該長什麼樣、為什麼要那樣，見
`prompts/steps/README.md`；已經量測到什麼程度見 `docs/judge_prompt_plan.md`。

## 磁碟

每個步驟都會把當時的 AnnData 保存下來，所以流程中斷後可以從已完成的步驟繼續。
代價是一次執行約 **410 MB**（雙樣本 PBMC 測試，幾乎全是 `.h5ad`）。程式不會自動刪
任何東西：哪一次執行還值得留是對工作的判斷，不是對位元組的判斷。

```bash
bash scripts/run_disk_usage.sh        # 各次執行多大、跑完了沒
find runs/<run_id> -name adata.h5ad -delete   # 留報告，丟中間檔（丟了就不能再繼續跑）
```

一次性的除錯腳本和 run 輸出不進 repo。值得留的複製到 `results/` 再把 run 刪掉。
