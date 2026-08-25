# Day Trading Research & Decision Engine

Software V1 implementation based on **Implementation Plan v2.2**.

Current milestone: **M2 Questrade market-data adapter**.

## Locked V1 scope

- $100 USD starting validation capital
- no capital top-ups
- long-only, cash-only, no leverage
- manual execution only
- maximum one active position
- 30 research candidates per normal trading session
- 2–5 user-facing finalists
- 0–1 PRIMARY candidate or NO TRADE
- AI is optional and cannot override deterministic rules
- 12-month minimum / 24-month preferred historical bootstrap target

M2 adds Questrade Level 1 collection only. No strategy or order execution is implemented.

## Windows quick start

Prerequisites: Git, Python 3.12, and `uv`.

```powershell
.\setup.ps1
.\doctor.ps1
.\test.ps1
.\run.ps1
```

## Questrade market-data snapshot

1. Copy `.env.example` to `.env`.
2. Put the current Questrade refresh token in `QUESTRADE_REFRESH_TOKEN`.
3. Run a snapshot using the configured small watchlist:

```powershell
.\collect.ps1
```

To inspect Questrade market hours and `snapQuotesLimit` at the same time:

```powershell
.\collect.ps1 --markets
```

To collect explicit symbols instead of the configured watchlist:

```powershell
.\collect.ps1 AAPL AMD NVDA
```

Quotes are stored in `data/trading.db`. Every row retains source/received timestamps, Level 1 fields, delay/halt state, latency, rate-limit metadata, and a deterministic eligibility reason. Delayed, halted, stale, incomplete, crossed, or timestamp-unverified quotes are never trade-eligible.

Questrade refresh tokens rotate. The adapter stores the newest token in ignored local runtime state under `data/questrade_tokens.json`; never commit that file.

## Cross-platform

```bash
./setup.sh
./doctor.sh
./test.sh
./run.sh
./collect.sh
```

## Repository design

- `src/day_trading_engine/` — production source
- `src/day_trading_engine/providers/` — provider adapters, including Questrade
- `src/day_trading_engine/market_data/` — collection and persistence
- `tests/` — unit/integration tests
- `configs/v1.yaml` — locked V1 contract and controlled market-data watchlist
- `docs/` — architecture/evidence/dependency decisions
- `data/` and `logs/` — runtime-only, excluded from Git

See `docs/open_source_evaluation.md` for M0 and `docs/m2_acceptance.md` for M2.
