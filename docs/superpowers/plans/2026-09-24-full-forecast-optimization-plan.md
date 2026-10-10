# BTC Jev 全流程預測優化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每日 US$0.15 硬上限內，完成資料正確性、特徵、regime、Jev 角色與 walk-forward ensemble 的影子優化，並讓正式 Jev 預測保持可回溯。

**Architecture:** 先修正 cutoff／observed_at 的評估邊界，再由既有 frozen snapshot 產生 cutoff-safe 特徵與可解釋 market regime。正式 Jev 完成後，在剩餘預算中執行最多三個獨立角色；本機 deterministic ensemble 只在週截止使用已知結算做 walk-forward 評估，採用與回退都是版本化且不改寫正式 Prediction／MIME。

**Tech Stack:** Python 3.11、Pydantic、SQLite WAL、httpx MockTransport、pytest、Jinja2、systemd、wheel。

**Spec:** `docs/superpowers/specs/2026-09-24-full-forecast-optimization-design.md`

## Global Constraints

- 每日所有 Jev 呼叫共用同一 SQLite 預算，保守預留不得超過 3,571,428 input tokens（US$0.15）。
- 正式原始 Jev 預測、既有校準結果、Prediction、MIME、結算資料不可改寫。
- 新增角色只有 `market_regime`、`event_risk`、`adversarial_review`，失敗或預算不足不得取消正式預測。
- 候選只使用 cutoff 前已知資料，`observed_at == cutoff` 排除；所有評估 walk-forward。
- 新增特徵不可把 Jev request 撐過 24 KiB；缺失資料維持明確 missing，不填補未觀測值。
- 測試不使用真實 API key、secret、Binance 即時流量或 SMTP。

## Review Focus

- 合成結算與現實日期不再造成測試漂移：Task 1 固定 `observed_at` 與 strict cutoff。
- 特徵不偷看未來 candle、funding、OI 或新聞：Task 2 加 cutoff-safe fixtures。
- 角色失敗／預算不足不影響正式 Jev：Task 3 驗證正式 predictions 仍保存。
- ensemble 不因少量樣本或單一 regime 過擬合：Task 4 固定門檻、分離期間與有限候選網格。
- 影子評估與週報不改正式 MIME：Task 4／5 驗證 immutable snapshot 與重跑一致。

### Task 1: 固定時間語意、資料品質與乾淨安裝

**Files:**
- Modify: `src/btc_jev/store.py` — 保持 `observed_at` strict cutoff、每日 token reservation 與 schema migration 的原子性。
- Modify: `src/btc_jev/pipeline.py` — 確保 evaluate 使用明確觀測時間，失敗狀態可重試。
- Modify: `src/btc_jev/jev.py` — 預算拒絕發生在 HTTP 前，retry 不繞過預算。
- Modify: `src/btc_jev/domain.py` — 擴充正式與 shadow stage／評估型別而不改既有 Prediction contract。
- Modify: `src/btc_jev/config.py`, `pyproject.toml` — 驗證正式 dependency，包含 `websockets>=15,<16`。
- Test: `tests/test_store.py`, `tests/test_learning.py`, `tests/test_jev.py`, `tests/test_pipeline.py`。

- [ ] **Step 1: Write failing tests** for `observed_at == cutoff`, synthetic settlement timing, retry budget exhaustion, and clean dependency declaration.
- [ ] **Step 2: Run focused tests** with `.venv/bin/python -m pytest tests/test_store.py tests/test_learning.py tests/test_jev.py tests/test_pipeline.py -q`; confirm the date-sensitive failure and missing dependency behavior.
- [ ] **Step 3: Implement minimal time／budget／schema fixes** with monotonic migrations and no data deletion.
- [ ] **Step 4: Run focused tests** and confirm no HTTP request occurs after token reservation fails.
- [ ] **Step 5: Commit** with `git add src/btc_jev/store.py src/btc_jev/pipeline.py src/btc_jev/jev.py src/btc_jev/domain.py src/btc_jev/config.py pyproject.toml tests/test_store.py tests/test_learning.py tests/test_jev.py tests/test_pipeline.py && git commit -m "fix: make forecast evaluation and budgets time safe"`.

### Task 2: Add cutoff-safe features and market regimes

**Files:**
- Modify: `src/btc_jev/indicators.py` — add deterministic multi-period returns, volatility persistence／jump markers, funding deltas／percentiles, OI quantity／value changes, volume anomaly, price-to-SMA distances, and cross-exchange deviation fields.
- Modify: `src/btc_jev/context.py` — derive finite regime labels from saved features: `low_volatility`, `trend_up`, `trend_down`, `high_volatility`, `mixed`, `unknown`.
- Modify: `src/btc_jev/quality.py` — preserve source-age and freshness semantics for new inputs.
- Modify: `src/btc_jev/forecast.py` — include only validated, bounded feature and regime fields in Jev state without changing existing formal question IDs.
- Test: `tests/test_indicators.py`, `tests/test_context.py`, `tests/test_quality.py`, `tests/test_forecast.py`.

- [ ] **Step 1: Write failing tests** proving every new value uses only bars／funding／OI at or before `as_of`, returns missing on insufficient history, and stays finite.
- [ ] **Step 2: Run focused feature tests** with `.venv/bin/python -m pytest tests/test_indicators.py tests/test_context.py tests/test_quality.py tests/test_forecast.py -q` and confirm new names are absent.
- [ ] **Step 3: Implement deterministic features** with bounded decimal arithmetic and no network calls.
- [ ] **Step 4: Implement regime classification** using fixed thresholds and an explicit `unknown` fallback; do not fit parameters yet.
- [ ] **Step 5: Add feature-size／request-size tests** ensuring the 24 KiB Jev request limit remains enforced.
- [ ] **Step 6: Run all feature tests** and inspect serialized state for secrets and future timestamps.
- [ ] **Step 7: Commit** with `git add src/btc_jev/indicators.py src/btc_jev/context.py src/btc_jev/quality.py src/btc_jev/forecast.py tests/test_indicators.py tests/test_context.py tests/test_quality.py tests/test_forecast.py && git commit -m "feat: add cutoff-safe market regimes and features"`.

### Task 3: Add three independent Jev shadow roles under budget

**Files:**
- Modify: `src/btc_jev/domain.py` — allow role stage names `market_regime`, `event_risk`, `adversarial_review` while retaining existing stages.
- Modify: `src/btc_jev/forecast.py` — build distinct role-specific typed questions and bounded state views.
- Modify: `src/btc_jev/pipeline.py` — execute roles after formal predictions, saving only validated shadow results.
- Modify: `src/btc_jev/store.py` — freeze role StageResults by day／stage／request hash and expose them to weekly evaluation.
- Modify: `src/btc_jev/cli.py` — pass token-aware store reservation callback.
- Test: `tests/test_jev.py`, `tests/test_pipeline.py`, `tests/test_integration.py`, `tests/test_store.py`.

- [ ] **Step 1: Write failing tests** for three distinct payload hashes, successful typed role responses, one role failing, all roles skipped by budget, and formal predictions still saved.
- [ ] **Step 2: Run focused tests** and confirm role calls are not yet implemented.
- [ ] **Step 3: Implement role questions**: market only receives market fields, event only receives saved news／calendar, adversarial receives formal distribution plus quality／conflict fields.
- [ ] **Step 4: Implement independent guarded execution** with at most one successful result per role and no extra source fetches.
- [ ] **Step 5: Verify retries and token reservations** using MockTransport; assert no secret values in payload or exceptions.
- [ ] **Step 6: Commit** with `git add src/btc_jev/domain.py src/btc_jev/forecast.py src/btc_jev/pipeline.py src/btc_jev/store.py src/btc_jev/cli.py tests/test_jev.py tests/test_pipeline.py tests/test_integration.py tests/test_store.py && git commit -m "feat: add budgeted independent Jev shadow roles"`.

### Task 4: Build regime-aware, horizon-specific shadow ensemble

**Files:**
- Create: `src/btc_jev/ensemble.py` — define candidate distributions, finite equal-weight／single-source grids, strict walk-forward splits, metrics and status transitions.
- Modify: `src/btc_jev/store.py` — add immutable ensemble run／sample storage and migration.
- Modify: `src/btc_jev/weekly.py` — evaluate each horizon／regime identity at existing weekly cutoff.
- Modify: `src/btc_jev/render.py` — show shadow status, coverage, regime groups and candidate metrics without changing daily sections.
- Test: `tests/test_ensemble.py`, `tests/test_weekly.py`, `tests/test_render.py`.

- [ ] **Step 1: Write failing tests** for probability normalization, unavailable source exclusion, 30／15 sample warmup, seven-day separation, strict cutoff, class minimums, acceptance thresholds and regression fallback.
- [ ] **Step 2: Run focused tests** and confirm the ensemble module／tables are absent.
- [ ] **Step 3: Implement finite candidate construction** for formal Jev, Jev plus one baseline／role, and all-available equal weighting; preserve source IDs and versions.
- [ ] **Step 4: Implement walk-forward evaluation** storing train／validation IDs, cutoff, metrics, regime and policy version; make duplicate evaluation idempotent.
- [ ] **Step 5: Implement low-signal classification** for low maximum probability, small top-two margin or large role disagreement; do not alter raw distribution.
- [ ] **Step 6: Integrate weekly report shadow section** with explicit `warmup`／`rejected`／`accepted`／`regression` labels.
- [ ] **Step 7: Run focused tests** for late corrections and repeated weekly builds, confirming formal prediction／MIME payload hashes are unchanged.
- [ ] **Step 8: Commit** with `git add src/btc_jev/ensemble.py src/btc_jev/store.py src/btc_jev/weekly.py src/btc_jev/render.py tests/test_ensemble.py tests/test_weekly.py tests/test_render.py && git commit -m "feat: add walk-forward regime-aware shadow ensemble"`.

### Task 5: Final verification and release record

**Files:**
- Modify: `README.md` — document the full pipeline, three roles, regime／ensemble shadow status, low-signal semantics and US$0.15 cap.
- Create: `docs/reviews/2026-09-24-full-forecast-optimization-validation.md` — record commands, counts, artifacts and real-host limitations.
- Modify: `tests/test_deploy.py` — verify seven units, dependency metadata and path rendering.

- [ ] **Step 1: Run full regression** with `.venv/bin/python -m pytest -q`; record total and duration.
- [ ] **Step 2: Run static checks** with `.venv/bin/python -m compileall -q src deploy`, `.venv/bin/python -m pip check`, and `git diff --check`.
- [ ] **Step 3: Build a wheel** with `.venv/bin/python -m build --wheel`, install it into a fresh temporary venv, and run offline `btc-jev check-config`／`status`.
- [ ] **Step 4: Render and verify systemd units** with `deploy/install_units.py render` and `systemd-analyze --user verify`; report user-bus limitations honestly.
- [ ] **Step 5: Verify budget arithmetic and role fallback** from SQLite fixtures; assert reservations never exceed 3,571,428 tokens.
- [ ] **Step 6: Write the validation report** separating local tests, real-host service status, account billing, and future matured-prediction evidence.
- [ ] **Step 7: Commit** with `git add README.md docs/reviews/2026-09-24-full-forecast-optimization-validation.md tests/test_deploy.py && git commit -m "docs: record full forecast optimization validation"`.
