# 比特幣趨勢服務：規劃驗收紀錄

日期：2026-09-21。交付範圍是規劃；App 程式、模型連線、郵件與排程屬後續實作驗收。

入口：[README](../../../README.md)、[設計規格](../specs/2026-09-21-btc-jev-daily-email-design.md)、[實作計畫](../plans/2026-09-21-btc-jev-daily-weekly-email.md)、[研究邊界](../research/2026-09-21-prediction-quality.md)。

## Superpowers 流程與交付

本次接續既有架構級規劃，使用 using-superpowers、brainstorming、writing-plans 與 verification-before-completion。沿用既有需求紀錄，不重新開啟產品訪談。方案取捨、資料契約、九項實作任務及其驗收方法已寫入文件；本次執行文件自我審查與離線檢查。

開發階段的工作樹隔離、TDD、執行計畫、程式審查及分支整合，適用於後續實作。未宣稱已執行這些階段，也未將反覆「繼續」訊息視為使用者逐條審閱文件的證據。規劃採既有預設供交接，實作計畫保留執行方式的交接說明。

## 需求覆蓋

| 規格 | 交付／測試責任 |
| --- | --- |
| 個人用途、Jev 限定、Gmail 沿用、隔離秘密 | 設計 §§1–2、7；Task 1 |
| 行情、衍生品、時間截止、新聞版本與事件日曆 | 設計 §3；Task 2 |
| OKX 同分鐘檢查、過期／缺漏／零新聞 | 設計 §3；Tasks 2–3、8 |
| 指標、歷史百分位、情境、兩階段推論 | 設計 §§3–4；Task 4 |
| 模型型別、重試預算、整次呼叫截止、重啟續作 | 設計 §4；Tasks 4–5、8 |
| 72／168 小時標籤、固定基準、不可變預測及結算 | 設計 §5；Task 5 |
| 週別、遲到補正、四基準、可靠度與樣本不足 | 設計 §5；Task 6 |
| 中文雙格式郵件、SMTP 不確定狀態、保留原 MIME | 設計 §6；Task 7 |
| 每日時槽、併發、崩潰恢復、週一狀態郵件 | 設計 §6；Task 8 |
| 保存／清理、部署、離線整合與真實驗收 | 設計 §§7–8；Tasks 5、9 |
| 對照實驗與校準 | 研究文件，預設關閉，第一版不實作 |

## 本次修正

1. 正常空新聞仍可分析；兩個加密 feed 皆缺漏／過期才阻擋推論。
2. 機率平手保留 Jev 原 choice；Noul 沒有 confidence；固定模型與回傳模型必須一致。
3. 明確方向與 confidence 分組分開；均勻備援不硬選方向。
4. OI 不取目標之後的點；K 線時間統一為交易所最後一毫秒。
5. 每階段／每日嘗試預算及成功階段均持久化；預測時間取實際完成時刻。
6. 模型整次呼叫有牆鐘期限；07:55 後答案不成為預測；非空新聞批次失敗不靜默略過。
7. 明訂 08:30 時槽允許該分鐘內啟動、08:31 起拒絕；每槽原子 claim 一次。
8. SMTP 原 MIME 不變；DATA 明確拒絕與回覆不明分開；成功後 QUIT 失敗不誤判重寄。
9. 文字清理涵蓋 request/state/MIME 副本；數值評分保留，過期輸入不宣稱可完整重播。
10. 更正 Git 為「已初始化、無提交、環境限制唯讀」，並將前次未附證據的網路查核降為歷史背景。

## 官方文件查核

以下於本次重新閱讀，僅確認介面與規劃依據，沒有使用帳戶憑證。日曆查核確認資料格式；未宣稱抓到截止前版本或完成正式事件解析。

| 來源 | 查核結果 |
| --- | --- |
| [TypeSafe API](https://docs.typesafe.ai/api) | systemone 的 state/model/questions、Choice 原選項機率、Noul 欄位與錯誤狀態吻合 |
| [TypeSafe models](https://docs.typesafe.ai/models) | 文件列有 jev-1.13.0；固定版本避免 alias 自動變動；帳戶存取仍須實測 |
| [TypeSafe confidence](https://docs.typesafe.ai/confidence) | confidence 源自分布，與單一方向機率不同；Noul 無此欄位 |
| [Binance Spot](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market) | 官方行情文件可讀；原連結已轉址，本次保留實際文件位址 |
| [Binance Futures OI](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) | OI 歷史僅最近一個月；不能假造 90 天背景 |
| [OKX 歷史 K 線](https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history) | history-candles 與完成狀態欄位支援同分鐘檢查設計 |
| [Fed FOMC](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | 日曆包含會議日期區間，不能從日期自行補出公告時刻 |
| [BLS CPI](https://www.bls.gov/schedule/news_release/cpi.htm) | 官方 CPI 行事曆可讀；仍須實作日期、時間、改期解析及快取版本 |

## 可重跑的規劃檢查

在專案根目錄執行：

```bash
python docs/superpowers/reviews/verify_planning.py
```

[檢查程式](verify_planning.py)只使用 Python 標準函式庫。檢查本機 Markdown 連結、程式區塊語法、九項任務完整性與實作核取項目維持未執行；直接抽取計畫內九個既有範例函式，驗證 Decimal 分類邊界、Brier、百分位、品質分級、歷史暖機、截止可見性、跨年週別、K 線排除及秘密欄位白名單，另查夏令時間轉換。

這不會 import 尚不存在的 btc_jev 套件，不呼叫 API、不寄信、不啟用 timer。語法檢查不驗證未實作模組的 import 可用性；範例公式通過不代表 App 通過測試。

本次實際執行退出碼為 0，結果：

```text
PASS: 5 documents; 20 local links; 21 Python blocks compile; 9 example functions; 37 example/boundary checks; 9 unexecuted implementation tasks.
Scope: document structure and selected example formulas only; no application, API, SMTP or timer validation.
```

規劃交付驗收通過：五份文件可互相導覽、九項開發任務覆蓋規格、已列歧義均同步修正、範例與邊界檢查通過。這是規劃完成紀錄，不是使用者已審閱批准開發或產品已上線的紀錄。

## 交付限制

- 程式碼、依賴鎖定、真實模型帳戶存取、收件匣送達與排程執行尚未驗證；均列在實作計畫。
- 預測優勢須靠部署後到期樣本評估，文件與合成資料不能證明命中率。
- 目前文件未 commit：`git status --short` 列為未追蹤；`.git` 的唯讀限制來自本次工作環境，沒有嘗試繞過。
