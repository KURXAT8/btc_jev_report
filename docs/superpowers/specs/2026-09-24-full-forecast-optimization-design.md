# BTC Jev 全流程預測優化設計

日期：2026-09-24

## 目標

在每日 US$0.15 Jev 硬上限內，完整改善 BTC Jev 的預測流程。優化涵蓋資料截止與結算正確性、horizon 標籤、market regime、決定性市場特徵、獨立 Jev 角色、候選 ensemble、低信心過濾與 walk-forward rollout。正式 Jev 預測先保持可回溯，所有新策略先影子評估；只有前瞻驗證同時改善命中率與 Brier 分數才可採用。

這是提升可驗證預測品質的工程方案，不是命中率保證。

## 不變界線

- 每日所有 Jev 呼叫共用同一 SQLite token 預算，保守預留不得超過 3,571,428 input tokens，按公開 US$42／十億 input tokens 估算為 US$0.15。
- 正式日報的原始 Jev 預測、模型版本、既有校準結果、已凍結 Prediction、MIME 與結算資料不可改寫。
- 新增 Jev 呼叫最多三個獨立角色：`market_regime`、`event_risk`、`adversarial_review`。正式 Jev 流程優先；影子呼叫失敗或預算不足不得取消正式預測。
- 所有新候選使用嚴格 walk-forward；不得使用 cutoff 後取得的資料、結算或新聞文字。
- 測試不使用真實 API key、secret、Binance 即時流量或 SMTP。

## 完整資料流程

### 資料與時間正確性

修正並固定 `as_of`、`fetched_at`、`first_seen_at`、`published_at`、`known_at`、`observed_at` 的語意。到期結算只有在觀測時間嚴格早於評估 cutoff 時才可進入訓練／驗證；剛好等於 cutoff 的結果留到下一次。合成測試明確傳入觀測時間，避免依賴系統日期。

新聞採用來源、發布時間、首次看見時間與內容版本去重；官方公告、RSS 與媒體不因重抓而改變歷史快照。市場 K 線只使用已收盤且連續的資料，資金費率與 OI 必須在 cutoff 前可取得。

### 標籤與 horizon

保留現有 1／3／7 天預測介面與 ±1／2／4% 工程標籤，新增每個 horizon 的類別比例、基準命中率與分布診斷。若 walk-forward 顯示某 horizon 類別長期失衡，只建立影子替代標籤評估，不直接改正式 label；避免以改標籤製造表面命中率提升。

### 決定性市場特徵

在 `indicators.py` 與 `context.py` 增加可由已保存行情重算的特徵候選：多週期報酬、波動持續性與跳動、資金費率水準／變化／分位數、OI 數量與名目價值變化、價格相對均線距離、成交量異常、Binance／OKX 差異與資料新鮮度。每個候選都必須有 cutoff-safe 測試；缺資料時回傳明確 missing，不填入未觀測值。

特徵先以消融實驗比較；只有在 walk-forward 的驗證集改善且未造成其他 horizon 明顯退化時，才進入影子 state。不得因增加欄位而增加 Jev 請求大小超過既有 24 KiB 限制。

### Market regime

以保存的波動、趨勢、成交量、funding、OI 特徵建立有限且可解釋的 regime：`low_volatility`、`trend_up`、`trend_down`、`high_volatility`、`mixed`、`unknown`。regime 只作條件與分組，不由未驗證的自由模型直接改寫機率。每個 horizon 個別評估 regime 條件下的 Jev、基準與候選表現；樣本不足時只報告暖機。

### Jev 分工與預算

正式 Jev 仍先進行新聞分類及 1／3／7 天趨勢。之後可在剩餘預算內執行三個獨立角色：

- `market_regime`：只判斷價格、K 線、波動、funding、OI 與 regime。
- `event_risk`：只判斷已保存新聞與官方日曆對各 horizon 的風險。
- `adversarial_review`：只檢查正式分布的過度自信、相反訊號與資料品質衝突。

三個角色必須使用不同 request hash 與問題集合，結果只保存為影子 StageResult。每日 token 預留在 HTTP 前原子扣除；影子用量超出預算時跳過，正式結果照常保存。

### Ensemble 與低信心政策

建立有限候選網格：正式 Jev、正式 Jev 加單一基準、正式 Jev 加一個 Jev 角色、所有可用來源等權平均。每個 horizon／model／rules／labels／quality identity 個別評估；候選機率歸一且可重算。

候選至少需要 30 筆訓練與 15 筆驗證，訓練與驗證至少間隔 7 天，且兩段各類別至少 5 筆。採用條件是驗證命中率至少比正式 Jev 高 0.02、Brier 至少改善 0.005，且沒有 horizon 退化超過 0.01。未達門檻為 `warmup` 或 `rejected`。

當最高機率不足 0.55、前兩名差距不足 0.15、或角色分布差異超過固定門檻時，標記 `low_signal`；此狀態只影響採用／呈現，不修改原始機率。若未來啟用候選，最近 30 筆採用結果退化時自動標記 `regression` 並回原始 Jev。

## 評估與驗收

每週只用 cutoff 前已知資料執行一次不可變評估，保存策略版本、特徵版本、樣本 ID、結算版本、train／validation cutoff、命中率、Brier、log loss、校準誤差、coverage 與 regime 分組。重跑相同輸入必須得到相同 snapshot hash；晚到修正只能進入後續 cutoff。

驗收包含：完整 pytest、日期與觀測時間測試、資料洩漏測試、特徵消融、角色失敗與預算耗盡、ensemble 機率總和、候選退化回退、SQLite 重啟恢復、wheel 隔離安裝、compileall、pip check、systemd-analyze 與 git diff --check。

## 預期限制

目前只有少量預測且尚無成熟結算，所有新策略將先維持暖機。regime switching、ensemble 與獨立 Jev 角色可能改善穩定性或機率品質，但不保證方向命中率；只有長期前瞻結算才能確認是否值得採用。
