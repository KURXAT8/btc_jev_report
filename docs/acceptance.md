# 第一版驗收：BTC Jev

日期：2026-09-22。範圍：Task 1–9 的郵件服務與本機預覽；Python 3.13.5／Linux。這份紀錄區分基本驗證與真實部署，不以合成機率宣稱預測優勢。

## 交付

| Task | 已交付 | 驗證 |
| --- | --- | --- |
| 1 | 設定優先序、mail allowlist、SecretStr、具版本資料模型 | test_config、test_domain |
| 2 | Binance／RSS／FOMC／CPI、截止版本、新聞重新觀察紀錄 | test_sources、test_calendars、test_boundaries |
| 3 | OKX 同分鐘交叉檢查、來源新鮮度與品質分級 | test_quality |
| 4 | 指標、歷史百分位、情境、Jev 分階段請求與持久化上限 | test_indicators、test_context、test_jev |
| 5 | SQLite 交易、不可變預測、價格錨點、結算與四種基準 | test_store、test_evaluation、test_baselines |
| 6 | 到期日歸週、版本分組、共同樣本、遲到補正、可靠度 | test_weekly |
| 7 | 繁體中文 HTML／文字、MIME 凍結、SMTP 分類 | test_render、test_mail |
| 8 | 每日流程、期限、重啟、互斥、防重寄、CLI | test_pipeline、test_cli、test_boundaries |
| 9 | 六份 systemd 檔、分開 render/install/enable、鎖定依賴、文件 | test_deploy、test_integration、實際 systemd-analyze |

## 基本驗證與重跑

~~~bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests deploy
.venv/bin/python -m pip check
.venv/bin/python docs/superpowers/reviews/verify_planning.py
.venv/bin/btc-jev preview --fixture tests/fixtures/preview.json --output-dir data/preview
~~~

全套測試結果：240 passed，零失敗、零跳過。compileall 通過；pip check 顯示 No broken requirements found。文件檢查只驗證連結、範例公式與進度，不替代產品測試。

HTML／文字預覽位於 data/preview/report.html、report.txt。資料含 3 天／7 天合成預測、指標、暖機情境與新聞，明確標為離線合成資料。test_render 驗證兩種內容一致、HTML escaping、安全連結、零樣本與週報。測試中的 HTTP 與 SMTP 注入合成傳輸，不寄到真實信箱。

重新安裝 editable 套件後，.venv/bin/btc-jev 入口可直接執行；標準 wheel 建置及從 wheel 載入模板的預覽通過。依賴清單見 requirements.lock，鎖定執行與測試套件；建置工具仍按 pyproject.toml 在隔離環境解析。

~~~bash
.venv/bin/python deploy/install_units.py render --project "$PWD" --destination /tmp/btc-jev-release-units
systemd-analyze --user verify /tmp/btc-jev-release-units/btc-jev-prepare.service /tmp/btc-jev-release-units/btc-jev-prepare.timer /tmp/btc-jev-release-units/btc-jev-deliver.service /tmp/btc-jev-release-units/btc-jev-deliver.timer /tmp/btc-jev-release-units/btc-jev-collect-context.service /tmp/btc-jev-release-units/btc-jev-collect-context.timer
~~~

六份產生的 unit 實際驗證 exit 0，無診斷訊息；測試也包含有空白的專案目錄。這個檢查沒有安裝／啟用 timer。

## 關鍵情境

| 情境 | 結果／證據 |
| --- | --- |
| 一般日與星期一寄送 | 日期唯一鍵；週一日報／週報同一 MIME；成功後重跑不重寄 |
| 兩週端到端 | MockTransport 走真實來源 client → SQLite → Jev → MIME → SMTP double → 到期結算 → 下週週報；兩封、兩次趨勢請求 |
| 新聞成功但為空 | 零新聞分類請求，仍有趨勢判斷 |
| 兩個加密新聞來源失效 | 保留 P0 錨點、停止模型、建立狀態郵件 |
| Jev 529 耗盡 | 最多三次該階段請求；週一仍附既有週報 |
| 預測尚未到期／缺到期價格 | 不當作答錯；保持待結算 |
| 結算端點格式錯誤 | 該筆待結算，其他結算與週一報告繼續 |
| 晚到補正 | 維持原到期週，不改舊快照；missed／failed／unknown 週報的補正下週仍列出 |
| 07:55／08:31 邊界 | 不接受截止後模型答案、不開始窗口後寄送 |
| SMTP DATA 結果未知 | unknown 後不自動重試；人工 reconcile 才開放未用時槽 |
| 明確 SMTP 4xx／5xx | retryable／failed；250 後 QUIT 失敗仍視為成功 |
| SQLite 併發與重啟 | 只有一個 delivery owner；已完成模型階段沿用，HTTP 次數不重置 |
| 預測後、郵件凍結前重啟 | 沿用兩個預測；保留模型新聞排除統計 |
| 新聞 A→B→A | 按最新觀察時間選回 A，保留初見時間與舊截止可見版本 |
| 91 天清理 | 已結算文字副本清除、數值仍可評分；必要未解決工作受保護 |

## 本次修正與審查

接續時首次完整測試為 226 passed、2 failed。兩個既有失敗是新聞舊版本重新出現未被選中，以及模型新聞排除數未保存。本次均先重現，再修正。

獨立唯讀審查另找到兩項重要問題：未寄出的週報補正被當成已通知；畸形結算回應的 IndexError 會中止週一準備。回歸測試先失敗，再修正並通過全套。未留下待修的重要或次要審查項目。

實際 systemd 檢查找到 WorkingDirectory／EnvironmentFile 的引號錯誤；分別以實際解析測試重現後修正。追加重啟回歸確認不會清掉既有新聞排除統計。

## 決策與外部部署邊界

- 沿用使用者指定的原工作目錄；Git 尚無 HEAD，環境限制 .git 唯讀，未建立 commit、分支或 PR。程式與文件完整保留在工作目錄；代價是目前沒有 Git 提交歷史可供回退。
- 接續現有實作，不重新製造 Tasks 2–9 的早期缺模組失敗。這次只宣稱實際看到的修正 RED→GREEN 與最終全套結果；代價是不能重建所有早期測試先行的時間順序。
- Task 1–9 與基本離線驗收為本次完成範圍；真實帳戶與排程部署仍需獨立驗收。未讀取或改寫舊專案憑證、未呼叫付費模型、未寄送真實郵件、未啟用 timer。代價是供應商即時可用性、帳戶用量／價格、真實 Gmail 收件及常駐排程尚未驗證。
- 數值與週報資料持續保存，文字依 90 天規則清理；目前不刪除超過 365 天的數值。這符合最低保存期，代價是資料庫需要持續磁碟空間。

正式上線依 README：本機設定、check-config、截止前 collect-context、在準備窗口執行一次真實 prepare、檢視預覽／model usage，再寄送及啟用排程。模型輸出的實際優勢必須等待真實預測到期後評估。
