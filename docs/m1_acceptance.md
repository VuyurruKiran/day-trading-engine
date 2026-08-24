# M1 Acceptance Checklist

- [x] Monorepo uses src layout and keeps source/tests together.
- [x] V1 contract is centralized in `configs/v1.yaml` and validated at startup.
- [x] $100, no-top-up, long-only, cash-only, no-leverage, one-position, manual-only rules are enforced in code.
- [x] Research cohort is exactly 30; finalists max 5; PRIMARY max 1.
- [x] AI is explicitly optional for daily operation.
- [x] Windows PowerShell scripts exist for setup, health, tests, run, and support bundle.
- [x] Linux/macOS shell equivalents exist for setup, health, tests, and run.
- [x] Local health page exists in Streamlit.
- [x] SQLite and DuckDB embedded availability is checked.
- [x] Runtime data, databases, Parquet, logs, and secrets are excluded from Git.
- [x] CI covers Windows and Ubuntu with Python 3.12 and 3.13.
- [x] Static checks and automated tests are part of the required test script.
- [x] M0 simulation abstraction prevents third-party engines from owning business rules.

Not included by design: Questrade integration, strategy implementation, live market data, AI/NLP, or order execution.
