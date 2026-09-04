# Day Trading Research & Decision Engine

Software V1 implementation based on **Implementation Plan v3.2**.

Current code scope: v3.2 software is implemented in staged, regression-tested changes. Provider-, historical-duration-, extended-gate-, forward-validation-, soak-, and Canadian-activation gates remain evidence-driven and are not marked complete until their artifacts exist.

## Locked V1 scope

- start with exactly $100 USD validation capital; no external top-ups
- realized manual-trade P&L compounds into later available cash; the account is not reset to $100 each session
- long-only, cash-only, no leverage
- manual execution only
- maximum one active position
- versioned ~200-symbol US research universe with a 30-symbol daily cohort using 20 core / 5 boundary / 5 deterministic diversity
- 1-5 user-facing finalists when candidates qualify
- rank one is PRIMARY; zero qualifiers means NO TRADE
- context weights: technical 50%, market/sector 20%, news 20%, Reddit 5%, fundamentals 5%
- missing optional context is neutral and its weight is reassigned to technical scoring
- hard data/risk gates remain authoritative; context cannot rescue an ineligible symbol
- preferred 24-month / minimum 12-month Alpaca historical target where provider history exists
- Questrade live US data/symbol validation; Alpaca historical US data
- canonical 04:00-20:00 ET Alpaca history, with pre/regular/post phase and provider/feed provenance
- same-day pre-market evidence may affect the regular-session decision; post-market evidence is next-session-only
- extended evidence is 20% of the technical component; new extended hard gates default to shadow mode
- absent Alpaca bars are accepted as sparse only when raw SIP trades confirm no bar-eligible
  trade occurred; replacement candles are never manufactured
- AI is optional and cannot override deterministic rules
- Canada remains architected but inactive until its own validation gate passes

Context evidence is normalized into `data/context.db`. Store source metadata and derived fields only; full article and Reddit bodies are not retained. Provider errors and version metadata are persisted with collection runs. Monthly refinement remains review-only: checksummed month-end datasets and ablation evidence may support a manually reviewed challenger, but no algorithm is promoted automatically.

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

## v3.2 operational commands

Run the provider/resource evidence gate:

```powershell
.\capacity-gate.ps1 AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL XYZ SHOP SPY QQQ
```

Bootstrap a historical universe manifest, then backfill history:

```powershell
.\bootstrap-universe.ps1 --as-of 2024-08-01 AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL XYZ SHOP SPY QQQ
.\backfill.ps1 --start 2024-08-01 --end 2026-08-01 AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL XYZ SHOP SPY QQQ
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
.\month-end.ps1 D:\day-trading-backups --month 2026-07 --algorithm orb-v1 --config-version 3.2 --schema decision-v1
```

The dashboard shows whether the latest backup is on the same storage volume as runtime data. Same-volume backups are explicitly labeled as corruption/deletion protection only, not protection from physical disk failure.

The local workflow schedules the Alpaca SIP after-close backfill at 18:25 America/Edmonton, beyond the Basic-plan recent-data delay. Monthly reporting, backup, and month-end snapshot jobs follow at 19:15, 19:30, and 19:45 respectively.
The scheduled live engine and dashboard stop cleanly at the 20:00 ET extended-session close; run `run.ps1` or `run.sh` manually when after-hours dashboard access is needed.

## Cross-platform

Replace the example symbols, dates, IDs, paths, and versions with your own values.

```bash
./setup.sh
./doctor.sh
./test.sh
./run.sh
./collect.sh
./capacity-gate.sh AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL XYZ SHOP SPY QQQ
./bootstrap-universe.sh --as-of 2024-08-01 AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL XYZ SHOP SPY QQQ
./backfill.sh --start 2024-08-01 --end 2026-08-01 AAPL MSFT NVDA AMD AMZN META GOOGL GOOG TSLA JPM BAC WMT COST XOM CVX CAT DIS NFLX ORCL CRM INTC QCOM AVGO MU UBER PYPL XYZ SHOP SPY QQQ
./backup.sh /path/to/backups
./schedule-backup.sh /path/to/backups 18:30
./restore.sh /path/to/backup /path/to/empty-restore --verify-only
./month-end.sh /path/to/backups --month 2026-07 --algorithm orb-v1 --config-version 3.2 --schema decision-v1
```

## Repository design

- `src/day_trading_engine/` - production source
- `src/day_trading_engine/providers/` - provider adapters, including Questrade/Alpaca/context sources
- `src/day_trading_engine/market_data/` - collection, history, backfill and persistence
- `src/day_trading_engine/research/` - research datasets, validation, experiments and refinement
- `src/day_trading_engine/ops/` - backup/restore and maintenance commands
- `tests/` - unit/integration/acceptance/replay/regression coverage
- `configs/v1.yaml` - locked Software V1 validation contract
- `docs/` - architecture, milestone evidence and dependency decisions
- `data/` and `logs/` - runtime-only, excluded from Git
