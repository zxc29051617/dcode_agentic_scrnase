# Web

## 兩種啟動方式，不要混用

CLI 和 Web 是兩種**不同的互動模型**，不是同一件事的兩個入口。

| | `python -m src.run --interactive` | Web `/analysis/new` |
|---|---|---|
| 誰組出參數 | 你自己打旗標 | 對話整理成 draft，你在畫面上確認 |
| 需要確認時怎麼問 | 直接在終端機問你 | 執行掛起，進度留在磁碟，之後從瀏覽器回答 |
| 誰在等 | 你的終端機一直開著 | 沒有人在等，worker 之後才接手 |
| 誰做的決定 | `getpass.getuser()` | 由 server 端判斷，前端不能自稱是誰 |

**CLI 完全沒有改變**，[`cli.md`](cli.md) 講的都還算數。網頁是加上去的一層，它自己
不執行任何分析：controller 只做驗證和排程，真正跑流程的是 `dcode-scrna` 環境裡的
worker，而 worker 走的是同一個 `src/graph.py`。

## 最小可複製的 Web 啟動

四個東西，三個環境。照順序來：

```bash
# 1. controller（自己的 venv，有 FastAPI、沒有 scanpy）
cd services/controller
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. 資料白名單：哪些路徑允許被分析請求指名
cp config/dataset_catalog.example.json config/dataset_catalog.json
#    改成你自己的路徑。這個檔決定 model 和瀏覽器能指名什麼，
#    而且每個路徑還要通過 CONTROLLER_DATA_ROOTS 的檢查

# 3. gateway + controller + web 一起起來
cd apps/web
cp .env.local.example .env.local   # 設 GATEWAY_URL 和 ANALYSIS_CONTROLLER_URL
conda activate copilotkit-web
npm install && npm run dev:stack

# 4. worker —— 另一個 terminal，在科學環境裡
conda activate dcode-scrna
CONTROLLER_DB=var/controller/controller.sqlite CONTROLLER_RUNS_ROOT=runs \
  python -m services.controller.worker
```

然後開 `http://127.0.0.1:3000/analysis/new`。

`dev:stack` 刻意**不**幫你起 worker：worker 會 import 整個分析程式，把 scanpy 塞進
前端的行程樹裡沒有道理。沒裝 controller 的話整個站台就退回唯讀，頁面上會直說。

## 限制

網頁這一層是**本機開發用的 MVP**：SQLite、輪詢、**沒有登入驗證**，這些限制在
`services/controller/README.md` 裡列得很清楚，不要對外開放那個連接埠。

細節看 `services/controller/README.md` 和分析請求的 API 規格
[`analysis_request_contract.md`](analysis_request_contract.md)。
