# M0 — Open-source engine evaluation

Status: **PASS WITH CONDITIONS** for the architecture gate. No third-party simulation engine is adopted into the runtime yet.

## Decision

Keep candidate, decision, risk, snapshot, and research logic proprietary and pure Python. Place any reusable simulator behind `SimulationEngine` so it can be replaced without rewriting business rules.

| Project | Role considered | Decision | Reason |
|---|---|---|---|
| NautilusTrader | deterministic replay/simulation/execution modeling | Preferred conditional candidate | Strong event-driven engine and Windows wheels, but active v2 transition means an exact version must pass target-machine compatibility and reproducibility tests before pinning. |
| QuantConnect LEAN | fallback/reference | Do not use for V1 | Mature, but common local workflows are heavier than this Windows-first project needs. |
| QSTrader | lightweight fallback/reference | Keep as fallback | Python and modular, but less aligned with the intended live intraday path. |
| Microsoft Qlib | ML research | Defer to software V2 | Useful research stack, but unnecessary for daily V1 runtime. |
| TradingAgents / ai-hedge-fund | AI workflow ideas | Reference only | AI/agent output cannot own deterministic risk or final trade decisions. |

## M0 acceptance evidence

- `SimulationEngine` protocol exists and contains no business/risk policy.
- `ReferenceSimulationEngine` provides a dependency-free deterministic replay fixture.
- Unit tests prove replay determinism independent of input bar ordering and symbol case.
- Third-party engine adoption is blocked until Windows installation, resource, determinism, licensing, and maintenance gates pass.

## Required target-machine gate before NautilusTrader adoption

1. Install a pinned stable Windows x86-64 wheel in an isolated environment.
2. Replay a deterministic synthetic dataset at least 20 times and compare project-boundary outputs.
3. Measure cold start, memory usage, and replay time on the target Windows machine.
4. Verify the exact package license/version and document obligations.
5. Verify our Python strategy/risk code does not need to inherit from Nautilus classes.
6. Record PASS/FAIL in `docs/dependency_decisions.md` before adding it to `pyproject.toml`.

Public backtest claims, screenshots, star counts, and README profitability claims are not accepted as strategy evidence.
