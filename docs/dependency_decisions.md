# Dependency decisions

| Dependency | Status | Decision |
|---|---|---|
| Python | Adopted | Target Python 3.12; CI also exercises 3.13 compatibility. |
| uv | Adopted | Environment/dependency management and lock file. |
| Pydantic | Adopted | Strict immutable configuration contract. |
| SQLite | Adopted | Python standard library; operational metadata in later milestones. |
| Parquet/PyArrow | Adopted | Local historical/research storage foundation. |
| DuckDB | Adopted | Embedded local analytics over Parquet. |
| Streamlit | Adopted | Local non-coder dashboard. |
| NautilusTrader | Pending target-machine gate | Preferred simulation component, intentionally not installed in M1. |
| Qlib | Deferred | Future software V2 offline research environment. |

No dependency is allowed to make AI mandatory or weaken the locked V1 trading contract.
