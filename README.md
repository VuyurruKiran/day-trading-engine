# Day Trading Research & Decision Engine

Software V1 implementation based on **Implementation Plan v2.2**.

Current code scope: **M0-M13 implemented at the software layer; M14 remains dormant**. Provider-, historical-duration-, forward-validation-, soak-, and Canadian-activation gates remain evidence-driven and are not marked complete until their artifacts exist.

## Locked V1 scope

- $100 USD starting validation capital
- no capital top-ups
- long-only, cash-only, no leverage
- manual execution only
- maximum one active position
- 30 research candidates per normal trading session
- 2-5 user-facing finalists
- 0-1 PRIMARY candidate or NO TRADE
- AI is optional and cannot override deterministic rules
- 12-month minimum / 24-month preferred historical bootstrap target

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
3. Run a snapshot using the configured watchlist:

```powershell
.\collect.ps1
```

Quotes are stored in `data/trading.db`. Every row retains source/received timestamps, Level 1 fields, delay/halt state, latency, rate-limit metadata, and an eligibility reason. Delayed, halted, stale, incomplete, crossed, or timestamp-unverified quotes are never trade-eligible.

Questrade refresh tokens rotate. The adapter stores the newest token in ignored local runtime state under `data/questrade_tokens.json`; never commit or back up that file.

## Plan v2.2 gap-closure commands

Run the live 30-symbol provider/resource evidence gate:

```powershell
.\capacity-gate.ps1 AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL SQ SHOP SPY QQQ
```

Bootstrap the universe as a ticker list, then backfill 24 months of history:

```powershell
.\bootstrap-universe.ps1 --as-of 2025-08-01 AAPL MSFT NVDA AMZN META
.\backfill.ps1 --start 2025-08-01 --end 2026-08-01 AAPL MSFT NVDA AMZN META
```

Back up research data and install the Windows daily backup schedule:

```powershell
.\backup.ps1 D:\day-trading-backups
.\schedule-backup.ps1 -Destination D:\day-trading-backups -Time 18:30
```

Verify or restore a backup. Replace the example timestamp with an existing backup folder:

```powershell
.\restore.ps1 D:\day-trading-backups\20260824T183000Z C:\temp\restore-check --verify-only
```

Create the month-end checksummed/versioned research snapshot used by the evidence review. Run it only for a closed month:

```powershell
.\month-end.ps1 D:\day-trading-backups --month 2026-07 --algorithm orb-v1 --config-version v1 --schema decision-v1
```

The dashboard shows whether the latest backup is on the same storage volume as runtime data. Same-volume backups are explicitly labeled as corruption/deletion protection only, not protection from physical disk failure.

## Cross-platform

Replace the example symbols, dates, IDs, paths, and versions with your own values.

```bash
./setup.sh
./doctor.sh
./test.sh
./run.sh
./collect.sh
./capacity-gate.sh AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL SQ SHOP SPY QQQ
./bootstrap-universe.sh --as-of 2025-08-01 AAPL MSFT NVDA AMZN META
./backfill.sh --start 2025-08-01 --end 2026-08-01 AAPL MSFT NVDA AMZN META
./backup.sh /path/to/backups
./schedule-backup.sh /path/to/backups 18:30
./restore.sh /path/to/backup /path/to/empty-restore --verify-only
./month-end.sh /path/to/backups --month 2026-07 --algorithm orb-v1 --config-version v1 --schema decision-v1
```

## Repository design

- `src/day_trading_engine/` - production source
- `src/day_trading_engine/providers/` - provider adapters, including Questrade
- `src/day_trading_engine/market_data/` - collection, history, backfill and persistence
- `src/day_trading_engine/ops/` - backup/restore and maintenance commands
- `tests/` - unit/integration/acceptance coverage
- `configs/v1.yaml` - locked V1 contract
- `docs/` - architecture, milestone evidence and dependency decisions
- `data/` and `logs/` - runtime-only, excluded from Git

See `docs/v2_2_gap_closure.md` for the remaining evidence-only gates and their exact disposition.
