# Plan v2.2 gap closure

This change closes the remaining software gaps that can be implemented without fabricating live-provider, historical-duration, forward-validation, soak, or Canadian-market evidence.

## Implemented

- M0 is resolved by explicitly rejecting NautilusTrader for Software V1 and retaining the already-working project-owned simulator behind `SimulationEngine`.
- Resumable one-minute historical backfill by exchange session.
- Per-session coverage status, row counts, output file checksums and failure reasons.
- BAR_ONLY fidelity and historical quote/spread/latency availability are explicit in the coverage manifest.
- Dated historical-universe manifests with survivorship-risk labeling.
- Daily research-data backup primitives using SQLite's online backup API plus file checksums.
- OAuth/token files are explicitly excluded from backups.
- Backup verification rejects missing, corrupted, unsafe, and undeclared data files; restore copies only manifest-declared verified data.
- Backup status is persisted locally so the dashboard can warn when the backup is on the same storage volume.
- Windows Task Scheduler and cron setup wrappers for automatic daily backups.
- Month-end research snapshots include checksummed algorithm/config/schema metadata in the integrity manifest.
- PowerShell and shell wrappers for backup, restore, historical backfill and month-end snapshots.
- Live 30+ symbol Questrade capacity-gate runner that records quote count, failures, elapsed time, CPU time, peak memory, latency and minimum REST request load.
- Regression tests for resume behavior, fidelity metadata, universe manifests, secret exclusion, backup verification/restore, month-end version metadata and the 30-symbol boundary.

## Evidence-only gates that remain pending by design

These cannot be truthfully marked PASS from mocked CI:

1. **M2 live provider gate** — run `capacity-gate.ps1` with at least 30 valid symbols using the user's live Questrade entitlement and keep the generated JSON evidence.
2. **M3 historical coverage gate** — run `backfill.ps1` until `coverage_manifest.json` reaches the 12-month minimum target, preferably 24 months. Provider/entitlement gaps remain recorded rather than synthesized.
3. **Quarterly restore drill** — run `restore.ps1 D:\day-trading-backups\20260824T183000Z C:\temp\restore-check --verify-only` using an existing backup and an empty destination, and periodically perform a real restore to an empty directory.
4. **M10/M11 operating evidence** — month-end reports, holdout consumption and champion/challenger cycles require actual collected sessions. NO CHANGE remains valid.
5. **M12 soak/live-realism evidence** — a full trading-session soak and paper-to-manual execution measurements require target-machine runtime data.
6. **M13 Canadian activation evidence** — Canadian activation remains disabled until currency, entitlement, market-calendar and dedicated replay/paper gates pass.

## L1 streaming disposition

Questrade's official developer platform documents its customer API as REST over HTTPS (TLS), including Level 1 quote access, and does not document a supported RawSocket or WebSocket market-data interface. V1 therefore keeps batched REST as the supported transport and defers streaming unless Questrade publishes a secure, verified streaming contract.

Provider source: Questrade Developer Platform, https://developer.questrade.com/ (accessed 2026-08-25).

## Acceptance interpretation

Software implementation and automated tests can be complete while provider/data-duration/operating gates remain pending. Those gates are evidence requirements, not code stubs, and must not be marked complete until their artifacts exist.
