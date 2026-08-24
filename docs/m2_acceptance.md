# M2 — Questrade Market-Data Adapter

Status: **IMPLEMENTED — automated CI and live-token acceptance still required before merge**.

## Scope implemented

- Questrade OAuth refresh flow, including rotated refresh-token persistence.
- API-server handling from the OAuth response.
- Market metadata endpoint with trading hours and `snapQuotesLimit`.
- Symbol resolution for a controlled configured watchlist.
- Batched Level 1 quote requests.
- Rate-limit header capture and retry/backoff for 429 and 5xx responses.
- One forced token refresh/retry on HTTP 401.
- SQLite persistence of bid/ask, sizes, last trade, volume, OHLC, delay, halt state, source time, receive time, latency, and rate-limit metadata.
- Deterministic quote-quality vetoes.
- Windows and cross-platform one-shot collector wrappers.

## Deterministic quote validity

A stored quote is not trade-eligible when any of these conditions is true:

1. `delay != 0`
2. `isHalted == true`
3. response source time cannot be verified from the HTTP `Date` header
4. measured latency exceeds `market_data.max_latency_ms`
5. bid/ask/last is missing or non-positive
6. ask is below bid

M2 only establishes trustworthy market-data acquisition. Candidate ranking, strategy, and order execution remain out of scope.

## Acceptance checklist

- [x] OAuth/token rotation implemented without logging secrets.
- [x] Questrade API server is taken from the token response rather than hard-coded.
- [x] Market hours and `snapQuotesLimit` can be read.
- [x] Quote requests are batched.
- [x] Questrade rate-limit headers are stored with quote provenance.
- [x] 429/5xx retry with bounded exponential backoff is implemented.
- [x] 401 triggers one forced token refresh before failure.
- [x] Every stored quote contains source and received timestamps.
- [x] Delayed or halted quotes are automatically invalidated.
- [x] Stale or timestamp-unverified quotes are automatically invalidated.
- [x] Runtime database and token cache remain ignored by Git.
- [x] Unit/integration tests cover auth, retries, rate metadata, storage, and quality vetoes.
- [ ] GitHub Actions passes on Windows + Ubuntu, Python 3.12 + 3.13.
- [ ] Live Questrade smoke test succeeds with the user's current refresh token and entitlement.

The final two checks require CI execution and a real local Questrade token; neither is simulated as completed.
