# Execution ledger — plan: docs/superpowers/plans/2026-09-23-continuous-calibration.md

使用者授權新增內部持續學習／機率校準；以已有研究設計為基礎，完成設計與實作計畫後原地執行。既有 1 天／郵件修改全部保留。Git metadata 明確唯讀，無分支、提交或推送。

Pre-flight: calibration returns policy/temperature/manifests/validation; learning owns persistence and forward-only applications; pipeline persists raw predictions first; render and weekly consume immutable application rows. No shared-interface conflict.

Baseline: 264 passed in 15.56s.

Task 1: complete — 11 tests observed failing for missing module, then 11 passed. Overconfidence correction accepted, held-out regime change rejected, no fitting on validation, strict maturity/known-time filter, 7-day purge, identity separation, duplicate control. Corrected test fixture horizon (future example must remain in the same group).

Task 2: complete — 7 tests observed failing for missing module, then core + persistence 18 passed in 6.18s. Weekly restart/late revision freeze, auto/shadow/off, forward-only application, regression fallback, read-only status, v4→v5→v6 migration.

Task 3: complete — settings, CLI, daily automatic apply, explicit error fallback, labeled raw/calibrated mail, weekly prospective paired scores. Synthetic timeout and runtime exceptions retain valid raw report; frozen MIME, settings modes, CLI no-network, the weekly boundary, and calibration labels covered.

Ruling: default auto as explicitly requested continuous learning; apply only on validated improvement, otherwise raw. Unlike the earlier research-only roadmap, version updates are automatic and audited. Cost if wrong: future probability presentation changes; mode off restores raw for new forecasts, existing frozen reports remain immutable.

Ruling: use temperature-only learning, not a learned direction classifier. This minimizes degrees of freedom with sparse daily labels. Cost: does not change argmax accuracy; cannot claim an increase in hit rate.

Ruling: weekly cutoff fixed at Taipei Monday 00:00, independent of command time. Cost: outcomes settled later Monday wait until next week's fitting; avoids timing-dependent repeated selection.

Independent review found a Critical gap in the original cutoff: `settled_at` cannot prove when a settlement revision was learned. Added `observed_at` (schema v6), backfill old records at migration time, strict known-at query filters, and exact late/backdated-correction RED→GREEN tests for storage and weekly learning. Follow-up caught an exact-cutoff ambiguity and a regression test that was masked by run immutability; now all learning/weekly cutoffs use strict before, with the first delayed run proving it sees result version 1 and a later validation sees version 2.

Ruling: do not retry a raw fallback after report preparation completes. The daily MIME and calibration decision are frozen together; recalibrating on restart would rewrite the already prepared report. Cost: a transient learning failure uses raw probabilities for that day's forecast.

Final review: independent read-only audit approved; no Critical or Important findings remain. Full suite `.venv/bin/python -m pytest -q` → 294 passed in 26.35s. `compileall`, `pip check`, `git diff --check`, offline synthetic preview, actual offline `learn` and read-only `learning-status` verified. Existing local database upgraded to schema 6; it contains no predictions or settlements, so status correctly shows zero calibration runs/applications and warmup/raw fallback.
