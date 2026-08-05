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
- **25 個 registry step 裡實作了 22 個**，其餘 3 個還是 `NotImplementedError`
- FASTQ 上游整段 + raw/filtered 分流 + 載入 + cell calling + 匯流標準化都是真的
- 兩條路各有入口檢查：`resolve_reference`（FASTQ）與 `matrix_preflight`（矩陣），
  各自用該路線有的證據驗物種，並輸出同一組 QC 常數
- `post_load_validate` 是匯流點：統一 AnnData、建立 counts layer、最後一次驗物種
- **細胞數由操作者決定**：`cell_calling_review` 給證據後停下來，不自己挑數字

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
3. ~~reference 接進專案~~ — 已完成。`scripts/link_reference.sh` 用 symlink 把
   reference 放進 `reference/`，`src/species.py` 是物種對照表，
   `resolve_reference` 負責解析 + 驗證物種對不對得上
4. ~~`cellranger_count`~~ — 已完成。cellranger 路徑自動尋找，
   `_assert_same_reference` 已移植：拒絕沿用「用別份 reference 算出來的」既有 matrix
5. ~~`count_matrix_classify`~~ — 已完成。從矩陣本身（空 barcode 數）判斷而不是看檔名，
   hint 對不上就停。對真實 pbmc 的 raw/filtered 兩個矩陣都驗證過
6. ~~`load_raw_counts` / `load_filtered_counts` / `cell_calling_review`~~ — 已完成。
   AnnData 以檔案路徑在 step 之間傳遞（見 `src/matrix_io.py`）
7. ~~`merge_samples` / `post_load_validate`~~ — 已完成。多樣本在這裡合併成一個
   帶 `sample` 標籤的物件，之後共用一條主線
8. ~~`run_qc_metrics`~~ — 已完成。第一個有真正 metrics 可以給 judge 評的 step。
   對 `pbmc_1k_v3` 驗證：median genes/cell 跟 Cell Ranger 自己報的 3,201 完全一致

現在已經有一條 `FASTQ -> count -> cell calling -> merge -> QC -> 真 judge` 的
端到端路徑，**接下來值得先把 judge 從 stub 換成本地模型**，因為後面的
QC filter / doublet / normalize / PCA / clustering 這些空殼步驟開始有真正的數值
可以評分了，先把評分機制接上再繼續填空殼會更有效率。

## 下一批要填的空殼（Scanpy 主線）

`apply_cell_qc_filter` → `detect_doublets` → `normalize_hvg_prepare` → `run_pca`
→ `run_integration` → `run_clustering` → `run_umap` → `find_markers` →
`annotate_cells` → `human_review_decision` → `build_report`，共 11 個。

~~`apply_cell_qc_filter`~~ 已完成——跟 `cell_calling_review` 同一個形狀：沒給閾值就
列證據然後停，程式碼裡沒有預設值。真實資料驗證過 `max_pct_mito=5` 會砍掉 55% 的細胞
（因為合併後的中位數就是 5.4），而且對兩個 chemistry 影響差很多。

~~`detect_doublets`~~ 已完成——但形狀跟前兩個**不一樣**，值得記下為什麼：

- `cell_calling_review` / `apply_cell_qc_filter` 沒有人決定就**沒有輸出**，所以會停
- `detect_doublets` 一定有輸出（標記好的 AnnData），刪不刪是另一個問題，所以**不擋流程**

expected doublet rate 從 10x 的 loading table 推（每 1,000 顆細胞 0.76%），
不是 Scrublet 那個假設回收 8,000 顆的 0.06 預設——對 1,200 顆的 library
那個預設會多找 7 倍的 doublet。每個 library 各自跑（doublet 只在同一個 GEM well 形成）。
真實資料：v2 11/1,015、v3 11/1,218，兩邊都貼近 loading 預測的值。

~~`normalize_hvg_prepare`~~ 已完成——跟 doublet 一樣有合理預設、不擋流程：
`seurat_v3` 選 2,000 個 HVG（Scanpy/Seurat 共同的標準預設，不是借用別人組織的閾值），
有 `sample` 欄位時用 `batch_key` 避免把 batch 差異誤判成生物訊號。

實作時撞到一個真的穩定性問題：`seurat_v3` 的 loess fit 在基因數太少時
（graph fixture 過濾後只剩 6 個基因）**不一定乾淨地丟例外**——同樣的退化資料，
單獨跑是 `ValueError`，但在完整測試套件裡直接讓直譯器 core dump，`try/except`
接不住。修法是在呼叫前先檢查基因數（`MIN_GENES_FOR_SEURAT_V3=50`），
不夠就自動退回數值上更穩的 `flavor="seurat"` 並記錄警告。這不只是測試 fixture
的邊角案例——小型的 targeted panel（例如 spatial 的幾百個基因）在真實資料上
也會踩到。

~~`run_pca`~~ 已完成——同樣不擋流程，50 個成分是 Scanpy/Seurat 共同的標準預設。
只在 `highly_variable` 標記的基因上 fit（`mask_var`），但 embedding 跟 loadings
仍然覆蓋所有細胞跟所有基因，跟 HVG 步驟「標記不刪除」的原則一致。

成分數在呼叫前就先夾住（跟 `detect_doublets` 的 `_components_for` 同一個思路）：
`arpack` solver 對超過 rank 的請求會直接丟例外，所以先算 `min(n_obs, n_genes_used)-1`
再送進去，而不是先丟給它再處理錯誤。

~~`run_integration`~~ 已完成——第一個「是否執行」而不只是「用什麼參數」的主線步驟：
單樣本或某個樣本細胞數太少（<20）就跳過，不當成需要人決定的閾值。用 Harmony 校正
`X_pca` → `X_pca_harmony`，`X` 跟原始 counts 都不動。

實作時撞到一個真的跨版本相容性 bug：scanpy 內建的 `harmony_integrate` 包裝函式
寫死假設 `harmonypy.run_harmony` 回傳的 `Z_corr` 是 `(n_pcs, n_obs)`，但這只在
`harmonypy 0.0.10` 成立——0.1.0 之後（包括現在的 2.0.0）全部改成 `(n_obs, n_pcs)`,
包裝函式的 `.T` 因此把方向轉錯，assign 進 `obsm` 時噴 shape 錯誤，跟資料本身無關。
沒有去鎖死一個舊版本套件，而是自己呼叫 `harmonypy`、依實際回傳的 shape 判斷方向——
不管裝的是哪個版本都正確。

~~`run_clustering`~~ 已完成——resolution 預設 1.0（Scanpy 自己的預設），可調整
（`config.resolution`），不擋流程。讀 `run_integration` 記錄的 `embedding_key`
而不是寫死 `X_pca`，所以不管有沒有做過批次校正都讀對物件。用 `flavor="igraph"`
而不是 `sc.tl.leiden` 自己的預設 `"leidenalg"`——Scanpy 文件現在建議的做法，
給定種子後結果是決定性的，leidenalg 那條路不是。

~~`run_umap`~~ 已完成——`config.method` 可選 `umap`（預設）/ `tsne` / `both`。
兩個方法讀的東西不一樣：UMAP 讀 `run_clustering` 建好的 neighbor graph（保證
UMAP 圖跟 cluster label 用同一個鄰居結構）；t-SNE 直接讀 `embedding_key`，
不需要先跑過 clustering。t-SNE 的 perplexity 在呼叫前就先夾住
（`min(30, (n_obs-1)//3)`，sklearn 自己的經驗法則），避免 `perplexity >= n_samples`
直接丟例外。

~~`find_markers`~~ 已完成——第一個讀 `X`（表現量）而不是 embedding 的下游步驟。
用 `wilcoxon`（scanpy 教學建議，非其預設的 t-test），**測全部基因而不只是 HVG**——
這正是 `normalize_hvg_prepare` 當初「只標記不刪除」換來的：canonical marker 不一定
在變異度前 2,000 名內。

一個關鍵的 crash guard：只要有任何一個 cluster 只有 1 顆細胞，scanpy 會直接
abort **整個** ranking（不是只跳過那一群），所以呼叫前就先把太小的 cluster 從
`groups=` 排除。

完整表格（326,775 列）寫到 CSV，state 裡只留每群前 25 個——大結果走路徑、
摘要走 state，跟 AnnData 一樣的規則。

**真實資料驗證是目前最強的一次**：15 個 cluster 的 top marker 全部對得上教科書
PBMC 族群（S100A8/A9=monocyte、GNLY/NKG7=NK、MS4A1/CD79A=B、FCGR3A=CD16+ mono、
TCF4=pDC、TUBB1=platelet、KLRB1/SLC4A10=MAIT）。這代表前面每一步都對。

~~`annotate_cells`~~ 已完成——只用 CellTypist（依使用者決定，因為之後要接 agent
討論選哪個 reference 模型，marker 對照表用人工反而難解釋）。

三個關鍵設計：

1. **模型是決定，不是預設**。61 個模型各自針對特定組織/物種，用錯不會報錯只會
   給自信的錯答案，所以沒指定就什麼都不標、把候選清單當 evidence 列出來然後停
   （跟 `apply_cell_qc_filter` 同一個形狀）。這份清單正好就是之後 advisor 要讀的東西。
2. **表現量要重新正規化到 10,000**。CellTypist 明確要求 log1p + 每細胞 10,000 counts，
   而我們主線是 median depth（真實資料是 6,780）。直接餵進去它只會印一行 warning
   然後照樣回傳看起來正常的預測——這正是 `post_load_validate` 堅持保留 counts layer
   換來的：從 counts 重建一份給 CellTypist 用，主線 `X` 不動。
3. **majority voting 跑在我們自己的 Leiden 上**，不是 CellTypist 自己的 over-clustering，
   這樣標籤跟 `find_markers` 的每群表格、跟報告裡的 UMAP 都對得起來。

**獨立驗證**：CellTypist 訓練自外部參考資料、完全沒看過我們的 marker，但 15 個 cluster
全部跟 `find_markers` 的結果一致（XBP1→Plasma cells、SLC4A10→MAIT、TUBB1→platelets）。

**下一個是 `build_report`**，但**先補齊了它需要的 provenance**（見 `docs/report_contract.md`）。

盤點文獻圖表慣例後發現 6 個「事後無法重建」的缺口，全部先補完才動報告：

1. **run metadata** — 完全沒有記錄。改在 run 開始時寫（不是報告時現查，那是不同的環境），
   含 git commit **與 dirty 狀態**、seed、15 個套件版本
2. **random seed** — 6 個步驟都在用，但沒有一個記錄下來，報告連 seed=0 都寫不出來
3. **barcode-rank 曲線** — 只存了 knee/inflection 純量，曲線畫不出來。改存完整排序後的
   UMI 向量（**不是降採樣**：log-log 曲線上均勻抽點會全落在平坦長尾）。
   真實資料 329,735 個 barcode 壓縮後只有 4.3 KB
4. **QC 逐細胞旗標** — 只留整數計數，拆不回重疊。真實資料：26 個 min_genes 失敗、
   72 個 mito 失敗、共移除 74 個——**其中 24 個同時違反兩者**，所以 min_genes
   其實只獨立砍掉 2 顆細胞，這在只有計數時完全看不出來
5. **整合前 embedding** — 原本打算在 build_report 重算，這是錯的：UMAP 有種子、
   鄰居數、套件版本，報告階段重算會跟它宣稱描述的那次執行漂移。改由 `run_umap` 存
6. **模型與方法身分** — CellTypist 只記檔名（模型會原地改版），補上 SHA-256

報告分三層（論文主圖 / QC 附錄 / pipeline 稽核），12 個**條件式**圖組，
`report.md` 與 `report.html` 共用同一份 ReportModel。詳見 `docs/report_contract.md`。

`--matrix-kind` 和 `--cell-calling-resolved` 兩個 scaffold fallback 都已經移除；
分支現在讀的都是上游 step 真正做出的決定，這條路上沒有 config fallback 了。

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
