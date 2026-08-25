# M0–M4 alignment to Implementation Plan v2.2

This file records the corrected v2.2 milestone mapping and current acceptance status.

| Milestone | v2.2 requirement | Current repository status |
|---|---|---|
| M0 | Open-source engine selection; Windows/resource/license/deterministic replay gate or fallback choice | **Software decision complete.** `SimulationEngine` boundary is retained and NautilusTrader is explicitly rejected for Software V1 because the project-owned replay/paper stack already covers V1 needs. Future adoption remains separately gated. |
| M1 | Windows-first monorepo and one-command foundation | **Implemented.** setup/run/test/doctor/support scripts, Streamlit health, config, logging and Windows/Ubuntu CI. |
| M2 | Questrade REST + optional L1; normalized fresh data; 30-symbol API/resource proof | **Software implemented; live evidence pending.** REST/OAuth/history/freshness/rate handling exist. `capacity-gate.*` now generates 30+ symbol latency/CPU/memory/request evidence. L1 sockets remain deferred because current public Questrade streaming docs do not document a TLS-secured socket contract, while the project requires secure external transport. |
| M3 | Reproducible Parquet/candles/features, deterministic 30-symbol 20/5/5 cohort, 12–24 month resumable bootstrap/manifests | **Software implemented; historical-duration evidence pending.** Resumable 1-minute backfill, coverage/checksum/fidelity manifests and dated universe manifests are implemented. The live dataset must still reach the 12-month minimum or record a provider limitation. |
| M4 | Baseline opening-range/VWAP strategy + deterministic hard risk over research cohort; 2–5 finalists, PRIMARY/NO TRADE | **Implemented at the software layer.** Final evidence depends on the M3 historical/live dataset and later validation campaign. |

## Corrected later-stage mapping

The normalized GDELT/SEC/FRED context implementation is M6 under Plan v2.2. M5 and M7–M13 are implemented at the software/framework layer, while data-duration, live-provider, monthly-cycle, soak and Canadian activation gates remain evidence-driven and cannot be fabricated by CI.

## Rules preserved

- USD 100 start, no top-ups, long-only, cash-only, manual execution.
- 30 research rows are not 30 independent trading sessions.
- Research-only evaluation never increases exposure.
- Invalid/stale/delayed/halted symbols are never inserted to pad the cohort.
- Final candidates are a subset of the research cohort; PRIMARY is at most one.
- NO TRADE remains a valid result.
- Context/AI cannot override deterministic hard risk.
