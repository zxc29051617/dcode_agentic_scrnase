# Next step recommendation

## 已完成

1. ~~建立 conda 環境~~ — `dcode-scrna` 已建好並與 `environment.yml` 同步
2. ~~scaffold LangGraph state / node / edge~~ — `src/` 已可執行，11 項接線測試通過
3. ClawBio 維持為 reference material，沒有複製進來

## 現在的實際狀態

Orchestrator 層是**真的**，分析層是**假的**：

- 路由（FASTQ / matrix、raw / filtered、cell calling）已接好並測過
- 每個 step 後面都有 judge node，verdict 符合 `schemas/judge_result.schema.json`
- human gate 預設不放行 warn/fail，headless 執行預設 `stop`
- provenance 每步寫 JSONL audit log
- **20 個 registry step 裡實作了 2 個**（`ingest_validate`、`fastq_preflight`），
  其餘 18 個 `run()` 還是直接 raise `NotImplementedError`

## 一個待決定的落差

`skills/judge_*/` 有 19 個資料夾，但 **orchestrator 目前不呼叫它們**。
judge 邏輯集中在 `src/judge.py`：一份 contract、一份 prompt、每個 step 傳不同 payload。

這是實作時的取捨 —— 19 個幾乎一樣的 judge 模組不如一份共用契約好維護。
但 `docs/tool_registry.md` 原本的設計是把 judge 也當成 MCP tool。兩條路：

- **維持現狀**：`skills/judge_*/` 是死的，應該刪掉，registry 的 `judge` 欄位當標籤用
- **接回去**：讓 `make_judge_node` 改走 `call_skill`，每個 judge 可以有自己的門檻與 prompt

在實作第一個真的 judge 之前先決定，不然會寫出永遠不會被呼叫的程式碼。

## 下一步

一次實作一個 skill，順序照 `docs/tool_registry.md` 的 MVP implementation order：

1. ~~`ingest_validate`~~ — 已完成，對 `pbmc_1k_v3` 官方測試集驗證過
2. ~~`fastq_preflight`~~ — 已完成，對 `pbmc_1k_v3` 驗證過（R1=28bp 正確判成 SC3Pv3）；
   缺 reference 時會真的擋在 human gate，不是假裝通過
3. `cellranger_count` — 真的呼叫 `/home/zxc29051617/projects/cellranger-10.1.0/bin/cellranger`；
   需要先準備一份參考基因體（`fastq_preflight` 只檢查路徑存在 + `reference.json`，
   不驗證內容完整性）
4. `count_matrix_classify` — raw / filtered 的分流靠它；`ingest_validate` 已經給了
   `matrix_kind_hint` 和 `matrix_path`，這一步負責把 hint 變成決定
5. `load_filtered_counts` — 最短的一條可跑通的真實路徑
6. `run_qc_metrics` — 第一個有真正 metrics 可以給 judge 評的 step

做到第 6 個，就有一條 `FASTQ -> count -> QC -> 真 judge` 的端到端真實路徑，
那時候才值得把 judge 從 stub 換成本地模型。

實作完 4 和 5 之後，`src/run.py` 的 `--matrix-kind` 和 `--cell-calling-resolved`
兩個 scaffold fallback 就可以拿掉，`graph.py` 裡對應的 config fallback 也是。

## 實作一個 skill 的契約

`skills/<name>/<name>.py` 的 `run(payload) -> dict`：

- payload：`{step, run_id, config, input_bundle, sample_metadata, artifacts}`
- 回傳 dict，可含 `warnings` / `errors` / `metrics`，其餘欄位照 `SKILL.md` 定義
- 回傳的 dict 會存進 `state["artifacts"][step]`，下游 step 從 `artifacts` 讀

不需要改 `graph.py`：registry 已經知道要呼叫誰。

## MVP scope（不變）

- FASTQ
- Count matrix
- raw / filtered matrix split
- local judge after each meaningful step
- human gate for warn/fail cases
