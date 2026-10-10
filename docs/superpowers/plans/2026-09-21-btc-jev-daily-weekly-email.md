# BTC Jev Daily and Weekly Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天台北時間 08:00 透過既有 Gmail 帳號寄出 Jev 的 BTC 3／7 天趨勢分析，並於每週一同封提供可核對的 Weekly Report。

**Architecture:** Python CLI 以公開資料建立不可變預測，SQLite 保存工作、結算與週報快照。只有 Jev 處理 AI 判斷；指標、成效評分與中文郵件均由程式處理。準備與寄送分離，SMTP 重試不重新分析。

**Tech Stack:** Python 3.11+、HTTPX、Pydantic、feedparser、Jinja2、SQLite、SMTP_SSL、systemd user timers、pytest。

**Spec:** `docs/superpowers/specs/2026-09-21-btc-jev-daily-email-design.md`。執行前完整閱讀設計稿與本計畫。

**狀態（2026-09-22）：** Task 1–9 已實作並完成基本離線驗收，詳見 [第一版驗收](../../acceptance.md)及[執行紀錄](../reviews/implementation-ledger.md)。真實帳戶／收件／排程啟用另列於下方部署階段。

勾選表示目前交付已完成並有測試覆蓋；接續時已存在的實作不重製早期 RED 過程，不能由勾選推定所有歷史步驟曾在本次執行。Git commit checkpoint 依 Global Constraints 豁免，實際證據以驗收紀錄為準。

## 第一版交付順序

| 任務 | 交付結果 |
| --- | --- |
| 1 | 獨立設定、憑證隔離與資料契約 |
| 2 | 行情、新聞與官方日曆取得 |
| 3 | 同分鐘跨所檢查與品質分級 |
| 4 | 指標、歷史背景、情境與 Jev 分層判斷 |
| 5 | 快照保存、到期結算、四種比較基準 |
| 6 | 週報、情境分組與機率可靠度表 |
| 7 | 中文郵件與 SMTP 寄送 |
| 8 | 每日流程、快取、重試與操作指令 |
| 9 | 部署檔案與整合驗收 |

每個任務先以固定資料建立失敗測試，再完成最小實作與針對性驗證。數值、日期、機率門檻以設計規格為準；以下介面與測試須涵蓋它們，不能以範例省略為由刪減需求。

## Global Constraints

- 每天台北時間 08:00 寄送；07:30 開始準備。
- AI 僅使用 TypeSafe Jev API。
- 第一版預設固定 `jev-1.13.0`。
- 每個邏輯請求最多 3 次嘗試、每日最多 9 次 HTTP 嘗試。
- 每次模型請求 45 秒上限，完整 request JSON 上限 24 KiB，07:55 停止模型工作。
- 3 天標籤以 ±2%、7 天以 ±4% 為盤整閉區間；範圍外為偏多／偏空。
- 3 天／7 天分開評分，模型／規則版本分表。
- 週報併入每週一 08:00 日報，每天合計最多一封。
- 只採用舊環境檔的 `GMAIL_ADDRESS`、`GMAIL_APP_PASSWORD`、`RECIPIENT_EMAIL`，不載入其他舊專案設定。
- 不改動其他專案，不將郵件秘密送給 Jev。
- 不自動下單、不加入其他模型、不自動升級 Jev 版本。
- 正常報告、失敗狀態、週報使用同一日工作與同一份寄送狀態機。
- RSS 每小時 05 分與每日 07:25 存檔；同一工作刷新到期的官方日曆快取，不呼叫 Jev。分析僅使用截止前已取得的內容版本。
- Binance 為結算主來源；OKX 僅檢查同分鐘 BTC-USDT 現貨價，資料品質與模型機率分開。
- 第一版納入歷史百分位、FOMC／CPI 日曆、72／168 小時新聞影響、四種比較基準、情境分組及描述性機率可靠度；不自動校準或啟用候選實驗。

目前 workspace 是尚無提交的 Git repository，分支為 `master`，`.git` 由環境管理且唯讀。實作前重新以唯讀指令檢查；仍不可寫時就地保存檔案，不刪除／取代 `.git`，不把 commit 當成功條件。以下 commit checkpoint 僅在可用且可寫的 Git checkout 執行，否則在交付紀錄註明未 commit。

## Review Focus

1. 週一與跨年邊界：以 Asia/Taipei 的到期時間歸週，不把未到期預測算錯；Task 6 固定測試。
2. SMTP DATA 後失去回覆或程序崩潰：標記 unknown，不自動產生第二封信；Tasks 7、8 固定測試。
3. 舊專案 `.env` 混入 Gemini key／DATA_DIR：只選取 mail 欄位且從不列印值；Task 1 固定測試。
4. 事後補到價格或模型改版：不改已寄週報，補正原週且版本分表；Tasks 5、6 固定測試。
5. 週一 Jev 失敗但歷史資料正常：仍提供狀態郵件與週報，零樣本不顯示 0%；Tasks 6、8 固定測試。

## File Structure and Shared Contracts

```text
pyproject.toml, requirements.lock, .gitignore, .env.example, README.md
config/sources.toml
src/btc_jev/
  __init__.py, __main__.py, cli.py
  config.py, domain.py, store.py
  sources/market.py, sources/news.py, sources/crosscheck.py, sources/__init__.py
  sources/calendars.py
  quality.py
  indicators.py, context.py, jev.py, forecast.py
  evaluation.py, baselines.py, weekly.py
  render.py, mail.py, pipeline.py
  templates/report.html.j2, templates/report.txt.j2
deploy/
  btc-jev-prepare.service, btc-jev-prepare.timer
  btc-jev-deliver.service, btc-jev-deliver.timer
  btc-jev-collect-context.service, btc-jev-collect-context.timer
  install_units.py
tests/
  conftest.py, factories.py
  test_config.py, test_sources.py, test_calendars.py, test_indicators.py, test_context.py, test_jev.py
  test_store.py, test_evaluation.py, test_weekly.py, test_quality.py
  test_baselines.py
  test_render.py, test_mail.py, test_pipeline.py, test_cli.py, test_deploy.py
  test_integration.py
  fixtures/market.json, news.xml, jev_news.json, jev_forecast.json
docs/acceptance.md
```

Public objects in `domain.py` use Pydantic validation, timezone-aware UTC datetimes in storage, and `ZoneInfo('Asia/Taipei')` at display/window boundaries:

- `Settings`: `data_dir: Path`, `timezone: str`, `jev_model: str`, `typesafe_api_key: SecretStr`, `gmail_address: SecretStr`, `gmail_app_password: SecretStr`, `recipient_email: SecretStr`. `MAIL_ENV_FILE` is an optional input path for `load_settings`, never a value in model state. CLI help/status/fixture preview must work before secrets exist; only operations needing a secret validate it.
- `Candle`: `open_at`, `close_at` (datetime), `open`, `high`, `low`, `close`, `volume` (Decimal). `close_at` is the exchange's inclusive last millisecond; valid bars satisfy `open_at <= close_at < as_of`.
- `Article`: `id`, `source`, `url`, `title`, `summary`, `content_hash` (str), `published_at`, `first_seen_at`, `fetched_at` (datetime), `truncated` (bool). Each content revision has its own first_seen_at and immutable hash.
- `QualityReport`: `status: Literal['complete','degraded','unavailable']`, `reasons: list[str]`, `rules_version: str`, `crosscheck_deviation_pct: Decimal|None`, `source_ages_seconds: dict[str,int|None]`, `news_counts: dict[str,int]`.
- `CalendarEvent`: `id`, `source`, `url`, `title`, `content_hash` (str), `scheduled_start`, `scheduled_end`, `known_at` (datetime), `time_precision: Literal['exact','date','date_range']`, `source_timezone: str`. Date-only events use a local-day half-open interval; exact events store equal start/end and use point-in-window logic.
- `ContextSummary`: `percentiles: dict[str,dict]` (value, percentile or None, start/end, sample_count, warmup), `trend: Literal['up','down','mixed','unknown']`, `volatility: Literal['low','medium','high','unknown']`, `rules_version: str`.
- `Snapshot`: `as_of`, `fetched_at` (datetime), `reference_price: Decimal`, `daily: list[Candle]`, `four_hour: list[Candle]`, `hourly: list[Candle]`, `funding: list[dict]`, `open_interest: list[dict]`, `articles: list[Article]`, `events: list[CalendarEvent]`, `calendar_health: dict[str,SourceHealth]`, `context: ContextSummary`, `source_health: dict[str,SourceHealth]`, `quality: QualityReport`.
- `Distribution`: `probabilities: dict[str, float]`, `choice: str`, `confidence: float`; keys exactly `bullish`, `sideways`, `bearish`.
- `Prediction`: `id: str`, `as_of`, `created_at`, `due_at` (datetime), `horizon_days: Literal[3,7]`, `reference_price: Decimal`, `distribution: Distribution`, `model: str`, `rules_version: str`, `label_version: str`, `config_hash: str`, `snapshot_id: str`.
- `BaselineForecast`: `id`, `name`, `version`, `label_version` (str), `as_of`, `due_at` (datetime), `horizon_days: Literal[3,7]`, `reference_price: Decimal`, `probabilities: dict[str,float]|None`, `choice: str|None`, `history_count: int`, `input_ids: list[str]`, `status: Literal['ready','uniform_fallback','unavailable']`. Uniform baseline has no directional choice.
- `Settlement`: `prediction_id: str`, `target_price: Decimal`, `return_pct: Decimal`, `actual: str`, `settled_at: datetime`, `result_version: int`.
- `RenderedMail`: `subject`, `text`, `html` (str). The job also persists the final MIME bytes, Message-ID, Date and payload hash before ready; sending retries never regenerate MIME boundaries or headers.
- `DeliveryResult`: `status: Literal['sent','retryable','failed','unknown']`, `reason: str`.
- `WeeklyReport`: `week_start`, `week_end`, `cutoff` (datetime), `groups: list[dict]`, `corrections: list[dict]`, `snapshot_id: str`.

Do not use floating point to determine threshold equality. JSON encode Decimal as strings; convert only probability calculations to float. Data structures in persisted JSON carry a schema version. Tests use injected clock, HTTP transport and SMTP factory rather than live credentials.

### Execution dependency order

Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8 → Task 9. Task 4 tests inject the declared attempt/checkpoint callbacks; they must not import the not-yet-implemented Store. Task 5 implements those contracts and Task 8 wires them. Model stages and job persistence are required parts of Tasks 4/5/8, not optional later improvements.

### Frozen data and stage contracts

- `SourceHealth`: `status: Literal['ok','error']`, `last_success_at: datetime|None`, `last_attempt_at: datetime`, `reason: str|None`, `item_count: int`. Source adapters return this structure per source; `status='error'` may still carry a fresh prior success. The `health` dictionaries above and below use these fields, serialized at storage boundaries.
- `StageResult`: `day: date`, `stage: str` (`news-1`, `news-2`, `trend`), `request_hash: str`, `model: str`, `completed_at: datetime`, `answers: dict`, `usage: dict[str,int]`. Exactly one accepted result per `(day,stage,request_hash)`.
- `Store.reserve_attempt(day: date, stage: str, request_hash: str)->bool`: one transaction checks the frozen request identity, per-stage limit 3 and per-day limit 9, then increments both before HTTP. Counts do not reset when a process restarts.
- `Store.get_stage(day: date, stage: str, request_hash: str)->StageResult|None`; `Store.save_stage(result: StageResult)->None`. First preparation freezes snapshot/request identities; conflicting identities cannot consume a fresh budget under the same date.
- `Job`: `id: str`, `day: date`, `state: str`, `lease_owner: str|None`, `lease_until: datetime|None`, `used_slots: list[int]`, `mime_bytes: bytes|None`, `message_id: str`, `created_at: datetime`, `payload_hash: str|None`, `weekly_snapshot_id: str|None`. Slots are minutes after local midnight: 480, 485, 495, 510.
- `Store.claim_delivery(day: date, slot: int, owner: str, now: datetime)->Job|None`: `BEGIN IMMEDIATE`; only ready/retryable jobs, unused valid slot, and no active sending lease. Mark sending and consume slot atomically; never hold a SQLite transaction during network I/O. Use a three-minute sending lease, longer than the two-minute SMTP operation cap.
- `Store.finish_delivery(job_id: str, owner: str, result: DeliveryResult)->None`: only the current owner may finalize sending. Recovery of an expired sending lease becomes unknown; unknown requires reconciliation and still respects used slots.
- `forecast(snapshot, client, now: Callable[[],datetime], get_stage, save_stage)->list[Prediction]`: read completion time after valid trend answers; require all inputs acquired before creation and creation strictly before 07:55. Create both horizons with one completion timestamp and persist them atomically.

### Numeric definitions

`daily[-1]` and `hourly[-1]` are latest eligible closes. Return over n bars is `100*(close[-1]/close[-1-n]-1)`. Four-hour six-bar return needs seven closes. Daily volume ratio uses the latest volume divided by the mean of the preceding 20 volumes; hourly volume change is `100*(sum(last24)/sum(previous24)-1)`. Nonpositive denominators yield missing. Twenty-day volatility uses the sample standard deviation of 20 natural-log close ratios (21 daily closes), without annualization. RSI uses first 14 changes to seed average gain/loss, then `(13*previous+current)/14`; both zero → 50, loss zero → 100, gain zero → 0. Historical calculations use the identical formulas and exclude the current observation.

## Task 1: Local configuration and validated domain objects

**Files:** Create `pyproject.toml`, `.gitignore`, `.env.example`, `src/btc_jev/{__init__,config,domain}.py`, `tests/{conftest,factories,test_config}.py`.

**Interfaces:** `select_mail_settings(values: dict[str,str]) -> dict[str,str]`; `load_settings(environ: Mapping[str,str], project_env: Path) -> Settings`; domain objects above. `tests.factories.prediction(**overrides) -> Prediction` provides a fixed valid 2026-09-21 07:30 Taipei prediction, P0=100, probabilities 0.7/0.2/0.1, pinned model and rules `v1`; `settlement(prediction, target_price='103', settled_at=None) -> Settlement` derives deterministic expected labels.

- [x] Add packaging and tests using only dummy credentials. Add tests for quoted env values, environment precedence, required names, secret-safe repr, invalid timezone, and mail-only extraction:

```python
from btc_jev.config import select_mail_settings

def test_mail_environment_does_not_import_old_project():
    actual = select_mail_settings({
        'GMAIL_ADDRESS': 'sender@example.org',
        'RECIPIENT_EMAIL': 'reader@example.org',
        'GMAIL_APP_PASSWORD': 'fake-password',
        'GEMINI_API_KEY': 'must-not-load',
        'DATA_DIR': '/wrong/project',
    })
    assert set(actual) == {'GMAIL_ADDRESS', 'RECIPIENT_EMAIL', 'GMAIL_APP_PASSWORD'}
```

- [x] Create local `.venv`, install runtime/test requirements, and run `.venv/bin/python -m pytest tests/test_config.py -q`; verify the new behavior fails before implementing it.
- [x] Implement dotenv parsing without shell evaluation, mail allowlist and explicit precedence: process env → project env → mail-file mail fields. Do not read the real mail env in tests. Use `SecretStr` and redacted error messages. Core selection:

```python
MAIL_KEYS = ('GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'RECIPIENT_EMAIL')

def select_mail_settings(values):
    return {key: values[key] for key in MAIL_KEYS if key in values}
```

- [x] Implement domain validators: aware timestamps, finite positive prices, supported horizon, due_at equals as_of + horizon, probabilities and matching winner, immutable prediction fields. Match any maximum on a probability tie without rewriting the provider's choice. Implement the SourceHealth, StageResult and Job contracts above. Add .gitignore entries for `.env`, `.venv`, `data/`, caches, and logs.
- [x] Run `.venv/bin/python -m pytest tests/test_config.py -q`; record passing result. Commit checkpoint: `feat: add isolated BTC report configuration and domain contracts`. Verified 27 config tests and 115 total tests; no commit because `.git` is read-only and has no HEAD, as permitted by Global Constraints.

## Task 2: Timestamped market, news and calendar acquisition

**Files:** Create `config/sources.toml`, `src/btc_jev/sources/{__init__,market,news,calendars}.py`, `tests/{test_sources,test_calendars}.py`, `tests/fixtures/{market.json,news.xml,fomc.html,cpi.html}`.

**Interfaces:** `MarketClient(http: httpx.Client).collect(as_of: datetime) -> dict`; `MarketClient.price_at(boundary: datetime) -> Decimal|None`; `NewsClient(http: httpx.Client).collect(run_at: datetime) -> tuple[list[Article],dict[str,SourceHealth]]`; `CalendarClient(http).collect(run_at: datetime)->tuple[list[CalendarEvent],dict[str,SourceHealth]]`; `event_in_window(event: CalendarEvent, as_of: datetime, horizon_days: int)->bool`; `closed_candles(candles: list[Candle], as_of: datetime) -> list[Candle]`; `build_snapshot(market: dict, articles: list[Article], health: dict, as_of: datetime, fetched_at: datetime, quality: QualityReport, events: list[CalendarEvent], calendar_health: dict, context: ContextSummary) -> Snapshot` in `sources/market.py`.

- [x] Create synthetic fixtures with explicit timestamps and OHLC values. Test each source URL/query using `httpx.MockTransport`, not captured credentials. Pin incomplete candle exclusion:

```python
from datetime import datetime, timezone
from btc_jev.domain import Candle
from btc_jev.sources.market import closed_candles

def test_open_candle_is_not_a_feature():
    end = datetime(2026, 9, 21, tzinfo=timezone.utc)
    c = Candle(open_at=end, close_at=end.replace(hour=1),
               open='100', high='102', low='99', close='101', volume='2')
    assert closed_candles([c], end) == []
```

- [x] Run `.venv/bin/python -m pytest tests/test_sources.py -q`; confirm failure.
- [x] Implement documented Binance endpoints and windows from spec. Paginate until sufficient history, sort/dedupe by timestamps, reject gaps, check OHLC ordering, finite prices, nonnegative volume, and reference minute exactness. OI comparisons choose the latest observation at or before each target, no more than one hour late; record actual timestamps. Fixed host allowlists, 20-second whole-request timeout, max three attempts for transient errors; stop collection at 07:40. Only accept candles whose close timestamp is strictly before the cutoff boundary:

```python
def closed_candles(candles, as_of):
    return sorted((c for c in candles if c.close_at < as_of),
                  key=lambda c: c.open_at)
```

- [x] Implement RSS parsing with per-source status, valid timezone conversion, published_at cutoff, summary stripping, URL/title/hash dedupe and deterministic 20-item selection with source diversity. A successful empty feed is healthy; malformed or unreachable feed is not. Preserve content revisions and acquisition time; do not overwrite earlier text under the same URL. Add cases for updated items, missing dates, future dates, identical URLs and malicious HTML.
- [x] Test Binance pagination, stale funding, BTC-vs-USDT OI fields, 429 Retry-After, wrong content-type, empty RSS and one failed RSS. Run source tests; commit checkpoint: `feat: collect timestamped BTC market and news snapshots`.
- [x] Add FOMC/CPI HTML fixtures and tests for explicit New York time, date-only/multi-day meetings, missing time, changed publication schedule and parsing failure. Verify a future event known before cutoff is included, while a revised schedule first acquired after cutoff is excluded:

```python
from datetime import datetime, timedelta, timezone
from btc_jev.domain import CalendarEvent
from btc_jev.sources.calendars import event_in_window

def test_future_calendar_event_requires_prior_knowledge():
    cutoff = datetime(2026, 9, 21, tzinfo=timezone.utc)
    event = CalendarEvent(id='cpi-test', source='bls', url='https://www.bls.gov/',
        title='Synthetic CPI', content_hash='fixture',
        scheduled_start=cutoff + timedelta(days=2),
        scheduled_end=cutoff + timedelta(days=2), known_at=cutoff - timedelta(hours=1),
        time_precision='exact', source_timezone='America/New_York')
    assert event_in_window(event, cutoff, 3)
    assert not event_in_window(event.model_copy(update={
        'known_at': cutoff + timedelta(seconds=1)}), cutoff, 3)
```

- [x] Run `.venv/bin/python -m pytest tests/test_calendars.py -q` before implementation. Parse only the two official source pages, retain full version metadata, and convert via ZoneInfo. Use `(as_of, as_of+h]` for exact event points; use interval overlap for date ranges, never infer an unavailable time. Implement core selection:

```python
def event_in_window(event, as_of, horizon_days):
    from datetime import timedelta
    if event.known_at > as_of:
        return False
    end = as_of + timedelta(days=horizon_days)
    if event.time_precision == 'exact':
        return as_of < event.scheduled_start <= end
    return event.scheduled_end > as_of and event.scheduled_start <= end
```

- [x] Add DST tests (08:30 New York in January → 13:30 UTC, July → 12:30 UTC), empty valid calendar vs broken parser, and no numeric CPI result field. Run both source/calendar suites; commit checkpoint: `feat: archive known official macro event schedules`.

## Task 3: Crosscheck and explicit data quality

**Files:** Create `src/btc_jev/sources/crosscheck.py`, `src/btc_jev/quality.py`, `tests/test_quality.py`; extend `tests/test_sources.py`.

**Interfaces:** `CrosscheckClient(http).price_at(boundary: datetime)->Decimal|None`; `classify_quality(required_ok: bool, crypto_fresh_count: int, fed_fresh: bool, deviation_pct: Decimal|None, calendars_fresh: bool)->str`; `assess_quality(market: dict, news_health: dict, calendar_health: dict, crosscheck_price: Decimal|None, as_of: datetime)->QualityReport`.

- [x] Pin quality thresholds before implementation:

```python
from decimal import Decimal
from btc_jev.quality import classify_quality

def test_partial_news_does_not_invent_complete_quality():
    assert classify_quality(True, 1, True, Decimal('0.1'), True) == 'degraded'
    assert classify_quality(True, 2, True, Decimal('0.3'), True) == 'complete'
    assert classify_quality(True, 2, True, Decimal('1'), True) == 'degraded'
    assert classify_quality(True, 2, True, Decimal('1.0001'), True) == 'unavailable'
    assert classify_quality(True, 0, True, None, True) == 'unavailable'
    assert classify_quality(True, 2, True, Decimal('0.1'), False) == 'degraded'
```

- [x] Run `.venv/bin/python -m pytest tests/test_quality.py -q`; confirm failure. Implement the policy, without modifying model probabilities:

```python
from decimal import Decimal

def classify_quality(required_ok, crypto_fresh_count, fed_fresh, deviation_pct, calendars_fresh):
    if not required_ok or crypto_fresh_count == 0:
        return 'unavailable'
    if deviation_pct is not None and deviation_pct > Decimal('1'):
        return 'unavailable'
    if (crypto_fresh_count == 2 and fed_fresh and calendars_fresh
            and deviation_pct is not None and deviation_pct <= Decimal('0.3')):
        return 'complete'
    return 'degraded'
```

- [x] Implement OKX history-candles for BTC-USDT 1m; validate `code='0'`, `confirm='1'`, exact 07:29 open timestamp, positive close, same quote unit. Limit to three HTTP attempts and the market collection deadline. A missing crosscheck gives degraded, not fabricated agreement. Add wrong-minute, unconfirmed-bar and failure tests.
- [x] Compute source age from latest successful fetch completed before cutoff; freshness ≤5400 seconds, healthy empty feed allowed. Record all reasons, source times and dedupe counts. Never trim an actual market jump merely because it is large. Add a latest-request-failed but fresh-cache case.
- [x] Treat calendar freshness separately at ≤24 hours, with missing/expired calendar causing degraded rather than unavailable. Historical percentile warmup is reported independently and must not invalidate otherwise complete basic market data. Test both boundaries.
- [x] Run quality/source tests. Commit checkpoint: `feat: validate source freshness and cross-exchange price agreement`.

## Task 4: Indicators, historical context and bounded Jev forecasting

**Files:** Create `src/btc_jev/{indicators,context,jev,forecast}.py`, `tests/{test_indicators,test_context,test_jev}.py`, `tests/fixtures/{jev_news,jev_forecast}.json`.

**Interfaces:** `compute_features(snapshot: Snapshot) -> dict`; `validate_distribution(payload: dict) -> Distribution`; `is_direction_clear(distribution: Distribution) -> bool`; `JevClient(http, settings, reserve_attempt: Callable[[str,str],bool], now: Callable[[],datetime]).ask(state: dict, questions: dict, stage: str, deadline: datetime) -> dict`; `forecast(snapshot: Snapshot, client: JevClient, now: Callable[[],datetime], get_stage, save_stage) -> list[Prediction]`. Callbacks bind the report date to the Store methods above; JevClient computes the canonical full request hash and passes `(stage,request_hash)` to the reservation callback.

`context.py`: `historical_percentile(value: Decimal, earlier_values: list[Decimal])->Decimal|None`; `build_context(market: dict, historical_features: dict, as_of: datetime)->ContextSummary`. The caller selects preceding, comparable observations before invoking the pure percentile helper; it records actual history period/sample count and current-value exclusion.

- [x] Add reference-valued tests for SMA/RSI/return/volume/OI changes, constant price RSI=50, all rises RSI=100, all falls RSI=0. Define RSI seed as arithmetic mean of first 14 gains/losses, then Wilder update. Add contract tests rejecting NaN, missing choices, wrong IDs and secret-bearing state.

```python
import pytest
from btc_jev.jev import validate_distribution

def test_rejects_non_normalized_probabilities():
    with pytest.raises(ValueError):
        validate_distribution({'type': 'choice', 'choice': 'bullish',
            'confidence': 0.7,
            'probabilities': {'bullish': 0.7, 'sideways': 0.4, 'bearish': 0.2}})
```

- [x] Run `.venv/bin/python -m pytest tests/test_indicators.py tests/test_jev.py -q`; confirm new tests fail.
- [x] Implement feature math using the frozen snapshot. Specify daily returns from latest completed daily close and close N periods earlier; volume baseline excludes the latest complete day. Add latest completed hourly close 1/4/24-hour returns and 24-hour volume versus the preceding 24 hours from 200 hourly bars. Label the separate daily/hourly cutoff times. In a test with flat daily bars but an overnight hourly rally, hourly return must be positive while daily return stays zero.
- [x] Compute semantic fields from exact numbers: `reference_vs_sma20`, `sma20_vs_sma60`, `oi_24h_direction`, `funding_sign` with positive/negative/zero or above/below/equal states. Jev receives both values and labels. Add exact equality and missing-value tests; missing must never imply neutral.
- [x] Add percentile tie/warmup tests, then implement deterministic midrank percentiles:

```python
from decimal import Decimal

def historical_percentile(value, earlier_values):
    if len(earlier_values) < 30:
        return None
    lower = sum(x < value for x in earlier_values)
    tied = sum(x == value for x in earlier_values)
    return Decimal(100) * (Decimal(lower) + Decimal(tied) / 2) / len(earlier_values)

# Test expectations (import the actual function in test_context.py):
assert historical_percentile(Decimal('5'), [Decimal('5')] * 30) == 50
assert historical_percentile(Decimal('5'), [Decimal('5')] * 29) is None
```

- [x] Test context construction excluding current/future observations and unlike funding intervals, OI's limited actual history and the daily sampling hour. Use the spec's four feature definitions, target 90-day lookback without claiming unavailable coverage, and record warmup per feature. Build fixed up/down/mixed trend and low/medium/high/unknown volatility tags; test 25/75 percentile boundaries and unknown history. Run `.venv/bin/python -m pytest tests/test_context.py tests/test_indicators.py -q`.
- [x] Build two news batches at most, then one trend call. Include full horizon/threshold definitions in instructions, never only in question keys. Actual request pattern:

```python
questions = {
    'trend_3d': {
        'type': 'choice',
        'instructions': 'Estimate BTCUSDT return from reference_price at as_of '
                        'to exactly 72 hours later using only the supplied state.',
        'criteria': {
            'bullish': 'Return strictly greater than +2 percent.',
            'sideways': 'Return between -2 and +2 percent inclusive.',
            'bearish': 'Return strictly less than -2 percent.',
        },
    },
}
```

- [x] Add corresponding 168-hour question with ±4% criteria; save original probabilities and raw choice. Accept any maximum option for exact ties, mark direction unclear and score the saved choice. Validate Noul through its `noul` field without expecting confidence; require response model to equal the pinned model. Apply UTF-8 request-size cap to actual serialized bytes, request/day attempt limits, deadline and documented transient statuses. Reserve attempts before network calls, including timeouts.
- [x] Add news text-kind Choice (reported event, prediction/opinion, unverified claim, insufficient information). Filter second-stage news on Noul relevance ≥0.5, preserving full classification distributions and exclusion counts; this is relevance filtering, not verified truth. Reject quality unavailable before AI forecasting; include degraded reasons explicitly. Test hostile article instructions cannot alter hosts, model settings, thresholds or program behavior. Keep three logical requests and nine HTTP attempts; trim lowest-priority articles if extra questions exceed 24 KiB.
- [x] Create distinct 72-hour and 168-hour news impact Choice questions; each references the article and cutoff explicitly, with positive/negative/neutral/unclear choices. Stage two reads the matching news answers, ContextSummary and known calendar windows. Test that a neutral 7-day answer need not match a positive 3-day answer, and that no question depends on another result within the same request. Preserve stage boundaries and the original request cap.
- [x] Persist validated stage results and reuse them on restart. An exhausted nonempty news batch yields a status report, with no trend call; healthy empty news makes zero news calls and one trend call. Enforce whole-call 45-second cancellation and absolute 07:55 cutoff even if response bytes keep arriving. Test a callback returning at 07:55:00 cannot create a prediction, and three failed attempts remain exhausted after client/store recreation.
- [x] Test 401/422 no retry, 429/529 finite retry, deadline before Retry-After, 24 KiB trimming, pinned model unavailable, news-only completion, zero news, and uncertain distribution policy. Run targeted tests; commit checkpoint: `feat: add bounded Jev news and trend analysis`.

## Task 5: Persistent predictions, settlement and comparison baselines

Task 1 review carry-forward (Minor): `BaselineForecast(status='uniform_fallback')` currently validates the distribution but does not require equal probabilities. Add a rejection test and enforce equal probabilities when implementing baseline generation; do not claim a nonuniform distribution is uniform fallback.

**Files:** Create `src/btc_jev/{store,evaluation,baselines}.py`, `tests/{test_store,test_evaluation,test_baselines}.py`.

**Interfaces:** `Store(path: Path)` with `save_snapshot(snapshot)->str`, `save_predictions(predictions)->None`, `pending_settlements(as_of)->list[Prediction]`, `save_settlement(settlement)->None`, `reserve_attempt(day: date, stage: str, request_hash: str)->bool`, `evaluated_records(cutoff: datetime)->list[tuple[Prediction,Settlement]]`, `save_articles(articles, health, completed_at)->None`, `cached_news(as_of: datetime)->tuple[list[Article],dict]`, `get_snapshot(snapshot_id: str)->Snapshot`; `classify_return(return_pct: Decimal, horizon_days: int)->str`; `brier_score(probabilities: dict[str,float], actual: str)->float`; `settle(prediction: Prediction, target_price: Decimal, settled_at: datetime)->Settlement`. Store also implements the stage and job contracts above. `get_snapshot` is used for active work within retention; after text expiry, preview reports incomplete replay explicitly and scoring reads numeric records directly.

Additional Store methods: `save_calendar(events, health, completed_at)->None`, `cached_calendar(as_of)->tuple[list[CalendarEvent],dict]`, `save_anchor(as_of, price, fetched_at)->None`, `historical_outcomes(horizon_days, as_of, label_version)->list[dict]`, `historical_features(as_of)->dict`, `save_baselines(forecasts: list[BaselineForecast])->None`, `baselines_for(as_of,horizon_days)->list[BaselineForecast]`. Historical outcomes carry source anchor IDs, target time and acquisition time; only both prices known strictly before as_of are eligible.

`baselines.py`: `historical_probabilities(labels: list[str])->tuple[dict[str,float],str]`; `build_baselines(snapshot: Snapshot, horizon_days: int, history: list[dict])->list[BaselineForecast]`.

- [x] Add tests for threshold equality, idempotent insertion, immutable original forecast, exact maturity minute, missing maturity price, migrations and atomic daily attempt reservation.

```python
from decimal import Decimal
from btc_jev.evaluation import classify_return, brier_score

def test_threshold_equality_is_sideways():
    assert classify_return(Decimal('2'), 3) == 'sideways'
    assert classify_return(Decimal('-4'), 7) == 'sideways'
    assert classify_return(Decimal('4.0001'), 7) == 'bullish'

def test_brier_perfect_and_wrong():
    p = {'bullish': 1.0, 'sideways': 0.0, 'bearish': 0.0}
    assert brier_score(p, 'bullish') == 0.0
    assert brier_score(p, 'bearish') == 2.0
```

- [x] Run `.venv/bin/python -m pytest tests/test_store.py tests/test_evaluation.py -q`; confirm failure.
- [x] Implement schema migrations, foreign keys, WAL, unique forecast `(as_of,horizon,model,rules_version)`, immutable payload hashes, and versioned settlements. Schema tables: metadata, snapshots, predictions, settlements, source_runs, daily_attempts, model_stages, stage_attempts, jobs, weekly_reports, weekly_report_samples, text_blobs. Store full precision numeric strings. Save the two prediction horizons in one transaction; test a failure on the second insert rolls both back.
- [x] Add article_versions and news_fetches tables. Every distinct content hash has immutable first_seen/completed timestamps. `cached_news` selects latest content version acquired by cutoff, never a later correction with backdated published_at; freshness includes successful empty fetches. Save quality and source-selection policy version in snapshots. Test first_seen=07:31 excluded from 07:30 even when published_at=06:00; a 07:29 version is eligible while its 07:31 revision is not.
- [x] Add calendar_versions/calendar_fetches, price_anchors, historical_features and baseline_forecasts. Preserve schedule revisions and exact-time precision, model/rules/label/config identity and original features. Save each valid 07:30 anchor even when later news/AI work fails. Historical outcomes use retained same-hour anchor pairs, never only successful Jev days. Test excluding a just-matured outcome fetched after the baseline cutoff.
- [x] Implement return classification and score directly:

```python
from decimal import Decimal

def classify_return(return_pct, horizon_days):
    limit = {3: Decimal('2'), 7: Decimal('4')}[horizon_days]
    return 'bullish' if return_pct > limit else 'bearish' if return_pct < -limit else 'sideways'

def brier_score(probabilities, actual):
    return sum((probabilities[k] - float(k == actual)) ** 2
               for k in ('bullish', 'sideways', 'bearish'))
```

- [x] Settle only after due_at, using exact archived/retrieved reference minute from the same market. Missing candle remains pending; late lookup retains original due_at. Add retention with references: never delete unresolved work or numeric samples needed by retained weekly snapshots. Keep text in expiring blobs, including copies embedded in model state, MIME and snapshots; retain hashes and expiry metadata without falsely claiming replay. Add a 91-day test where the article text is absent from every persisted payload, scores still match, and a pending unknown job keeps reconciliation data. Run tests; commit checkpoint: `feat: persist and settle immutable BTC forecasts`.
- [x] Add warmup and smoothing tests for the historical baseline:

```python
import pytest
from btc_jev.baselines import historical_probabilities

def test_history_baseline_is_smoothed_and_explicit_about_warmup():
    p, status = historical_probabilities(['bullish'] * 29)
    assert status == 'uniform_fallback'
    assert p['bullish'] == pytest.approx(1 / 3)
    p, status = historical_probabilities(['bullish'] * 30)
    assert status == 'ready'
    assert p['bullish'] == pytest.approx(31 / 33)
    assert p['bearish'] == pytest.approx(1 / 33)
```

- [x] Run `.venv/bin/python -m pytest tests/test_baselines.py -q` and confirm failure. Implement the formula plus the three other baselines:

```python
def historical_probabilities(labels):
    keys = ('bullish', 'sideways', 'bearish')
    if len(labels) < 30:
        return dict.fromkeys(keys, 1 / 3), 'uniform_fallback'
    return {k: (labels.count(k) + 1) / (len(labels) + 3) for k in keys}, 'ready'
```

- [x] Build always-sideways one-hot, uniform with choice=None, and horizon-specific hourly momentum one-hot. History selection is time-ordered and at most 365 days; a tie in non-warmup historical argmax uses sideways, bullish, bearish order and retains all probabilities. Uniform fallback has choice=None. This tie rule is separate from Jev's raw Choice validation. Save all four baseline records before inference, with status and source inputs; missing momentum anchor yields unavailable. Test horizons, exact threshold equality, future-label exclusion, immutable stored baselines and common-sample scoring. Run baseline/store/evaluation tests; commit checkpoint: `feat: freeze auditable forecast comparison baselines`.

## Task 6: Weekly performance reports and late corrections

**Files:** Create `src/btc_jev/weekly.py`, `tests/test_weekly.py`; extend `store.py` weekly snapshot methods.

**Interfaces:** `previous_week_window(now: datetime)->tuple[datetime,datetime]`; `build_weekly_report(store: Store, now: datetime, cutoff: datetime)->WeeklyReport`; `Store.save_weekly_report(report)->None`, `Store.get_weekly_report(week_start)->WeeklyReport|None`. Group dictionaries carry horizon, model, rules_version, window, settled_count, correct_count, accuracy (float or None), brier (float or None), baselines, confusion, clear/unclear counts, pending_count and sample IDs.

`reliability_bin(probability: float)->int`; `reliability_table(records: list[tuple[Prediction,Settlement]])->dict` returns per-class rows with n, mean_probability, event_rate and small_sample. Weekly snapshots also freeze context regime, quality policy, baseline versions and label_version.

- [x] Pin a timezone/cross-year example and startup zero-sample behavior:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from btc_jev.weekly import previous_week_window

def test_previous_week_crosses_year_in_taipei():
    tz = ZoneInfo('Asia/Taipei')
    start, end = previous_week_window(datetime(2027, 1, 4, 7, 30, tzinfo=tz))
    assert start == datetime(2026, 12, 28, tzinfo=tz)
    assert end == datetime(2027, 1, 4, tzinfo=tz)
```

- [x] Add factory-based tests: a Sunday-generated 7-day forecast due next Sunday is not scored in the earlier week; a result due last Sunday fetched Tuesday belongs to last week; two models yield two groups. Empty group has `accuracy is None`, not zero. Run `.venv/bin/python -m pytest tests/test_weekly.py -q`; confirm failure.
- [x] Implement calendar window with local midnight and end-exclusive filtering:

```python
from datetime import timedelta
from zoneinfo import ZoneInfo

def previous_week_window(now):
    local = now.astimezone(ZoneInfo('Asia/Taipei'))
    end = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end -= timedelta(days=end.weekday())
    return end - timedelta(days=7), end
```

- [x] Aggregate only settlements known by cutoff; group by horizon/model/rules/label, filter by due_at. Compute counts, accuracy, confusion and Brier, plus the four frozen baselines on each common sample set. Uniform has no classification accuracy; missing and warmup baselines are explicit. Display current week, previous four complete weeks and retained cumulative range. Both clear and unclear predictions remain included. Missing scheduled predictions are computed from activation metadata, separately from pending settlements and future maturities.
- [x] Freeze report JSON, sample/result-version references and hash atomically. Compare prior snapshots against later settlement revisions to form original-week corrections; never rewrite sent snapshots. Add tests for zero records, all incorrect, one incorrect high-confidence prediction, multiple versions, no mail delivery, missed Monday, retained-history boundaries and no additional Jev request.
- [x] Join saved snapshot quality, displaying complete/degraded sample counts and metrics plus unavailable/missing prediction-day counts. Group by quality-policy version as well as model/forecast rules where policies differ. Verify subgroup settled counts sum to main settled count; no after-the-fact exclusion of degraded forecasts. Cross-version rows include actual covered dates and do not claim causal improvement.
- [x] Keep direction-clear groups separate from confidence groups (confidence ≥0.55 is the high group). Compute missing forecasts for each horizon by scheduled origin day whose due_at lies in the report window, starting at activation metadata; future maturities are a separate cutoff count. Test that a 7-day horizon is not counted missing during its startup week and that warmup uniform baselines have Brier but no accuracy.
- [x] Add frozen trend and volatility groups independently, including unknown; do not generate every combination. Implement cumulative per-class reliability tables with fixed intervals, zero-sample None values and n<30 warnings. Verify boundary assignment:

```python
from btc_jev.weekly import reliability_bin

def test_probability_bin_boundaries():
    assert [reliability_bin(p) for p in (0, .2, .4, .6, .8, 1)] == [0, 1, 2, 3, 4, 4]
```

- [x] Run the new test before implementing `reliability_bin` as the count of thresholds `.2,.4,.6,.8` less than or equal to p. Validate p in [0,1], compute mean probability and class occurrence count separately, and test that n totals per class equal all scored predictions. One prediction appears once per class, not as three independent observations. Raw probabilities remain unchanged; no calibration model is fitted.
- [x] Run weekly and evaluation tests; commit checkpoint: `feat: add auditable weekly Jev performance reports`.

## Task 7: Traditional Chinese mail rendering and SMTP delivery

**Files:** Create `src/btc_jev/{render,mail}.py`, `src/btc_jev/templates/{report.html.j2,report.txt.j2}`, `tests/{test_render,test_mail}.py`.

**Interfaces:** `render_report(day: date, predictions: list[Prediction], details: dict, weekly: WeeklyReport|None, failure: str|None)->RenderedMail`; `build_mime(rendered: RenderedMail, settings: Settings, message_id: str, created_at: datetime)->bytes`; `send_mail(mime_bytes: bytes, settings: Settings, smtp_factory=SMTP_SSL)->DeliveryResult`. `build_mime` is called once before ready; `send_mail` consumes the persisted bytes.

- [x] Test normal/uncertain/failed reports, weekly suffix, original English headline with Chinese labels, hidden secrets and escaping. Establish exact text examples:

```python
from datetime import date
from btc_jev.render import render_report

def test_failure_mail_has_no_invented_prediction():
    mail = render_report(date(2026, 9, 21), [], {}, None, '新聞來源無法取得')
    assert '分析未完成' in mail.subject
    assert '新聞來源無法取得' in mail.text
    assert '命中率：0%' not in mail.text
```

- [x] Add a fake SMTP that accepts MAIL/RCPT then raises timeout during DATA; assert `unknown`. Before DATA connection failure must be `retryable`; authentication failure must be `failed`. Run `.venv/bin/python -m pytest tests/test_render.py tests/test_mail.py -q`; confirm failure.
- [x] Explicit negative SMTP replies remain classifiable: DATA 4xx → retryable, DATA 5xx → failed, final DATA 250 → sent even if QUIT then fails; only lost/malformed acceptance replies are unknown. Assert retries send byte-identical MIME and preserve Date. Reject CR/LF in envelope and subject fields; display only http/https article links, never javascript/data URLs.
- [x] Implement Jinja autoescape, both MIME alternatives and `smtp.gmail.com:465` with TLS certificate verification. Track DATA-start separately from connection errors:

```python
# Inside send_mail, after successful MAIL and RCPT:
data_started = True
code, response = smtp.data(mime_bytes)
# Connection loss with data_started is unknown, never retryable.
```

- [x] Weekly template prints `correct / settled` beside percentage, all source timestamps, sample-size labels, version headings, corrections and missing counts. No samples renders `尚無可評估資料`. Brier includes a short Chinese explanation: `機率誤差，越低越好`.
- [x] Daily template prominently prints complete/degraded/unavailable, OKX deviation or missing, each source's freshness, and article source/text-kind labels. Keep Jev probabilities unchanged when quality is degraded. Test missing deviation does not render as 0%, and media classification does not render as fact-checked.
- [x] Render historical percentile with actual period/n or warmup, separate trend/volatility labels, and upcoming FOMC/CPI events with local date precision. Weekly cumulative section adds four-baseline comparisons, regime groups and reliability table. Test date-only events never acquire invented times, history fallback never claims a trained prior, and event schedules contain no future economic results.
- [x] Verify multipart alternatives carry identical substantive facts, external text cannot inject headers or markup, Message-ID preserved and no second AI call. Run tests; commit checkpoint: `feat: render and deliver daily and weekly BTC email`.

## Task 8: Daily orchestration, concurrency and CLI

**Files:** Create `src/btc_jev/{pipeline,cli,__main__}.py`, `tests/{test_pipeline,test_cli}.py`; extend `store.py` job state and weekly attachments; add console script in `pyproject.toml`.

**Interfaces:** `collect_context(store, news, calendars, now)->None` archives RSS and due calendar refreshes with no Jev/SMTP; `prepare_daily(store, settings, market, crosscheck, jev, now)->str` returns job ID using `store.cached_news(as_of)` and `store.cached_calendar(as_of)`; `deliver_due(store, settings, now, smtp_factory)->DeliveryResult|None`; `reconcile(store, job_id, result: Literal['sent','not-sent'])->None`; `cli.main(argv: list[str]|None=None)->int`.

- [x] Add injected-clock tests for 07:30 preparation, 07:55 cutoff, 08:00 send, retries at 08:05/08:15/08:30 and no catch-up after deadline. Slots allow claims only during their own minute; test 08:30:01 allowed, 08:31:00 rejected, second claim in one slot rejected. Add CLI tests for status/check-config/preview making zero network requests.

```python
from btc_jev.cli import main

def test_cli_help_is_offline(capsys):
    import pytest
    with pytest.raises(SystemExit) as exc:
        main(['--help'])
    assert exc.value.code == 0
    assert 'preview' in capsys.readouterr().out
```

- [x] Add Monday scenario with valid historical settlements and a Jev 529 exhausted: assert one status mail containing Weekly Report, unchanged history and no second job. Add two independent SQLite connections claiming the same job: exactly one can enter sending. Run `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_cli.py -q`; confirm failure.
- [x] Implement state transitions with `BEGIN IMMEDIATE`, unique day key and lease owner. State set: preparing, ready, sending, retryable, sent, failed, unknown, missed. Persist payload/weekly hash/Message-ID before ready. A sending job recovered after crash becomes unknown, even if transmission cannot be confirmed. Failed prepare can still build a ready status-mail job.
- [x] Provide exact commands: `collect`, `prepare`, `preview`, `send --due`, `evaluate`, `status`, `check-config`, `reconcile JOB_ID --result sent|not-sent`, `cleanup`. `preview --fixture PATH` renders explicit synthetic fixtures; manual prepare produces previewable output but cannot replace a frozen/sent daily job. `reconcile not-sent` only enables retry within the current day's window.
- [x] Add `collect-context` for scheduled RSS/calendar collection. RSS is fetched each run; calendars refresh after six hours or forcibly at 07:25, with independent source health. `prepare` does not fetch post-cutoff text to replace missing cache. Both crypto feeds stale/missing yields a status report; fresh but empty feeds allow trend inference with explicit zero-news context. Only missing calendar yields degraded analysis. Integration tests count zero Jev/SMTP calls from all collection runs and cover cache/version races.
- [x] Recover expired preparing jobs with a status payload at the delivery slot, attaching the frozen Monday weekly report when available. Do not overwrite ready/sent payloads or recover an active sending lease. Test a kill before 07:55, fallback at 08:00, no repeat model calls for saved stages, and unknown after a crash during DATA. Freeze the MIME payload and weekly reference in the same ready transaction.
- [x] Persist the reference price first, build comparable history context and quality, construct Snapshot, freeze baselines, then invoke Jev. A model failure must not erase saved anchors or baselines. Snapshot context creation uses past observations and current frozen features; adding a newer historical-feature row after prepare must not mutate the forecast.
- [x] Settle historical predictions first through `MarketClient.price_at(prediction.due_at)` and `Store.save_settlement`; source failures leave each pending independently. Generate weekly data on Mondays before daily AI work so a model failure does not remove history. Reuse saved weekly snapshot on restart. Weekly delivery uses the daily job retry state; after Monday window mark missed without creating a Tuesday job. Run pipeline, store and CLI tests; commit checkpoint: `feat: orchestrate bounded daily jobs and weekly attachments`.

## Task 9: Deployment assets and end-to-end acceptance

**Files:** Create all `deploy/` files, `tests/{test_deploy,test_integration}.py`, `requirements.lock`, `docs/acceptance.md`; update existing `README.md`.

**Interfaces:** `install_units.render(project: Path, destination: Path)->list[Path]` produces units only; separate explicit install and enable commands. Entry points call `btc-jev prepare` and `btc-jev send --due` using absolute venv paths.

- [x] Test rendered units for absolute paths with spaces, Taipei timezone, independent service names and no foggy service mutation. Integration setup uses temporary SQLite, `httpx.MockTransport`, injected clock, synthetic RSS/Jev responses and local fake SMTP; run once through prepare/send/settle/next-week prepare/send.
- [x] Run `.venv/bin/python -m pytest tests/test_deploy.py tests/test_integration.py -q`; confirm failure before implementing deployment rendering.
- [x] Write exact calendar entries:

```ini
# btc-jev-prepare.timer
[Timer]
OnCalendar=*-*-* 07:30:00 Asia/Taipei
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0

# btc-jev-deliver.timer
[Timer]
OnCalendar=*-*-* 08:00:00 Asia/Taipei
OnCalendar=*-*-* 08:05:00 Asia/Taipei
OnCalendar=*-*-* 08:15:00 Asia/Taipei
OnCalendar=*-*-* 08:30:00 Asia/Taipei
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0

# btc-jev-collect-context.timer
[Timer]
OnCalendar=*-*-* *:05:00 Asia/Taipei
OnCalendar=*-*-* 07:25:00 Asia/Taipei
Persistent=false
AccuracySec=1s
RandomizedDelaySec=0
```

- [x] Services use `Type=oneshot`, `UMask=0077`, `NoNewPrivileges=true`, `PrivateTmp=true`, current project WorkingDirectory and its own EnvironmentFile. Preparation TimeoutStartSec=26min permits cleanup after the application stops model work at 07:55; delivery TimeoutStartSec=2min. Application clocks enforce absolute cutoffs independently of service timeout. Installer refuses mismatching existing units, render/install/enable are separate operations.
- [x] Context collection uses TimeoutStartSec=3min, same isolated configuration and `btc-jev collect-context`. Verify its timer, six-hour calendar refresh and 07:25 forced refresh, no catch-up for missed hourly runs and versioned cache under concurrent prepare. Start collection before the first intended morning cutoff. No experiment/calibration timer is installed.
- [x] Extend the existing README while preserving design/plan/research links; document actual operations, sources, arbitrary thresholds, probability vs accuracy, warmup, calendar precision, four baselines, weekly cohorts, secret allowlist, unknown reconciliation, stop commands, missed windows, retention and pinned upgrades. Research stays explicitly out of the first-release acceptance scope. Lock dependencies from the verified environment rather than copying another project's environment wholesale.
- [x] Run `.venv/bin/python -m pytest -q`, `.venv/bin/python -m compileall -q src tests deploy`, `.venv/bin/python -m pip check`; render units into a task-specific temporary directory and run `systemd-analyze --user verify` on the explicit generated paths. Record unavailable systemd verification as unverified rather than passed. Do not enable services as part of syntax checks.
- [x] Verify acceptance cases: regular day one email, Monday one combined email, pending maturity, late correction, failed source, Jev outage, missed window, SMTP unknown and restart without duplicate. Write actual results and remaining external checks to `docs/acceptance.md`. Commit checkpoint: `feat: package scheduled BTC daily and weekly reports`.

## Final Live Validation and Handoff

- [x] Confirm implemented output through offline HTML/text preview and full test results before requesting or using live credentials. Keys remain in local env and never in chat.
- [ ] When live use is authorized, use `check-config`, one bounded real Jev prepare and local preview; record actual model and usage. No undocumented model substitution.
- [ ] Sending the first real mail and enabling recurring timers are deployment actions after the concrete preview is reviewable. Preserve any authorization already supplied in the session; do not treat this planning request alone as timer activation.
- [ ] Report tested behavior separately from real inbox delivery and scheduled execution. Weekly performance becomes available only after real forecasts mature; synthetic tests do not prove prediction accuracy.

## Plan Self-Review and Execution Choice

Coverage: configuration/isolation → Task 1; market/RSS/calendar versions and time handling → Task 2; crosscheck/freshness gates → Task 3; hourly features/percentiles/regimes/horizon-specific Jev → Task 4; immutable data/anchors/settlement/four baselines/retention → Task 5; weekly cohorts/corrections/quality/regime/reliability → Task 6; mail/templates → Task 7; deadlines/restarts/context collection/CLI → Task 8; deployment/acceptance → Task 9. Review Focus cases have assigned tests. Paired experiments and fitted calibration remain in the separate research roadmap.

Recommended execution: implement sequentially in this session using `superpowers:executing-plans`, because persistence, evaluation and mail orchestration share contracts. An alternative is task-by-task delegated implementation and independent review. The user reviews this written plan and chooses the execution method before product implementation begins.
