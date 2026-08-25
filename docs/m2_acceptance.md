# M2 — Questrade Market-Data Adapter

Status: **SOFTWARE IMPLEMENTED — live 30-symbol provider/resource evidence still required**.

## Scope implemented

- Questrade OAuth refresh flow with rotated refresh-token persistence.
- API-server handling from the OAuth response.
- Market metadata with trading hours and `snapQuotesLimit`.
- Controlled symbol resolution and batched Level 1 quote requests.
- Rate-limit metadata and retry/backoff for 429/5xx plus one 401 refresh recovery.
- SQLite quote provenance and deterministic freshness/delay/halt/quality vetoes.
- Windows/cross-platform snapshot collectors.
- `capacity-gate.*` live 30+ symbol runner recording stored/valid quotes, failures, elapsed time, CPU time, peak process memory, maximum observed quote latency and minimum REST-request load.

## L1 streaming decision

Plan v2.2 makes L1 streaming optional where practical. The project also requires secure external transport. Current public Questrade streaming documentation describes RawSocket/WebSocket ports and token handshakes but does not document a TLS-secured socket endpoint. Software V1 therefore retains batched REST for the controlled research/final watchlist path rather than adding an undocumented or insecure socket transport.

## Acceptance checklist

- [x] OAuth/token rotation implemented without logging secrets.
- [x] Questrade API server is taken from the token response rather than hard-coded.
- [x] Market hours and `snapQuotesLimit` can be read.
- [x] Quote requests are batched.
- [x] Questrade rate-limit headers are stored with quote provenance.
- [x] 429/5xx retry with bounded exponential backoff is implemented.
- [x] 401 triggers one forced token refresh before failure.
- [x] Every stored quote contains source and received timestamps.
- [x] Delayed, halted, stale or timestamp-unverified quotes are invalidated.
- [x] Runtime database and token cache remain ignored by Git.
- [x] Unit/integration tests cover auth, retries, rate metadata, storage and quality vetoes.
- [x] 30+ symbol capacity-gate tooling is implemented and persists local evidence.
- [ ] Live capacity gate passes with the user's current Questrade entitlement on the target Windows machine.

The final live check is intentionally not simulated in CI because real token rotation/entitlement behavior must be measured on the user's environment.
