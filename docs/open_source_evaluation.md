# M0 — Open-source engine evaluation

Status: **PASS — minimal project-owned simulator selected for Software V1**.

## Decision

Keep candidate, decision, risk, snapshot, paper-ledger and research logic proprietary and pure Python. Keep reusable simulation mechanics behind `SimulationEngine` so a future engine can replace the current implementation without rewriting business rules.

| Project | Role considered | Decision | Reason |
|---|---|---|---|
| NautilusTrader | deterministic replay/simulation/execution modeling | Reject for Software V1 | Strong engine, but V1 already has the required deterministic replay/paper/execution primitives. Adding a large dependency during its active v2 transition creates Windows/resource/license/migration work without replacing a missing V1 capability. |
| QuantConnect LEAN | fallback/reference | Do not use for V1 | Mature, but heavier and Docker-oriented compared with this Windows-first local design. |
| QSTrader | lightweight fallback/reference | Reference only | Python and modular, but no current V1 requirement needs another runtime engine. |
| Microsoft Qlib | ML research | Defer to software V2 | Useful research stack, unnecessary for daily V1 runtime. |
| TradingAgents / ai-hedge-fund | AI workflow ideas | Reference only | AI/agent output cannot own deterministic risk or final trade decisions. |

## M0 acceptance evidence

- `SimulationEngine` protocol exists and contains no business/risk policy.
- The project-owned deterministic replay boundary is already exercised by tests.
- M5 adds the USD 100 paper ledger, replay fidelity and execution/accounting behavior needed by V1.
- Strategy/risk code remains independent of simulator internals.
- No unresolved third-party license obligation enters the V1 runtime from a simulation engine.
- A future simulator replacement remains possible behind the same project-owned boundary.

## Why the Nautilus target-machine gate is no longer pending

Plan v2.2 requires the target-Windows install/resource/license benchmark **or** rejection of Nautilus. Software V1 now explicitly chooses rejection rather than carrying an indefinitely pending dependency gate. This is a YAGNI/reuse decision, not a claim that Nautilus failed technically.

If future evidence shows that the custom simulator cannot model a required fill/accounting behavior, Nautilus or another engine becomes a challenger dependency and must pass the original Windows, resource, license, determinism and replay-regression gates before adoption.

Public backtest claims, screenshots, star counts and README profitability claims are not accepted as strategy evidence.
