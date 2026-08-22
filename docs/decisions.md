# 決策紀錄

這裡收「為什麼現在不是別的樣子」。README 描述現況，這份文件保留曾經踩過的坑，
避免同一個決定被反覆重做。完整的推理在 `git log` —— 這個 repo 的 commit message
是刻意寫長的。

## README 不寫死測試通過數

上一版 README 同時宣稱 452 和 463，兩個都錯。測試數量本來就取決於本機有哪些
資料集，而寫一個「量測當下」的快照只是把過期時間延後而已。現在 README 放 CI badge，
它讀的是 master 的實際狀態；`docs/development.md` 只承諾兩件不會變的事：
沒有 failure，且每個跳過的測試都指名它缺哪份資料。

## 不要用 `micromamba -f conda-lock.yml`

量測過，不是猜的：它會裝完 257 個 conda 套件、靜默跳過 55 個 pip 套件，給你一個
沒有 `langgraph` 也沒有 `scanpy` 的環境——看起來裝好了，跑起來才發現不對。
用 `conda-lock install`，它兩半都裝。詳見 [`environment.md`](environment.md)。

## 已刪除 `_generate_skills.py`

它產生的是空殼骨架，形狀停在專案早期——例如 `INPUT_FIELDS` 寫的是 `"AnnData"`
這種散文，而現在是 `"artifacts.normalize_hvg_prepare"` 這種實際的 payload 路徑。
拿它開新 step 得到的骨架是錯的，還要先拆掉。從真的在運作的 skill 複製，形狀永遠
是對的，也不會多出一份會跟實作漂移的描述。開新 step 的流程見
[`development.md`](development.md#加一個新的-step)。

## 模型的建議永遠不會被自動套用

舊版提示詞下 `gpt-oss:20b` 曾建議 `max_pct_mito=0.1`——但這個欄位的單位是 0–100 的
百分比，照做會砍掉幾乎所有細胞。檢查節點的回傳值只有 `judge_results`
（見 `src/nodes.py:109`），沒有任何欄位能讓建議值走到 `artifacts` 或設定裡。
這條限制不是形式主義。

## `--matrix-kind` 已移除

`count_matrix_classify` 會從矩陣本身判斷是 raw 還是 filtered，不需要人先宣告。

## 文件的資訊架構（2026-08）

README 一度長到 481 行，同時扮演入口頁、操作手冊、開發指南和決策紀錄。現在 README
只回答六個問題（這是什麼／為什麼／怎麼運作／怎麼跑／會得到什麼／深入去哪看），
其餘內容依讀者拆進 `docs/`。內容是搬移不是刪除。

同一輪還做了用詞調整：**專有名詞保留，專案自己的概念講白話。** QC、PCA、UMAP、
AnnData、FASTQ 這些 scRNA-seq 的詞保留英文；judge、verdict、human gate、
provenance、artifact、checkpoint 這些是這個專案自己的概念，README 用白話講，
並在「名詞對照」表裡標出程式碼裡叫什麼，這樣讀者點進 `docs/` 或原始碼不會斷線。
