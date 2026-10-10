# 真實環境驗證：2026-09-23

使用者已填寫本專案 `.env` 並要求提交 Git 後驗證所有流程。設定檢查四項均為 SET；`.env` 權限為 0600，Git 忽略規則有效，待提交檔案掃描未發現已設定的憑證值。

## 已驗證

| 項目 | 結果 |
| --- | --- |
| 自動化測試 | 修正後 245 passed，14.11 秒，零失敗、零跳過 |
| Python 編譯／相依套件 | compileall 通過；pip check 無相依衝突 |
| 文件檢查 | 9/9 工作項目文件檢查通過；不代表真實部署完成 |
| Binance 現貨／衍生品 | HTTP 200；120 日 K、180 根 4 小時 K、200 根小時 K、21 筆 funding、192 筆 OI |
| OKX | HTTP 200，取得與 Binance 同一分鐘的現貨價格 |
| RSS | 修正後 CoinDesk 16 筆、Cointelegraph 18 筆、Fed 2 筆，健康狀態皆 ok |
| 官方事件日曆 | 修正後 CPI 13 筆、FOMC 57 筆，健康狀態皆 ok；含歷史與未來日期 |
| Jev | 真實 jev-1.13.0 三次請求均 HTTP 200；兩批新聞分類及 3／7 天趨勢回應均通過結構驗證 |
| SQLite | 真實新聞及日曆已存入正式資料庫；驗證預測／模型階段存入獨立驗證資料庫 |
| 郵件呈現 | 繁體中文 HTML／純文字與 MIME 產生成功 |
| Gmail | SMTP 接受一封 `[驗證用]` 郵件；使用者確認已收到且內容正常 |
| 寄送窗口 | 真實 `send --due` 在窗口外返回 `no eligible delivery`，未另寄正式日報 |
| 結算入口 | 真實 `evaluate` 返回 `settled=0`；目前沒有已到期的正式預測 |
| systemd 靜態驗證 | 六份 service／timer render 及 systemd-analyze verify 通過 |

初次即時驗證時間為台北 08:39，市場截止為 08:39，不冒充正式 07:30 日報。測試信與預測不加入正式績效。第一封信正確標示 CoinDesk／FOMC 缺漏；修正後只重新抓取來源，未額外呼叫模型或重寄郵件。

Jev usage：news-1 為 11,172 input／1,965 output；news-2 為 11,405／1,953；trend 為 9,883／89。合計 32,460 input、4,007 output tokens；未推算帳戶費用。

私有驗證產物位於 `data/validation/20260923T003916Z/`，包括 `results.json`、`validation.sqlite3`、`report.html`、`report.txt`、`message.eml`。全部由 Git 忽略；結果保留初次驗證當時的缺漏，不回寫為修正後結果。

## 真實驗證發現並修正

1. CoinDesk 帶尾端斜線的 RSS URL 回傳 308，導向同站不帶斜線的 URL。更新固定來源及規格為回傳 200 XML 的正式網址，維持不自動跟隨重新導向的既有政策。
2. FOMC 官方頁面包含 `Apr/May`、`Jan/Feb` 等月份縮寫，以及歷史日期 `22 (notation vote)`，原解析器會拒絕整頁。加入月份縮寫與已知註記解析；notation vote 保留獨立名稱，未知註記仍拒絕。

新增五項測試，其中四項先觀察到失敗，再修正至通過；一項確認未知註記繼續被拒絕。修正後重新執行真實 collect-context，五個來源全部 ok。獨立唯讀審查未發現重大或重要問題；審查另跑 27 項來源測試通過，並確認實際 FOMC HTML 解析的 57 筆事件日期區間均為正值。

## 尚未完成及限制

- Git：`git add` 失敗，原因為 `.git/index.lock: Read-only file system`。尚未提交，工作目錄保留所有變更；無設定 remote，尚未推送。
- 常駐排程：`systemctl --user list-timers` 無法連上使用者 bus（`No data available`）。靜態 unit 驗證不等於排程已安裝／啟用。
- 正式每日流程：當下已超過準備／寄送窗口；未改系統時間，也未建立過期的正式日報。首次正式 07:30 準備與 08:00 寄送仍待部署後觀察。
- 3／7 天真實到期結算與週一週報：目前沒有真實成熟樣本；自動化整合測試已覆蓋兩週收集、模型、寄送、結算、週報，以及失敗、重試、補正及防重寄案例。

在可寫入 Git 的本機終端，可先提交目前檔案：

~~~bash
git add -- .env.example .gitignore README.md config deploy docs pyproject.toml requirements.lock src tests
git commit -m "Implement BTC Jev reports and validate live services"
~~~

使用者已確認目前不設定 remote，只需本機提交。排程部署指令見 README；應在能使用 user systemd 的實際主機執行。
