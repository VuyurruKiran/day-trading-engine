# M4 — News, Global Events, Macro and Fundamentals

Status: **IMPLEMENTED — CI and final review gates still required before merge**.

## Scope implemented

- GDELT DOC 2 news adapter with normalized article records.
- SEC EDGAR submissions adapter for recent company filings.
- FRED series-observation adapter preserving observation and real-time/vintage metadata.
- One normalized `ContextRecord` contract for news, filing, and macro records.
- Source timestamp and local `received_at` timestamp on every record.
- SQLite context store with deterministic duplicate suppression.
- Syndicated-news suppression by normalized headline so copied articles do not count as independent evidence.
- Point-in-time snapshots that include only records received at or before the requested cutoff.
- Provider isolation: one failing provider is reported as degraded without discarding successful provider results.
- No new runtime dependency; adapters use the Python standard library and remain replaceable.

## Timestamp and leakage rules

1. `source_at`, `received_at`, and snapshot cutoffs must be timezone-aware.
2. Stored timestamps are normalized to UTC before comparison.
3. Historical snapshots filter on `received_at <= cutoff`; data fetched later cannot leak backward into an earlier decision.
4. Provider source timestamps are retained separately from local receipt time.
5. FRED keeps both observation date and real-time/vintage fields; backfilled macro data is not treated as if it had been locally available earlier.

## Acceptance checklist

- [x] Global/company news can be normalized through a replaceable GDELT adapter.
- [x] SEC recent filings can be normalized with ticker, form, accession, filing/report dates, and source URL.
- [x] FRED observations can be normalized with vintage metadata.
- [x] Every normalized record contains source and received timestamps.
- [x] Duplicate syndicated headlines do not count as independent news records.
- [x] A provider failure does not crash collection from healthy providers.
- [x] Point-in-time snapshots exclude records received after the requested cutoff.
- [x] Unit/integration tests cover normalization, invalid timestamps, duplicate handling, degraded mode, persistence, and leakage guards.
- [ ] GitHub Actions passes on Windows + Ubuntu, Python 3.12 + 3.13.
- [ ] Code review, architecture review, SDET review, configuration/security review, and cross-platform review pass on the final PR head.
- [ ] Final Codex and CodeRabbit reviews complete on the final PR head with no unresolved valid findings.

M4 does not add AI/NLP, social sentiment, candidate ranking, risk decisions, strategy entry/exit logic, or order execution.
