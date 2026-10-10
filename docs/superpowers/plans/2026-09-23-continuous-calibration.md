# 持續校準實作計畫

Spec: ../specs/2026-09-23-continuous-calibration-design.md

使用者已授權完整功能實作，在目前 workspace 原地完成並保留既有未提交修改。Git metadata 唯讀，不建立分支或提交。以本機 TDD 逐項執行，最後獨立審查。

## Task 1：可驗證的校準核心

- 新增 `calibration.py` 與 `test_calibration.py`。
- 先寫溫度、時間隔離、樣本與類別不足、可改善／退化資料的測試並觀察失敗。
- 實作固定政策、溫度擬合、成對評分、區塊估計與採用門檻。
- 驗證：`.venv/bin/python -m pytest tests/test_calibration.py -q`，預期全部通過。

## Task 2：持續學習與不可變保存

- 新增 `learning.py`、`test_learning.py`；Store 新增兩表並向後相容。
- 先測每週快取、截止／結算觀測時間與版本隔離、凍結、模式與前瞻退化。
- 實作每週學習、每天採用、稽核與狀態；無網路。
- 驗證：核心與 learning 測試全部通過。

## Task 3：每日流程、CLI 與呈現

- Settings/config/.env.example 加模式；CLI 加 learn、learning-status。
- prepare 在原始預測保存後套用；學習故障不阻斷日報。
- render 同時標示原始與校準機率；weekly 保存前瞻成對評分。
- 先寫流程、CLI、呈現、週報測試並觀察失敗，再實作。
- 驗證：相關測試通過，既有重啟、防重寄、1/3/7 天流程不退化。

## Task 4：文件、全套驗證與審查

- 更新 README、研究計畫的實作狀態、操作指令、門檻與限制。
- 執行 pytest 全套、compileall、git diff --check；實際離線 learn/status 及合成預覽。
- 由獨立 reviewer 查時間洩漏、凍結、錯誤回退及評分對齊；重要問題先補失敗測試再修正。
- 驗收不能宣稱已提高真實績效：目前正式成熟樣本為零。

## Review focus

重跑與結算補正、跨版本混樣、訓練後再擬合洩漏、未成熟的回退監測、舊郵件被改寫、影子樣本冒充前瞻、校準錯誤連帶毀掉有效日報、週界限和準備截止互動。
