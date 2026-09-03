# Implementation Plan v3.2 — Extended-Hours Addendum

Plan v3.2 supersedes v3.1 and preserves every existing Software V1 capital, universe,
provider, ranking, risk, manual-execution, and acceptance rule except where this addendum
explicitly extends the market-data contract.

- Alpaca SIP history covers 04:00-20:00 ET and stores pre-market, regular, and post-market
  phases with provider/feed/schedule provenance. Regular candle validity remains strict;
  absent regular bars are accepted as sparse only after a stable retry and raw SIP trade proof
  that no bar-eligible trade occurred. Extended phases are sparse and are never synthesized.
- Questrade remains the live provider. Its current daily extended bounds are validated and
  archived before same-day extended processing.
- Today's pre-market evidence may affect today's regular-session decision. Post-market
  evidence may first affect the next trading session. Trading remains regular-hours-only.
- Extended evidence contributes 20% of the technical score; the prior technical calculation
  contributes 80%. Missing prior post-market evidence is neutral.
- The initial decision time is 08:00 America/Edmonton (10:00 ET), after the five-minute
  regular opening range. Decisions, plans, and manual entries remain regular-session-only.
- Frozen evidence includes pre-market high/low, volume, gap, range, volatility, distance from
  both extremes, active-minute coverage, freshness, provider/feed, and schedule provenance.
  The operator UI displays that evidence and whether extended gates are shadow or active.
- Extended hard gates default to shadow mode. Active mode requires a manually approved
  versioned artifact with at least 15 complete sessions, 90% coverage, deterministic replay,
  consumed holdout evidence, forward confirmation, and no expectancy, drawdown, or hard-risk
  regression.
- Each monthly versioned activation report compares frozen regular-only and extended PRIMARY
  choices and their Alpaca replay outcomes. Generated evidence cannot approve or activate itself.
- Ruff, the complete test suite, at least 90% coverage, Windows/Linux behavior, and the full
  200 -> 30 -> finalists/PRIMARY-or-NO-TRADE funnel remain mandatory acceptance gates.
- Overnight data and extended-hours order execution remain out of scope.
