# Full forecast optimization validation — 2026-09-24

The optimization keeps formal Jev 1/3/7 day predictions immutable. It adds cutoff-safe market features and a finite regime label, three best-effort shadow roles, and a local ensemble gate with warmup, low-signal, rejection, and recent-adopted fallback states.

The daily Jev reservation remains conservative and shared by all stages. A request reserves four times its encoded input payload before HTTP; the SQLite counter and retry limit survive restart. Role failures therefore cannot bypass the US$0.15 daily input-token ceiling or invalidate an already-created formal forecast.

Validation completed:

- `318 passed` — full pytest suite.
- `8 passed` — deploy, CLI, and planning checks.
- Wheel built with `pip wheel`; installed into a clean virtual environment.
- `compileall` passed for `src`.
- `pip check` reported no broken requirements.
- Runtime dependency lock includes `websockets==15.0.1`.

The acceptance gate requires at least 30 training and 15 validation samples, at least five examples of each class in each split, an accuracy gain of at least 0.02, and a Brier improvement of at least 0.005. Until those conditions are met, formal Jev probabilities remain the adopted output.
