# Dependency decisions

| Dependency | Status | Decision |
|---|---|---|
| Python | Adopted | Target Python 3.12; CI also exercises 3.13 compatibility. |
| uv | Adopted | Environment/dependency management and lock file. |
| Pydantic | Adopted | Strict immutable configuration contract. |
| SQLite | Adopted | Python standard library operational metadata. |
| Parquet/PyArrow | Adopted | Local historical/research storage foundation. |
| DuckDB | Adopted | Embedded local analytics over Parquet. |
| Streamlit | Adopted | Local non-coder dashboard. |
| NautilusTrader | Rejected for Software V1 | V1 already has deterministic project-owned replay, paper-ledger and execution-realism primitives behind the `SimulationEngine` boundary. Adding Nautilus now would introduce a large dependency and Windows/v2-migration gate without replacing a proven V1 requirement. Re-evaluate only if the custom simulator cannot satisfy a measured acceptance criterion. |
| Qlib | Deferred | Future software V2 offline research environment. |

## M0 decision

Plan v2.2 permits Nautilus rejection and a minimal custom simulator behind the project-owned port. The repository now takes that path for Software V1. This removes the uncompleted target-machine Nautilus installation gate without pretending it passed: the dependency is simply not adopted.

The replacement path remains intact because strategy/risk code does not import simulator internals. A future engine adoption requires its own branch, Windows/resource/license benchmark, compatibility tests and replay-regression proof before merge.

No dependency is allowed to make AI mandatory or weaken the locked V1 trading contract.
