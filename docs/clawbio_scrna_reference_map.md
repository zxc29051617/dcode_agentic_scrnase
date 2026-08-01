# ClawBio scRNA reference map

這份文件不是把 ClawBio 複製進來，而是把它的 scRNA 相關能力拆成我們這個專案可用的 workflow 參考。

## 結論

在 `dcode_agentic_scrnaseq/` 裡，ClawBio 的內容應該被當成：

- **pipeline reference**：拿它的流程分工、輸入型態、輸出契約
- **node reference**：拿它的 step 邊界與判斷點
- **not a separate skill runtime**：不要在這個專案裡再建一套 ClawBio 式 skill 系統

## 對應到本專案的建議

### 1. FASTQ 上游 preprocessing
對應 ClawBio：`nfcore-scrnaseq-wrapper`

可借用的概念：
- samplesheet / references preflight
- upstream preprocessing from FASTQ
- count matrix / preferred h5ad 的產生
- provenance bundle

在本專案裡的角色：
- 作為 **FASTQ 主入口的上游子流程**
- 不把它混進 downstream Scanpy 主線

### 2. sample-level QC triage
對應 ClawBio：`sample-qc-triage`

可借用的概念：
- sample QC metrics 的 deterministic triage
- 多樣本時的前置 outlier 檢查
- 低品質 sample 在主流程前先攔下來

在本專案裡的角色：
- **optional pre-route**
- 只有多 sample 或有 QC summary table 時才啟用

### 3. downstream Scanpy mainline
對應 ClawBio：`scrna-orchestrator`

可借用的概念：
- QC
- cell QC filter
- doublet detection
- normalization / HVG / PCA
- clustering
- markers
- annotation
- report

在本專案裡的角色：
- 這就是我們現在 MVP 的 **主線 pipeline**
- 對應 `FASTQ -> count matrix -> raw/filtered matrix -> judge -> human gate -> report`

### 4. latent integration branch
對應 ClawBio：`scrna-embedding`

可借用的概念：
- scVI / latent embedding
- batch-aware integration
- `X_scvi` / `integrated.h5ad`

在本專案裡的角色：
- 這是 **後續擴充分支**
- 先不放進 MVP 主入口，避免一開始把 workflow 搞太寬

## MVP 的實作優先順序

1. `FASTQ` / `Count matrix` 兩條主入口
2. `Count matrix` 再分 `raw` / `filtered`
3. 每個 stage 後面加 local judge
4. 把 human gate 接好
5. 再考慮 latent integration branch

## 實作建議

- **workflow 層**：LangGraph state machine
- **分析層**：Scanpy / deterministic Python
- **評斷層**：local LLM judge
- **參考層**：這份文件 + ClawBio docs

## 目前可直接用的 ClawBio 參考項

- `nfcore-scrnaseq-wrapper`：FASTQ 上游 preprocessing
- `sample-qc-triage`：sample-level QC triage
- `scrna-orchestrator`：Scanpy 下游主線
- `scrna-embedding`：scVI latent branch
