# M4 — Baseline Strategy and Deterministic Risk Core (Plan v2.2)

Plan v2.2 supersedes the previous milestone numbering. The news/global/fundamental implementation previously delivered as M4 is retained and is now treated as M6 context infrastructure.

## Acceptance

- [x] Deterministic 30-symbol cohort builder supports the frozen 20 core / 5 boundary / 5 diversity policy.
- [x] Duplicate symbols never pad the cohort.
- [x] Hard data, halt, spread, volatility, liquidity, cash and active-position vetoes run before trade planning.
- [x] Initial ORB/VWAP continuation strategy produces explicit entry, stop, target, quantity, risk and expiry.
- [x] Cash-only sizing forbids leverage and zero-share plans.
- [x] Context ranking cannot make an ineligible candidate eligible.
- [x] Final shortlist is capped at five and may be empty.
- [x] Same inputs/configuration produce deterministic cohort/ranking results.
- [x] Unit tests cover core invariants and negative cases.

See `docs/v2_2_m4_m13_acceptance.md` for the complete continuation through M13.
