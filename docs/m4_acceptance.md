# M4 — Baseline Strategy and Deterministic Risk Core

Status: **IMPLEMENTED IN CODE — final CI/review and live-plan integration gates remain**.

This document is aligned to Implementation Plan v2.2. The context/news work previously labeled M4 belongs to v2.2 M6 and remains in the repository as early future-stage implementation.

## Scope implemented

- Hard eligibility gates execute before scoring.
- Transparent opening-range/VWAP continuation baseline.
- Stale, delayed, halted, crossed, wide-spread, high-volatility, low-RVOL and low-liquidity vetoes.
- Cash-only sizing against the fixed USD 100 validation balance.
- Deterministic technical ranking with stable ticker tie-breaking.
- Explicit entry, stop, target, quantity, expiry and WAIT / ENTRY_VALID status.
- 2–5 user-facing finalists only; fewer than two eligible candidates returns NO TRADE.
- One PRIMARY maximum.
- Existing active position forces NO TRADE.
- Research evaluation is retained for every supplied cohort member, including rejected rows.

## Acceptance checklist

- [x] Risk can reject the otherwise highest-scoring symbol.
- [x] Same inputs and policy produce the same ranking and plan.
- [x] Every emitted plan contains entry, stop, target, quantity and expiry.
- [x] Cash-only sizing cannot exceed the available USD 100 balance.
- [x] A second active V1 position cannot be opened.
- [x] Unit tests cover deterministic cohort construction, shortfall, vetoes, sizing and one-position behavior.
- [ ] Wire the baseline evaluator into the daily engine flow once the M3 cohort/backfill gate is complete.
- [ ] GitHub Actions passes on the final PR head.
- [ ] Code, architecture, SDET, configuration/security and cross-platform review pass on the final PR head.
