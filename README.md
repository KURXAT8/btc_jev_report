# BTC Jev：比特幣波段分析郵件

個人使用的比特幣 1 天／3 天／7 天趨勢分析服務。提供 Python CLI、繁體中文 HTML／純文字日報、週一合併週報、SQLite 保存與 systemd 排程檔。AI 固定使用 TypeSafe jev-1.13.0；沒有交易下單功能。

Task 1–9 已實作，基本驗證、限制及重跑指令見 [第一版驗收](docs/acceptance.md)。2026-09-23 已完成真實資料來源、Jev 分析及 Gmail 收件驗證，修正來源問題後全套 245 項測試通過；常駐排程與正式每日時段仍待部署驗收。詳見 [真實環境驗證](docs/live-validation-2026-09-23.md)。

後續新增 1 天預測（盤整 ±1%）並精簡郵件，改版全套 264 項測試通過。此次使用離線測試與預覽，未再次呼叫模型或寄信；變更與相容性見 [郵件改版紀錄](docs/mail-update-2026-09-23.md)。

| 文件 | 用途 |
| --- | --- |
| [設計規格](docs/superpowers/specs/2026-09-21-btc-jev-daily-email-design.md) | 產品行為、來源、時段及公式 |
| [實作計畫](docs/superpowers/plans/2026-09-21-btc-jev-daily-weekly-email.md) | Task 1–9、介面與驗收清單 |
| [執行紀錄](docs/superpowers/reviews/implementation-ledger.md) | 接續進度、修正與決策 |
| [研究計畫](docs/superpowers/research/2026-09-21-prediction-quality.md) | 校準已實作；同日期對照研究仍未啟用 |
| [規劃驗收](docs/superpowers/reviews/2026-09-21-planning-verification.md) | 先前文件及官方契約查核 |

## 安裝與離線預覽

使用 Linux／POSIX、Python 3.11 以上；本次驗證 Python 3.13.5。整次網路操作期限使用 POSIX signal，CLI 必須在主執行緒執行。

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pytest -q
.venv/bin/btc-jev preview --fixture tests/fixtures/preview.json --output-dir data/preview
~~~

預覽產生 data/preview/report.html 與 report.txt，清楚標示合成資料；不需要憑證，也不呼叫模型或 SMTP。requirements.lock 鎖定已驗證的執行及測試相依套件；build backend 依 pyproject.toml 在隔離環境安裝。

## 設定

複製 [.env.example](.env.example) 為本專案 .env，在本機填入 TYPESAFE_API_KEY、Binance HMAC API key／secret 與郵件欄位。Binance 憑證只供公告 WebSocket 認證；請關閉交易與提領權限。標準變數名是 `BINANCE_API_KEY`、`BINANCE_API_SECRET`；程式也相容目前設定的 `API_KEY`、`secret_KEY`。不要將秘密貼進對話或提交到 Git。MAIL_ENV_FILE 可指定另一個環境檔以讀取郵件設定；只採用 GMAIL_ADDRESS、GMAIL_APP_PASSWORD、RECIPIENT_EMAIL。

優先順序：程序環境 → 本專案 .env → 舊檔案的三個 mail 欄位。支援單行引號、註解與 export；不展開變數或執行 shell。相對資料路徑依 .env 所在目錄解析。check-config 只列 SET／MISSING，不列出秘密值。

~~~bash
.venv/bin/btc-jev check-config
.venv/bin/btc-jev status
~~~

## 每日運作

全部排程以 Asia/Taipei 為準，儲存時間為 UTC。

| 時間 | 工作 |
| --- | --- |
| 每小時 05 分及 07:25 | 存檔六個 RSS；日曆每六小時刷新，07:25 強制刷新 |
| 07:30 | 取得行情、核對到期結果、凍結輸入、執行 Jev |
| 07:40 | 停止當日市場資料取得 |
| 07:55 | 停止模型工作，失敗則準備狀態郵件 |
| 08:00 | 寄送當日已凍結郵件，週一同封附 Weekly Report |
| 08:05、08:15、08:30 | 只重試明確可重試的 SMTP 失敗 |

四個寄送時槽各允許該分鐘內 claim 一次；08:31 起不開始寄送，往日郵件不補寄。成功準備的 MIME、Date、Message-ID、週報快照不因重試而改動。主機停機可能錯過日報，請用 status／journal 檢查。

~~~bash
.venv/bin/btc-jev collect-context
.venv/bin/btc-jev collect
.venv/bin/btc-jev prepare
.venv/bin/btc-jev status
.venv/bin/btc-jev preview --output-dir data/preview
.venv/bin/btc-jev evaluate
.venv/bin/btc-jev send --due
.venv/bin/btc-jev cleanup
~~~

排查分析時可執行 `.venv/bin/btc-jev --debug prepare`。`--debug` 顯示模型階段、嘗試次數、HTTP 狀態及安全錯誤代碼；不記錄 API 金鑰、請求內容或供應商回應本文。`status` 會列出保存的分析失敗階段與代碼。

collect 只在 07:30–07:40 取得市場資料並保存價格錨點；prepare 產生完整分析並保存快照，不寄信；evaluate 補查到期價格；send --due 不呼叫模型。preview --day YYYY-MM-DD 讀取已保存郵件。可以用 --env-file PATH 指定本專案設定檔，放在子指令之前。

## 新聞來源

RSS 包含 CoinDesk、Cointelegraph、Federal Reserve、SEC 新聞稿、CFTC 一般新聞稿及 CFTC 執法新聞稿。SEC／CFTC 僅保留提及 Bitcoin、crypto、digital asset、blockchain、ETF 等關鍵詞的消息。媒體新聞保存 3 天，官方來源保存 7 天。

Binance 使用官方 `com_announcement_en` WebSocket，由獨立的 `btc-jev-binance-capture.service` 常駐接收並保存至同一 SQLite。連線以 HMAC 簽署，每 30 秒 ping，並在 23 小時主動重連。只保存標題與最多 600 字的純文字正文，不抓公告全文頁。服務離線不會阻止 RSS、行情或日報；停機期間錯過的推送不保證補回。

日報最多採用 20 則：Fed 5、SEC 3、CFTC 一般 3、CFTC 執法 2、Binance 4，其餘名額由 CoinDesk／Cointelegraph 輪流補足。這些來源增加消息覆蓋，不保證提高命中率。

## 分析與週報

Binance BTCUSDT 現貨提供基準價、K 線及結算價，Binance 衍生品提供資金費率與 OI；OKX 只作同分鐘 BTC-USDT 現貨交叉檢查。CoinDesk、Cointelegraph、Fed RSS 提供截止前取得的新聞；Fed FOMC、BLS CPI 提供官方事件日曆。固定來源列於 [sources.toml](config/sources.toml)。

程式計算指標、歷史百分位、趨勢／波動情境。歷史少於 30 筆標為暖機，保留實際期間與樣本數。日期區間事件不假造精確時刻；已知的公布安排不等於公布結果。

Jev 分類新聞文本，獨立判斷 24／72／168 小時影響，再產生各期間的三類機率。成功取得正式預測後，另以相同凍結資料分開詢問 3 天及 7 天，結果只作影子實驗、不影響日報。到期後，週報用相同結算樣本比較命中率、Brier 分數及五分箱最高類別校準誤差；少於 30 筆標示小樣本，不自動切換正式策略。

Jev 每階段最多三次 HTTP 嘗試，每天最多 15 次，跨重啟持久化；完整請求上限 24 KiB。每次請求預留 UTF-8 位元組數 4 倍的輸入 token，日累計上限 3,571,428 預留 token，按 TypeSafe 公開輸入價格每百萬 token US$0.042 設定，相當於 US$0.15。每天的嘗試次數與單次大小上限使預留量實際最多約 1,474,560 token（約 US$0.062），留有緩衝。TypeSafe 帳戶點數消耗可能依帳戶／模型設定不同，請以帳戶實際計價確認；此為本機費用估算上限，不是供應商帳單保證。輸出 token 依[公開價格說明](https://typesafe.ai/blog/introducing-system-one-models-and-jev)不另計費。參考：[TypeSafe 價格](https://typesafe.ai/)、[API 點數條款](https://typesafe.ai/legal/mca)。

1 天報酬 ±1%、3 天 ±2%、7 天 ±4% 為盤整閉區間；最大機率小於 0.55 或前兩名差距小於 0.15 顯示方向不明確。它們是工程預設，未經收益最佳化。模型機率、模型確定程度、資料品質與實際命中率分開呈現。

週報按到期日在台北時區歸週，分 1／3／7 天、模型／規則／標籤／品質版本，包含前一完整週、最近四個完整週與保留期間累積。所有有效預測均計分，包括未成功寄出的預測；零樣本不顯示 0% 命中率，小於 30 筆提示樣本不足。

四種凍結比較基準為永遠盤整、均勻機率、歷史類別比例、簡單動能；均勻機率與歷史暖機備援沒有分類命中率。另有 Jev 分開期限影子策略。共同樣本比較、Brier、混淆矩陣、資料品質、情境及描述性可靠度表持續計算並保存在週報快照；精簡郵件顯示正式預測及影子策略的成對指標、樣本數及補正摘要。校準機制另存實際套用樣本的成對機率評分，原始績效不被取代。晚到結果維持原到期週；錯過寄送的補正仍會在下次週報出現。

日報依序呈現資料與品質（基準價、品質、模型）、1／3／7 天趨勢、市場摘要、近期事件、最多三則新聞。詳細時間戳、規則版本、缺漏診斷與設定雜湊仍保存在資料庫。

## 持續學習與機率校準

每日 `prepare` 會在保存原始預測後執行本機校準流程，依已知到期結果每週更新一次，無額外模型 API 請求。需要主機既有每日排程持續執行；這不是獨立背景服務。`evaluate` 負責結算，`learn` 可單獨執行本週學習，兩者用途不同。

~~~bash
.venv/bin/btc-jev learning-status
.venv/bin/btc-jev learn
~~~

兩個新命令皆不呼叫網路、不寄信；`learning-status` 是唯讀，`learn` 寫入本機不可變學習紀錄。設定 `CALIBRATION_MODE=auto`（預設）、`shadow`（學習但不採用）或 `off`（停用學習與新預測校準）。模式變更只影響未凍結的新預測，已保存郵件保持不變。

- 1／3／7 天與模型、規則、標籤、設定版本分別學習。每個台北週一 00:00 截止只評估一次，當週重跑沿用相同結果；截止後取得的結算／補正等下週再使用。
- 滾動使用過去 365 天的原始機率及已知結果。至少 120 筆訓練、60 筆較晚驗證，兩區間隔離至少 7 天；訓練每類至少 10 筆、驗證每類至少 5 筆。未滿足時為暖機，不憑空生成校準。每天一筆需累積約半年以上，實際可能更久。
- 已知結果須嚴格早於週截止到期且寫入資料庫。剛好在週截止寫入的結果會等下一週；晚到更正不會回填過去樣本。舊資料升級時，沒有觀測時間的結算保守地從升級當刻起才算已知。
- 採單參數溫度校準。候選必須讓較晚驗證的 Brier 至少改善 0.005、log loss 不變差，並通過至少 8 個七天區塊的成對 bootstrap 檢查，才在 `auto` 模式套用。驗證完不重擬合到驗證資料上；新週未通過就回原始機率。
- 追蹤最近 90 天真正已套用的結果。至少 30 筆時，若校準 Brier 比同批原始差逾 0.01 或 log loss 差逾 0.02，就暫停採用、回原始機率；仍繼續每週學習。此期間的退化資料離開 90 天窗口後才可能恢復採用。
- 原始機率、Jev confidence、每週校準參數、訓練／驗證樣本與結算版本、每日採用決策分開保存。校準失敗或逾時時保留有效的原始日報。只有實際採用時才在日報增加「Jev 原始機率／本機校準機率」標示。
- 校準工作失敗時凍結該日原始機率及郵件；重啟不會用後來學出的機率改寫已準備報告。

`learning-status` 的 `runs` 顯示各組最新評估、樣本數、溫度與驗證分數，`last_application` 是最近一次實際採用狀態；`expired` 表示版本過期。主要狀態：`warmup` 暖機、`accepted` 驗證通過、`rejected` 未通過、`active` 已採用、`shadow` 僅觀察、`regression` 退化回退、`error` 學習故障、`off` 停用。

溫度校準不改變最高機率方向，因此不能保證提高分類命中率；會影響機率可靠度與「方向明確」門檻的涵蓋率。門檻和七天區塊長度是工程政策，反覆每週評估及更長市場相依性仍可能過度擬合。真實優勢須由前瞻成績確認，不能以合成測試或回溯驗證宣稱。設計與驗收見 [持續校準設計](docs/superpowers/specs/2026-09-23-continuous-calibration-design.md)。

## SMTP 未知結果與保存

DATA 傳送後失去回覆或送信程序崩潰，工作標為 unknown，不自動重寄。先在 Gmail 已寄件／收件端核對 Message-ID，再執行其中一項：

~~~bash
.venv/bin/btc-jev reconcile btc-YYYY-MM-DD --result sent
.venv/bin/btc-jev reconcile btc-YYYY-MM-DD --result not-sent
~~~

not-sent 只開放當日尚未使用的後續時槽，錯過窗口則標為 missed。一般暫時失敗為 retryable；驗證失敗／明確永久拒絕為 failed。

cleanup 清除超過 90 天的新聞文字副本與已結束工作的 MIME；未結算／待對帳必要內容保留。數值、模型分類、雜湊、價格錨點、基準、週報引用與日期防重複鍵持續保存，至少滿足 365 天需求。文字過期後不能完整重播原文輸入；清理不重新計算原雜湊。資料目錄應另做私人備份。

## 部署與停止

先完成設定、真實 Jev prepare 與本機預覽，再由操作者安裝及啟用排程。首次早晨分析前先讓 collect-context 取得截止前新聞。

~~~bash
.venv/bin/python deploy/install_units.py render --project "$PWD" --destination "$PWD/data/systemd"
systemd-analyze --user verify "$PWD"/data/systemd/btc-jev-*.service "$PWD"/data/systemd/btc-jev-*.timer
.venv/bin/python deploy/install_units.py install --source "$PWD/data/systemd"
.venv/bin/python deploy/install_units.py enable
systemctl --user list-timers --all 'btc-jev-*'
systemctl --user status btc-jev-binance-capture.service --no-pager
journalctl --user -u btc-jev-prepare -u btc-jev-deliver -u btc-jev-collect-context
~~~

安裝器把 user systemd 設定連結到專案 `data/systemd/` 內的 unit 檔，拒絕覆蓋內容不同的既有 unit；render、install、enable 分開。請勿把安裝來源放在 `/tmp`，否則暫存檔清除後連結會失效。使用者服務需有持續運作的 user manager；enable 也會啟用 Binance 常駐收集服務。停止排程及收集：

~~~bash
systemctl --user disable --now btc-jev-prepare.timer btc-jev-deliver.timer btc-jev-collect-context.timer btc-jev-binance-capture.service
~~~

模型預設固定 jev-1.13.0，不自動升級。變更模型／規則需保留版本與新的設定雜湊，先重跑驗證；新版本各自重新累積校準證據。其他研究候選方法不直接加入正式預測。
# btc_jev_report
