# Day Trading Research & Decision Engine

Software V1 implementation based on **Implementation Plan v2.2**.

Current milestone: **M0 + M1 foundation**.

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

No strategy, Questrade connection, or live trading capability is implemented yet.

## Windows quick start

Prerequisites: Git, Python 3.12, and `uv`.

```powershell
.\setup.ps1
.\doctor.ps1
.\test.ps1
.\run.ps1
```

## Cross-platform

```bash
./setup.sh
./doctor.sh
./test.sh
./run.sh
```

## Repository design

- `src/day_trading_engine/` — production source
- `tests/` — unit/integration tests
- `configs/v1.yaml` — locked V1 contract
- `docs/` — architecture/evidence/dependency decisions
- `data/` and `logs/` — runtime-only, excluded from Git

See `docs/open_source_evaluation.md` for M0.
