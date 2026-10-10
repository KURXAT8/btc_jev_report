# First release execution ledger

## 2026-09-23 concise mail and one-day horizon

User approved concise mail and the closed ±1% one-day sideways threshold. Added 24-hour news impact and 1/3/7-day forecast-v2, one-day baselines/settlement/weekly cohorts, activation-aware missing counts and legacy completed-prediction resume. Simplified HTML/text rendering while retaining numeric diagnostics and weekly metrics in storage. Updated offline preview fixture and generated daily/weekly synthetic previews. Independent review found mixed-version weekly reports exposed internal rule IDs; reproduced with a regression and replaced with short version labels while keeping cohorts separate. Final full suite: 264 passed in 15.43s. Compileall and diff whitespace checks pass. No live model calls or email for this change. See ../../mail-update-2026-09-23.md.

## 2026-09-23 live verification follow-up

Live market/OKX, three Jev calls, rendered report, SMTP acceptance and user-confirmed inbox/HTML passed. Fixed CoinDesk canonical RSS URL and FOMC abbreviated months/notation-vote parsing after four failing regressions; unknown annotations remain rejected. Fresh full suite: 245 passed in 14.11s. Recollected RSS/calendars: all five healthy. Independent scoped review found no critical or important issues. See ../../live-validation-2026-09-23.md for evidence and limits. Git add remains blocked by read-only .git; user confirmed no remote is wanted. User systemd bus unavailable, so timers and first scheduled daily run remain unverified.

Plan: docs/superpowers/plans/2026-09-21-btc-jev-daily-weekly-email.md
Spec: docs/superpowers/specs/2026-09-21-btc-jev-daily-email-design.md

2026-09-22 resumed in the original workspace, as authorized by the user's instruction to finish Tasks 1–9 without questions. Git is unborn and .git is read-only; changes remain in the workspace. No live mail or timer activation was performed.

## Earlier evidence retained

- Task 1: existing configuration/domain review and tests.
- Tasks 2–3: earlier full suite 147 passing.
- Task 4: earlier record observed 32 missing-module failures, followed by 179 passing after implementation.
- At this continuation Tasks 5–9 code and tests already existed, although the old ledger and README still said pending. Initial fresh full suite: 226 passed, 2 failed.

## Completion record

Final full suite: .venv/bin/python -m pytest -q → 240 passed in 13.53s, zero skipped.

- Task 1: complete — configuration, secret allowlist, validation; test_config/test_domain.
- Task 2: complete — timestamped market/news/calendar collection and revisions; test_sources/test_calendars/test_boundaries.
- Task 3: complete — same-minute crosscheck and quality policy; test_quality.
- Task 4: complete — indicators/context/Jev stages and bounded requests; test_indicators/test_context/test_jev.
- Task 5: complete — persistence, budgets, settlements, baselines, retention; test_store/test_evaluation/test_baselines.
- Task 6: complete — weekly cohorts, confidence/quality/regimes/reliability, delivered corrections; test_weekly.
- Task 7: complete — Chinese HTML/text and frozen SMTP payloads; test_render/test_mail.
- Task 8: complete — collect/prepare/send/evaluate/reconcile/preview/status/cleanup and restart/leases; test_pipeline/test_cli/test_boundaries.
- Task 9: complete — dependency lock, deploy renderer/installer, unit verification, integration, README and docs/acceptance.md; test_deploy/test_integration.

## Final review and fixes

Fresh independent read-only reviewer inspected full-release correctness with the plan's five Review Focus cases. It found two important defects: correction notifications lost after an unsent weekly report, and malformed settlement rows aborting preparation. Both were accepted and fixed. It reported no additional important issues or deferred minors in the inspected paths.

Every new fix was reproduced before implementation:

1. News reverted revision: initial test_reverted_news_revision_uses_latest_observation failed (B selected after A→B→A). Added append-only article observations and schema v4 backfill; now selects A with original first-seen time and the latest eligible observation.
2. News diagnostic counts: initial test_model_excluded_news_counts_are_persisted failed with missing relevance_excluded. Forecast emits counts into job diagnostics before trend, and weekly aggregation retains selection/relevance/size exclusions.
3. Missed correction: new missed/failed/unknown cases each failed (zero corrections). Deduplication now uses confirmed sent jobs linked to weekly snapshot IDs; all four delivery-state cases pass.
4. Malformed maturity: five response-shape cases failed with uncontrolled exceptions; Monday integration also failed at IndexError. Validated every candle before selection, normalizing shape errors to SourceError; invalid result stays pending while valid settlements and weekly preparation proceed.
5. Deployment paths: real systemd-analyze first failed on quoted WorkingDirectory; then reported ignored quoted EnvironmentFile. Renderer uses directive-appropriate paths; tests require exit zero and empty diagnostics, including spaces in project paths.
6. Restart diagnostics: test_restart_after_predictions_preserves_news_exclusion_counts failed with missing key after resumed preparation. Existing diagnostics are preserved; saved predictions do not repeat model work.

All six fixes are covered by the final 240-test suite. No additional review pass was needed.

## Decisions

Ruling: Work in place without Git mutation — the supplied environment marks .git read-only and there is no HEAD; the plan explicitly permits this — cost: no commit history for rollback.

Ruling: Resume inherited implementation instead of rebuilding earlier RED steps — user asked to continue exactly where work stopped; only observed RED→GREEN fixes and final verification are claimed — cost: earlier test-first chronology cannot be reconstructed.

Ruling: First-release completion covers Tasks 1–9 and basic offline verification; live account validation, first real mail and activation remain deployment steps — the plan separates them and the user asked for first version/basic verification — cost: real provider availability, account usage, inbox and persistent timers are unverified.

Ruling: Keep numeric/weekly records indefinitely while expiring eligible text at 90 days — the spec requires at least 365 days and reference safety — cost: numeric storage grows over time.

## Verification artifacts

- Compileall and pip check pass.
- Package editable install and CLI entry point verified; wheel build and isolated wheel preview verified.
- HTML/text synthetic preview: data/preview/report.html and report.txt.
- Six generated units: /tmp/btc-jev-release-units; systemd-analyze --user verify exits 0 with no diagnostics. No units enabled.
- Document verification: 9/9 documented tasks; only structural checks, not claimed as app verification.
- Detailed acceptance and remaining external checks: docs/acceptance.md.
- TypeSafe API contract was previously checked against https://docs.typesafe.ai/api on 2026-09-22; this is not live account acceptance.
