# M0–M4 alignment to Implementation Plan v2.2

The repository was originally advanced with an incorrect milestone mapping. This file records the corrected v2.2 mapping without deleting useful work that belongs to later milestones.

| Milestone | v2.2 requirement | Repository status after realignment |
|---|---|---|
| M0 | Open-source engine selection; Windows/resource/license/deterministic replay gate | Architecture boundary and deterministic reference simulator exist. Nautilus remains conditional; target-Windows resource/license/version gate is still required before adoption. |
| M1 | Windows-first monorepo and one-command foundation | Implemented: setup/run/test/doctor/support scripts, Streamlit health, config, logging, Windows/Ubuntu CI. |
| M2 | Questrade REST + optional L1; normalized fresh data; 30-symbol API/resource proof | REST/OAuth/history/freshness/rate handling implemented. Live 30-symbol resource-budget proof and optional final-watchlist streaming remain local/provider gates. |
| M3 | Reproducible Parquet/candles/features, deterministic 30-symbol 20/5/5 cohort, 12–24 month resumable bootstrap/manifests | Market storage/features/history exist and deterministic 20/5/5 cohort construction is added. Full resumable 12-month coverage/universe/fidelity manifest gate remains incomplete. |
| M4 | Baseline opening-range/VWAP strategy + deterministic hard risk over research cohort; 2–5 finalists, PRIMARY/NO TRADE | Baseline strategy/risk core and tests are added. Daily-flow integration waits on completion of the M3 data gate. |

## Corrected later-stage mapping

The existing normalized GDELT/SEC/FRED context implementation that was previously called **M4** is retained, but under v2.2 it is **early M6 implementation**. It does not satisfy M4 and is not used to claim M4 completion.

## Rules preserved

- USD 100 start, no top-ups, long-only, cash-only, manual execution.
- 30 research rows are not 30 independent trading sessions.
- Research-only evaluation never increases exposure.
- Invalid/stale/delayed/halted symbols are never inserted to pad the cohort.
- Final candidates are a subset of the research cohort; PRIMARY is at most one.
- NO TRADE remains a valid result.
- Context/AI cannot override deterministic hard risk.
