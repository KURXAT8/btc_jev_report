# Jev 多角色影子預測與預算硬限制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每日 US$0.15 硬上限內增加三個獨立 Jev 決策角色，保存影子結果並以 walk-forward 驗證候選組合，正式日報維持原 Jev 預測。

**Architecture:** `forecast.py` 在正式 Jev 結果完成後，以相同凍結 state 呼叫 `market_regime`、`event_risk`、`adversarial_review` 三個獨立 stage；`JevClient` 在 HTTP 前向 `Store` 預留保守 input tokens。`weekly.py` 只使用已結算且觀測時間早於 cutoff 的樣本比較正式 Jev 與有限權重組合，結果寫入不可變的影子評估資料，不回寫 Prediction 或 MIME。

**Tech Stack:** Python 3.11、Pydantic、SQLite WAL、httpx MockTransport、pytest、Jinja2、systemd timers。

**Spec:** `docs/superpowers/specs/2026-09-24-prediction-ensemble-design.md`

## Global Constraints

- 正式 Jev 預測、模型版本、預測期限、label、校準政策及既有資料不可改寫。
- 每日所有 Jev 呼叫共用同一 SQLite 預算，保守預留總額不得超過 3,571,428 input tokens（按 US$42／十億 input tokens 為 US$0.15）。
- 三個新增 stage 是 shadow-only；任何失敗、缺資料或預算不足都不得取消正式預測。
- 評估只使用 cutoff 前已知資料；結算 `observed_at == cutoff` 必須排除。
- 不在測試中使用真實 API key、secret、Binance 或 SMTP。

## Review Focus

- 影子呼叫失敗或耗盡預算時正式預測仍完成：由 Task 2 的 pipeline integration test 固定。
- 同一 prompt 不得被三個角色重用：由 Task 2 的 request payload test 固定。
- 觀測時間剛好等於 cutoff 的結算不得洩漏到驗證集：由 Task 3 的 cutoff test 固定。
- 既有 prediction／MIME 在重跑與影子評估後完全不變：由 Task 3 的 immutability test 固定。
- 乾淨安裝仍包含 `websockets` 且每日預留預算可被重啟恢復：由 Task 1 與 Task 4 固定。

### Task 1: 修正日期測試與預算／stage 持久化介面

**Files:**
- Modify: `src/btc_jev/domain.py` — 擴充 `StageResult.stage` 的三個角色值 `market_regime`、`event_risk`、`adversarial_review`，並保留既有分期限影子 stage 值。
- Modify: `src/btc_jev/store.py` — 保持每日 `reserved_input_tokens` 原子更新，增加 shadow stage／影子評估保存與查詢方法，保留 schema migration。
- Modify: `src/btc_jev/jev.py` — 讓 `reserve_attempt` 的 token 估算與既有 retry 流程一致，預算拒絕不發送 HTTP。
- Modify: `tests/test_store.py`, `tests/test_learning.py` — 明確傳入合成結算的 `observed_at=settled_at`，新增重啟、cutoff、預算與 stage identity 測試。
- Modify: `tests/test_jev.py` — 新增預算不足時零 HTTP 呼叫測試。
- Modify: `pyproject.toml` — 確認 `websockets>=15,<16` 是正式 runtime dependency。

**Interfaces:**
- `Store.reserve_attempt(day, stage, request_hash, estimated_input_tokens) -> bool` 原子拒絕超過每日上限。
- `Store.save_shadow_stage(result: StageResult) -> None` 以 `(day, stage, request_hash)` 凍結結果；角色輸出另保存為可供 `build_candidates` 使用的 typed probability payload。
- `Store.shadow_stages(day) -> list[StageResult]` 回傳可重跑評估的影子結果。

- [ ] **Step 1: Write the failing tests** for explicit observed time, shadow stage identity, token cap, and missing runtime dependency.
- [ ] **Step 2: Run the focused tests** with `pytest tests/test_store.py tests/test_learning.py tests/test_jev.py -q`; confirm failure is missing behavior or dependency declaration.
- [ ] **Step 3: Implement the smallest schema/model/packaging changes**; keep migration version monotonic and keep existing rows intact.
- [ ] **Step 4: Run focused tests again** and verify no network transport is invoked after budget rejection.
- [ ] **Step 5: Commit** with `git add src/btc_jev/domain.py src/btc_jev/store.py src/btc_jev/jev.py tests/test_store.py tests/test_learning.py tests/test_jev.py pyproject.toml && git commit -m "fix: harden shadow stage storage and token budget"`.

### Task 2: Add three independent Jev shadow decisions

**Files:**
- Modify: `src/btc_jev/forecast.py` — add role-specific typed questions and invoke at most one stage per role after formal forecasts.
- Modify: `src/btc_jev/pipeline.py` — pass the store callback for shadow stages; never make shadow failure set the daily job to failed when formal predictions exist.
- Modify: `src/btc_jev/cli.py` — pass the token-aware reserve callback used by production prepare.
- Modify: `tests/test_jev.py`, `tests/test_pipeline.py`, `tests/test_integration.py` — verify distinct payloads, stage persistence, retry behavior, budget exhaustion and formal prediction survival.

**Interfaces:**
- `forecast(..., save_shadow: Callable[[list[BaselineForecast]], None] | None)` remains backward-compatible.
- Role names are exactly `market_regime`, `event_risk`, and `adversarial_review`.
- Role calls use existing `StageResult` validation and the existing 24 KiB request limit.

- [ ] **Step 1: Write failing tests** asserting each role has a distinct question set and that a 401／timeout／budget rejection still returns all formal predictions.
- [ ] **Step 2: Run `pytest tests/test_jev.py tests/test_pipeline.py tests/test_integration.py -q`** and confirm the role calls are currently absent.
- [ ] **Step 3: Implement role questions** using only frozen state; do not add news fetching, market fetching, or another API client.
- [ ] **Step 4: Implement bounded role execution**: formal stage first, each role independently guarded, at most one successful result per role, save only validated results.
- [ ] **Step 5: Run focused tests** and inspect captured payloads to ensure no secret fields and no duplicate role prompt hashes.
- [ ] **Step 6: Commit** with `git add src/btc_jev/forecast.py src/btc_jev/pipeline.py src/btc_jev/cli.py tests/test_jev.py tests/test_pipeline.py tests/test_integration.py && git commit -m "feat: add budgeted Jev shadow decision roles"`.

### Task 3: Add walk-forward shadow ensemble evaluation

**Files:**
- Create: `src/btc_jev/ensemble.py` — deterministic probability mixing, candidate grid, sample eligibility, metrics and acceptance state.
- Modify: `src/btc_jev/store.py` — add immutable ensemble run and per-sample tables or JSON payloads with migration and retrieval methods.
- Modify: `src/btc_jev/weekly.py` — call ensemble evaluation at the existing weekly cutoff and expose candidate metrics in weekly snapshot.
- Modify: `src/btc_jev/render.py` — show shadow-only role／ensemble status and sample counts without changing daily trend lines.
- Test: `tests/test_ensemble.py` — test probability sums, unavailable source handling, train／validation split, acceptance thresholds, regression and frozen rerun.
- Modify: `tests/test_weekly.py`, `tests/test_render.py` — verify weekly shadow output and no formal MIME/prediction mutation.

**Interfaces:**
- `build_candidates(formal, baselines, shadows) -> list[CandidateForecast]` returns only finite distributions summing to 1.
- `evaluate_candidates(records, cutoff) -> list[EnsembleRun]` uses strict observed cutoff and returns `warmup`, `accepted`, `rejected`, or `regression`.
- `Store.save_ensemble_run(run) -> None` is idempotent for `(identity, cutoff, policy_version)`.

- [ ] **Step 1: Write failing tests** for cutoff exclusion, minimum counts, equal-weight candidate grid, both metric thresholds, and regression fallback.
- [ ] **Step 2: Run `pytest tests/test_ensemble.py tests/test_weekly.py tests/test_render.py -q`** and verify the module/API is missing.
- [ ] **Step 3: Implement deterministic candidate construction** with no free-form fitted weights and no network calls.
- [ ] **Step 4: Implement strict walk-forward splitting** with a seven-day separation and immutable sample IDs/result versions.
- [ ] **Step 5: Integrate weekly snapshot rendering** under a clearly labelled shadow section; preserve existing daily prediction sections byte-for-byte for unchanged inputs.
- [ ] **Step 6: Run focused tests** including repeated evaluation and changed late settlement scenarios.
- [ ] **Step 7: Commit** with `git add src/btc_jev/ensemble.py src/btc_jev/store.py src/btc_jev/weekly.py src/btc_jev/render.py tests/test_ensemble.py tests/test_weekly.py tests/test_render.py && git commit -m "feat: evaluate walk-forward shadow ensembles"`.

### Task 4: Final budget, package, and release verification

**Files:**
- Modify: `README.md` — document three shadow roles, daily US$0.15 hard cap, shadow-only adoption, and warmup limitation.
- Create: `docs/reviews/2026-09-24-prediction-ensemble-validation.md` — record commands, outputs, test count and known real-host limitations.
- Modify: `tests/test_deploy.py` — assert dependency/unit rendering and all seven unit files remain valid.

- [ ] **Step 1: Run full tests** with `.venv/bin/python -m pytest -q`; expected all tests pass and no warnings are promoted to errors.
- [ ] **Step 2: Run static checks** with `.venv/bin/python -m compileall -q src deploy`, `.venv/bin/python -m pip check`, and `git diff --check`.
- [ ] **Step 3: Build and install a wheel** into a fresh temporary virtual environment, then run `btc-jev check-config` and `btc-jev status` offline.
- [ ] **Step 4: Render units and run** `systemd-analyze --user verify data/systemd/btc-jev-*.service data/systemd/btc-jev-*.timer` without claiming the user bus is active here.
- [ ] **Step 5: Verify budget arithmetic**: the stored daily reservation never exceeds 3,571,428 input tokens; role failures do not consume unbounded retries.
- [ ] **Step 6: Write the validation report** separating local evidence from real-host checks and noting that current production samples remain warmup.
- [ ] **Step 7: Commit** with `git add README.md docs/reviews/2026-09-24-prediction-ensemble-validation.md tests/test_deploy.py && git commit -m "docs: finalize Jev shadow ensemble validation"`.
