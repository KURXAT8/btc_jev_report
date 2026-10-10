# Task 1：設定與資料契約實作紀錄

日期：2026-09-22。範圍：[實作計畫](../plans/2026-09-21-btc-jev-daily-weekly-email.md) Task 1；不包含 Tasks 2–9。

## 已實作內容

- Python 3.11+ 的 src-layout 套件與本地 `.venv`，以及忽略帳密、快取、資料和日誌的規則。
- 明確 `.env` 路徑、單行引號／註解解析、不做 shell 或變數展開。
- 傳入環境優先，其次本專案設定；舊環境僅讀三個郵件欄位。
- Jev 固定編號版本、時區驗證、SecretStr、只顯示設定名稱的錯誤；操作時才檢查需要的憑證。
- 計畫全部共享資料契約：設定、K 線、新聞、健康狀態、日曆、品質、背景、快照、機率、預測、基準、結算、郵件、週報、模型階段與工作。
- UTC aware 時間、Decimal 價格、有限數值、機率總和與平手、精確 72／168 小時到期、不可就地修改的預測及機率映射、MIME bytes 的無損 JSON 往返。
- 固定合成資料工廠；測試阻擋 socket 連線，只讀暫存的假帳密。

## 驗證紀錄

- 基線文件檢查通過：5 份文件、20 個本機連結、21 個 Python 區塊、37 個範例檢查。
- 設定／資料模型 RED：111 個測試因模組未實作而失敗；實作後 111 個全數通過。
- 首次執行的 5 個測試誤報來自 traceback 印出測試來源中的假密碼常數；將常數移離拋錯行，保留完整 traceback 遮蔽檢查，未放寬產品要求。
- 文件檢查原先禁止任何已完成勾選；增加 4 個失敗測試後調整為顯示文件記載進度，不宣稱驗證產品完成。
- 設定測試：`.venv/bin/python -m pytest tests/test_config.py -q` → 27 passed。
- 資料模型測試：`.venv/bin/python -m pytest tests/test_domain.py -q` → 84 passed。
- 全套：`.venv/bin/python -m pytest -q` → 115 passed（另含 4 個文件進度測試）。
- `.venv/bin/python -m compileall -q src tests` 成功；`pip check` 回報 No broken requirements found。
- 在 `/tmp` 透過專案 `.venv/bin/python` 成功匯入已安裝的設定與資料模型，確認不依賴當前目錄。
- 獨立唯讀審查另行執行全套 115 個測試，沒有 Critical 或 Important 問題，結論為 Task 1 可驗收；Minor 一項如下。

目前驗證環境：Python 3.13.5、Pydantic 2.13.5、pytest 9.1.1、HTTPX 0.28.1、feedparser 6.0.14、Jinja2 3.1.6。尚未驗證 Python 3.11 的完整依賴組合；最終鎖版屬 Task 9。

## 執行決策與限制

1. 本次只完成使用者要求的「下一步」Task 1，其餘功能留待後續；此時仍不能產出日報。
2. Git 為尚無提交的 master，環境限制 `.git` 唯讀；在原目錄保存檔案，未建立 worktree 或 commit，因此沒有已提交的回復點。
3. `MAIL_ENV_FILE` 預設不讀任何檔案，範本以註解提供舊專案路徑；啟用時須明確設定，避免離線操作意外取得真實秘密。

資料契約只驗證結構與基本一致性，不等於來源新鮮度、新聞截止可見性或 Jev 回應真偽已驗證。快照內的一般列表及字典不是深層不可變容器，後續 Store 必須保存不可變快照。Pydantic 提供的跳過驗證介面不應用於不可信資料。

真實 Jev、SMTP、systemd、SQLite、行情／新聞收集均未執行或實作；沒有讀取真實郵件 `.env`，沒有修改 `foggy_V2`。不宣稱預測準確度。

## 審查後續與未驗證範圍

Minor（移交 Task 5）：`BaselineForecast` 可接受標為 `uniform_fallback` 但機率不相等的資料；目前沒有基準產生流程，因此不阻擋本階段，實作基準時需先補拒絕測試再限制為相等機率。機率的完整性、有限數值、加總及有 choice 時的最大選項檢查已存在。

審查列出的下列範圍都保留為未完成，不以結構測試替代行為驗證：

- 台北週別與跨年：Task 6。
- SMTP 不確定結果、恢復與防重寄：Tasks 7–8。
- 資料庫不可變性、補正與版本分表：Tasks 5–6。
- 週一失敗郵件與零樣本呈現：Tasks 6–8。
- 來源新鮮度／可見性、基準公式、完整工作狀態轉換：後續相應任務。
- Python 3.11 相容性、依賴鎖版：待後續環境驗證與 Task 9。
- 真實帳密、服務及部署：未執行。
- 最終進度文字：由本次檔案更新與最後一次文件檢查核對，只標記 Task 1。
