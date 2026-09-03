# Implementation Plan v3.2 — Extended-Hours and Evidence-Integrity Addendum

Plan v3.2 supersedes v3.1 and preserves every existing Software V1 capital, universe,
provider, ranking, risk, manual-execution, and acceptance rule except where this addendum
explicitly changes or extends the data/evidence contract.

## 1. Extended-hours contract

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
- Overnight data and extended-hours order execution remain out of scope.

## 2. Historical-coverage readiness comes first

Historical correctness is a prerequisite for selection research, not an optional enrichment.
Before relying on enhanced ranking conclusions, the Alpaca backfill path must reliably handle
rate limiting, resumability, and incomplete sessions.

- Historical requests must be checkpointed/resumable, honor provider rate-limit/retry signals,
  use bounded backoff, and persist provider errors without converting them into missing candles.
- Every symbol/session retains a coverage state, provider/feed provenance, expected/observed
  minute counts, checksums, retry evidence, accepted-sparse evidence where applicable, and an
  explicit reason for incomplete/unavailable coverage.
- For securities with at least 12 months of listing/provider history available, 12 months of
  verified 1-minute history is the minimum research-readiness threshold. Twenty-four months is
  preferred, not a universal hard gate.
- A newer listing that cannot physically have 12 months of history may still enter under the
  existing new-listing seasoning policy only when all available post-listing history is verified.
  It must carry `LIMITED_HISTORY` plus its exact history length and cannot be used in claims that
  require 12- or 24-month coverage.
- Never fabricate pre-listing history or synthesize missing sessions/minutes merely to satisfy a
  coverage target.

## 3. Evidence-completeness gate is separate from ranking

Completeness is a fail-closed eligibility contract, not another weighted score.

Before a candidate can be actionable, the decision snapshot must have fresh, provenance-valid:

- price;
- volume;
- spread;
- opening-range state;
- market/benchmark context;
- sector context; and
- historical-readiness/coverage evidence.

Each required input stores value/state, provider, source/event time, received time, freshness,
and a machine-readable completeness reason. Missing or stale critical evidence rejects that
candidate before weighted ranking. If no candidate remains actionable, the result is `NO TRADE`.
A high score can never compensate for incomplete critical evidence.

All 30 cohort rows are still preserved for research, including rows rejected by this gate.

## 4. Catalyst check is mandatory for PRIMARY eligibility

Every one of the frozen 30 must receive a point-in-time catalyst check before the 1-5 finalists
and PRIMARY are finalized. The check covers, where applicable:

- current company/market news;
- earnings calendar/results/guidance state;
- SEC filings and accepted-time events;
- trading halt/resumption state;
- offerings, dilution, capital-raise events, and material share-count changes; and
- other major company events represented by the configured event taxonomy.

Catalyst state must distinguish at least:

- `MATERIAL_CATALYST_FOUND`;
- `NO_MATERIAL_CATALYST_FOUND`; and
- `CATALYST_CHECK_UNAVAILABLE` / incomplete.

`NO_MATERIAL_CATALYST_FOUND` is a valid completed result. A source outage or a catalyst check
that could not run is not neutral evidence. A candidate whose catalyst check is unavailable may
retain its research rank for comparison, but it is `PRIMARY_INELIGIBLE`. The next ranked
candidate may become PRIMARY only if it independently passes every gate; otherwise return
`NO TRADE`.

This supersedes the prior rule that missing news could always be treated as neutral and its
weight silently reassigned to Technical for an actionable decision. Missing Reddit remains
optional; unavailable catalyst/news evidence does not.

## 5. Fundamentals are risk context, not deep valuation

For every frozen candidate, collect and persist point-in-time risk context including:

- market capitalization;
- float/shares context where reliable;
- dilution/offering risk;
- earnings date/proximity; and
- basic financial-health flags from time-correct reported facts.

V1 does not require a full valuation model, DCF, or long-horizon fundamental thesis for an
intraday trade. Fundamental data must be used to expose structural/event risk and avoid hidden
risk, not to override hard market/risk gates.

## 6. Reddit remains optional sentiment evidence

- Reddit is attention/sentiment/hype evidence only.
- Missing Reddit must not block operation.
- Positive Reddit activity by itself can never qualify a stock, rescue an incomplete candidate,
  cross a hard gate, or create PRIMARY eligibility.
- The existing frozen weighting remains unchanged until outcome evidence justifies a registered
  challenger; Reddit's contribution remains subordinate to hard gates and the catalyst contract.

## 7. Enrich all 30 before selection and validate against the frozen method

- Complete market, sector, catalyst, fundamental-risk, and optional Reddit enrichment for all 30
  frozen candidates before choosing the 1-5 user-facing finalists.
- Persist completeness/catalyst states and all effective ranking inputs for all 30, not only the
  finalists.
- After close, attach deterministic outcomes or explicit unavailable reasons to every one of the
  30 research rows.
- Keep the currently frozen production weights (Technical 50%, Market/Sector 20%, News 20%,
  Reddit 5%, Fundamentals 5%) while the evidence-integrity changes are evaluated.
- Compare the enhanced evidence-complete method against the current frozen method on the same
  versioned/session-grouped datasets, with the same holdout discipline, before any production
  weight change is proposed.
- Weight changes remain champion/challenger decisions; this addendum does not authorize ad-hoc
  production tuning.

## 8. Required UI/research visibility

For every finalist, and on the research-detail view for all 30, display/store:

- overall completeness status and missing-field reasons;
- freshness/as-of time for critical inputs;
- provider/feed provenance;
- verified history length and coverage status, including `LIMITED_HISTORY`;
- catalyst status, including the distinction between no material catalyst and unavailable check;
- fundamental-risk flags; and
- hard-gate, rejection, or `PRIMARY_INELIGIBLE` reasons.

The UI must not present an incomplete/degraded candidate as an ordinary actionable PRIMARY.

## 9. Acceptance and regression additions

Implementation is not accepted until tests prove:

- Alpaca rate-limit/retry/resume paths do not lose or fabricate sessions;
- verified-history thresholds and `LIMITED_HISTORY` behavior are deterministic;
- completeness is evaluated independently of weighted score;
- stale/missing price, volume, spread, opening range, market, sector, or required history fails closed;
- `NO_MATERIAL_CATALYST_FOUND` is distinct from catalyst-provider/check failure;
- an unavailable catalyst check blocks PRIMARY;
- Reddit cannot independently qualify or rescue a candidate;
- all 30 are enriched before finalist selection and all 30 receive after-close outcomes/unavailable reasons;
- frozen-vs-enhanced comparison uses the same versioned point-in-time dataset and grouped holdout rules; and
- finalist UI/research output exposes completeness, freshness, provider, history length, catalyst state, and rejection reasons.

Ruff, the complete test suite, at least 90% coverage, Windows/Linux behavior, and the full
200 -> 30 -> evidence completeness/catalyst -> normalized ranking -> 1-5 finalists/PRIMARY-or-
NO-TRADE -> immutable 30-row snapshot -> all-30 outcomes -> research report funnel remain
mandatory acceptance gates.
