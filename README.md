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
- `src/`：LangGraph orchestrator 實作
- `skills/`：每個 step 一個工具（`SKILL.md` 契約 + Python 實作）
- `schemas/`：judge / state / output 的 JSON schema
- `prompts/`：各 step 的 local judge prompt
- `workflows/`：LangGraph workflow 草圖與版本化設計

## 目前狀態

Orchestrator 可以跑。Skill **20 個裡實作了 2 個**：

| | |
|---|---|
| ✅ 已實作 | `ingest_validate`、`fastq_preflight` |
| ⬜ scaffold | 其餘 18 個，`run()` 直接 raise `NotImplementedError` |

Scaffold 不會讓流程崩潰，會被標成 `status="scaffold"`，summary 裡的 verdict 也寫成
`"pass (scaffold)"`、score 0，不會被誤讀成真的通過。

## 怎麼跑

```bash
conda activate dcode-scrna

# 預設 policy：走到 human gate 就停，不會偷偷放行
python -m src.run --input /path/to/filtered_feature_bc_matrix

# 走完整條線（--headless-decision accept 是明確的 opt-in）
python -m src.run --input ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs --headless-decision accept

# 只跑單一 skill，不進 graph
python skills/ingest_validate/ingest_validate.py ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs
python skills/fastq_preflight/fastq_preflight.py ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs --reference <path>

# FASTQ 路線需要 --reference 才能過 fastq_preflight（沒有的話會在 human gate 停下）
python -m src.run --input ~/data/pbmc_1k_v3/pbmc_1k_v3_fastqs --reference <path> --headless-decision accept

# 測試
python tests/run_all.py
```

`--input` 給什麼由 `ingest_validate` 自己偵測（FASTQ / MTX / .h5 / .h5ad）。
`--matrix-kind`、`--cell-calling-resolved` 只是 `count_matrix_classify` 和
`load_raw_counts` 還是 scaffold 期間的 fallback，那兩個實作掉之後就可以拿掉。

Judge 預設用 `StubJudge`（不需要模型，只看 status/warnings/errors）。
要接本地模型：

```bash
export SCRNA_JUDGE_BASE_URL=http://localhost:11434/v1
export SCRNA_JUDGE_MODEL=qwen2.5:7b-instruct
python -m src.run --judge local ...
```

## src/ 分層

| 檔案 | 負責 |
|---|---|
| `registry.py` | 有哪些 step、對應哪個 skill、誰來 judge |
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
