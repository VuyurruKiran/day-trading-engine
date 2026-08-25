# Plan v2.2 implementation alignment — M4 through M13

Plan v2.2 supersedes the old milestone numbering. The existing context/provider implementation is retained and now satisfies the core of **M6** rather than M4.

## Implemented in this branch

- **M4** — deterministic 20/5/5 research cohort, hard eligibility/risk vetoes, ORB/VWAP continuation plan, cash/risk sizing, 0-5 shortlist boundary.
- **M5** — USD 100 cash-only ledger, replay fidelity levels, shadow outcomes, ledger-neutral research evaluation, immutable/versioned decision snapshot model.
- **M6** — reuses the existing normalized context/provider/store implementation already on main.
- **M7** — auditable structured AI event schema, source hash, model/prompt version, uncertainty fallback, validation of direction/impact/confidence.
- **M8** — context-aware component ranking that cannot promote an ineligible symbol; frozen weight object and deterministic ties.
- **M9** — immutable SQLite decision reports, append-only monitoring transitions, latest report and transition history in Streamlit.
- **M10** — separate candidate/session/trade evidence counts, minimum complete-session gate, consumed-holdout registry, strategy evidence registry.
- **M11** — champion/challenger gate with NO CHANGE as a valid result and at most one promotion per cycle.
- **M12** — configurable execution realism profile for commission/slippage/manual-latency/FX inputs.
- **M13** — Canadian activation gate requires calendar, currency model and entitlement validation before enablement.
- **M14** — intentionally remains dormant; no larger-capital behavior is activated.

## Important fidelity limits

BAR_ONLY replay deliberately does not invent historical bid/ask spread, quote size, provider latency, or context that did not exist point-in-time. Spread-sensitive conclusions require quote-aware or forward data, matching Plan v2.2.

## Validation completed before PR

- Python syntax/bytecode compilation passed.
- New v2.2 behavior tests passed locally: 22/22.
- Coverage of the new implementation modules: 92% in the workspace test run.
- Manual static review checked imports, 100-character project line limit, deterministic ordering, SQLite lifecycle, cash-only invariants, shadow-ledger isolation, holdout reuse, and one-promotion-per-cycle behavior.

GitHub CI remains the authoritative Windows + Ubuntu Ruff/pytest gate after the PR is opened.
