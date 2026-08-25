# M3 — Market Catalog, Candles and Technical Features

Status: **SOFTWARE IMPLEMENTED — 12-month live historical coverage evidence still required**.

## Scope implemented

- Point-in-time quote export and versioned Parquet features.
- Deterministic 1-minute/5-minute candle generation and validation.
- VWAP, EMA, opening range, gap, RVOL, volatility, spread and relative-strength features.
- Timezone/session isolation and leakage guards.
- Questrade historical candle support with the documented 2,000-row response limit protected by per-session requests.
- Resumable one-minute backfill that skips completed symbol/session pairs.
- Atomic coverage manifest updates with per-session status, rows, output paths and SHA-256 checksums.
- Coverage summary records first/last complete session and whether 12-month/24-month targets are met.
- BAR_ONLY replay fidelity plus explicit unavailability of historical bid/ask, quote size and provider latency.
- Dated historical-universe manifests with survivorship-risk disclosure.
- Daily backup, restore verification, same-volume warning and month-end checksummed/versioned research snapshots.

## Determinism and leakage rules

1. Point-in-time cutoffs are timezone-aware.
2. Quote/benchmark inputs are filtered before feature calculation.
3. Feature calculation and historical replay remain session-isolated.
4. Same raw data/config/feature version reproduces the same rows.
5. Missing historical quote/spread/latency fields are not synthesized.
6. Current-universe membership is not silently treated as a complete historical universe.

## Acceptance checklist

- [x] Stored market records can be exported to Parquet by date/symbol.
- [x] 1-minute and 5-minute candles are generated deterministically.
- [x] Core technical features and relative strength are implemented.
- [x] Feature versions and calculation timestamps are persisted.
- [x] Historical replay prevents future rows from entering earlier frames.
- [x] Resumable historical backfill and checkpoint/coverage manifest exist.
- [x] Per-file checksums and missing/failed-session reasons are recorded.
- [x] Historical fidelity explicitly marks unavailable quote/spread/latency data.
- [x] Dated universe manifests disclose survivorship risk.
- [x] Backup/restore and month-end snapshot tooling exists; OAuth tokens are excluded.
- [ ] Live historical coverage reaches at least 12 months or records a blocking provider/entitlement limitation.
- [ ] Quarterly restore drill evidence exists on the target machine.

The two remaining checks require real local data/provider/storage operation and are not claimed from mocked CI.
