# M3 — Technical Features and Historical Replay

Status: **IMPLEMENTED — CI and final review gates still required before merge**.

## Scope implemented

- Point-in-time export of stored M2 market quotes to Parquet partitions by date and symbol.
- Deterministic 1-minute and 5-minute candle generation from stored Level 1 snapshots.
- VWAP, EMA, opening range, gap, relative volume, volatility, spread, market-relative strength, and sector-relative strength features.
- Feature version and calculation timestamp on every generated feature row.
- Versioned Parquet feature partitions that preserve prior feature versions.
- Weekday/explicit-holiday market calendar boundary without a new dependency.
- Historical replay that evaluates each timestamp using only rows available at or before that timestamp.
- Multi-session replay isolation so one trading day's data does not contaminate another.
- Explicit UTC/timezone validation for point-in-time cutoffs.

## Determinism and leakage rules

1. `as_of` must be timezone-aware.
2. Quote and benchmark inputs are filtered to `received_at <= as_of` before feature calculation.
3. Feature calculation accepts one trading session at a time.
4. Historical replay rebuilds each session chronologically from prefixes only.
5. Re-running the same source data, configuration, and feature version produces the same feature values.
6. Parquet feature output is partitioned by `feature_version`, date, and symbol.

## Acceptance checklist

- [x] Stored market records can be exported to Parquet by date/symbol.
- [x] 1-minute and 5-minute candles are generated deterministically.
- [x] VWAP, EMA, opening range, gap, RVOL, volatility, and spread are calculated.
- [x] Market and sector relative-strength features are supported with point-in-time benchmark inputs.
- [x] Feature version and calculation timestamp are persisted.
- [x] Market-calendar weekday and injected-holiday handling exists.
- [x] Historical replay prevents future quote rows from entering earlier replay frames.
- [x] Multi-session replay keeps sessions isolated.
- [x] Unit/integration tests cover feature math, invalid inputs, Parquet persistence, replay, and leakage guards.
- [ ] GitHub Actions passes on Windows + Ubuntu, Python 3.12 + 3.13.
- [ ] Code review, architecture review, SDET review, configuration/security review, and cross-platform review pass on the final PR head.
- [ ] Final Codex and CodeRabbit reviews complete on the final PR head with no unresolved valid findings.

M3 does not add candidate ranking, risk decisions, strategy entry/exit logic, news, AI/NLP, social signals, or order execution.
