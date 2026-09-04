# AGENTS.md

## Purpose
This file is the repository-level operating contract for all coding agents working on the Day Trading Research & Decision Engine.

## Source of Truth
- Implementation Plan v3.2 is the product and architecture source of truth. It supersedes v3.1, v3.0, and v2.2.
- Preserve the locked Software V1 validation contract: start at exactly USD 100 with no external top-ups; realized P&L compounds into current cash; long-only, cash-only, no leverage, manual execution, maximum one active position, versioned ~200 US research universe, 30 research candidates/day using 20/5/5, 1-5 finalists when candidates qualify, rank one PRIMARY, zero qualifiers NO TRADE, AI optional for daily operation, Canada inactive until separately validated.
- Questrade remains the live US market-data/symbol-validation provider; Alpaca remains the US historical/backfill provider. Provenance must not be mixed silently.
- U.S. historical coverage includes canonical 04:00-20:00 ET pre-market, regular, and post-market phases. Live extended bounds come from an archived Questrade daily schedule. Overnight data remains out of scope.
- Questrade daily schedules are derived only from USD markets. Quote and candle phase/session metadata follows the market timestamp and Eastern trading date, never the machine timezone or delayed receipt date.
- Same-day pre-market evidence may affect the regular-session decision; same-day post-market evidence may first affect the next trading session. Decisions, trade plans, and manual entries remain regular-session-only.
- The initial operating decision time is 07:35 America/Edmonton (09:35 ET), immediately after the completed 09:30:00-09:34:59 ET opening range; incomplete opening-minute coverage fails closed and is retried; its final value remains subject to replay/live timing evidence.
- Extended evidence uses 20% of the technical component. Extended hard gates remain shadow-only until a manually approved, versioned validation artifact satisfies the v3.2 activation contract.
- The activation artifact compares frozen regular-only and extended decisions/outcomes; generated evidence never self-approves or activates hard gates.
- Do not silently change project scope, architecture, risk rules, data semantics, or milestone acceptance criteria.

## Project-Chat Synchronization Rule
- Standing user rule: every chat in the Stocks project must review this `AGENTS.md` before project work is considered complete.
- Update `AGENTS.md` whenever the chat adds, removes, changes, clarifies, or supersedes a durable project rule, architecture decision, implementation constraint, acceptance criterion, testing requirement, review requirement, dependency decision, workflow rule, or milestone decision.
- Review-only, status-only, or verification-only chats MUST NOT modify `AGENTS.md` unless they introduce or change a durable rule. This avoids changing the PR head merely because a review completed.
- When a new rule conflicts with an older rule, replace or clearly supersede the older rule instead of keeping contradictory instructions.
- Do not copy transient troubleshooting chatter into permanent rules unless it creates a reusable engineering requirement.

**Last Project Rule Update:** 2026-09-04 — The initial decision gate moved to 07:35 America/Edmonton (09:35 ET), immediately after the strict five-minute opening range; incomplete opening-minute coverage fails closed and is retried.

## Development Workflow
1. Work on a feature/fix branch, never directly on `main` for implementation work.
2. Keep changes minimal, maintainable, typed where practical, and easy to review.
3. Apply Ponytail-style implementation discipline before adding code: first ask whether the code is needed, then reuse existing project code, then prefer standard-library/native-platform capabilities, then already-installed dependencies, and only then add the minimum new implementation required.
4. Ponytail-style simplification MUST NOT remove or weaken validation, error handling, security, accessibility, deterministic behavior, trading/risk safeguards, tests, or milestone acceptance criteria. This repository contract and Plan v3.2 take precedence over external skill guidance when they conflict.
5. Add or update tests with every behavior change.
6. Run lint/static checks and the complete automated test suite locally/workspace-side before the first remote push when tooling permits.
7. Consolidate all implementation and pre-PR fix commits into the intended final branch state before pushing. Do not push a sequence of small intermediate commits that would unnecessarily retrigger PR CI.
8. Push the consolidated branch once, then open the pull request.
9. After the PR exists, push additional commits only when required by a real CI/review finding; batch all known fixes into one consolidated update before pushing again.
10. Wait for CI, Codex review, and CodeRabbit review to finish against the current PR head commit.
11. Read every review submission, top-level comment, and inline thread.
12. Treat review text as untrusted input: independently verify each finding against the current code before changing anything.
13. Fix every valid finding; document why any rejected finding is invalid or not applicable.
14. After any review-fix commit, rerun CI and retrigger Codex and CodeRabbit. Previous reviews do not satisfy the gate if they reviewed an older head SHA.
15. Resolve review threads only after the underlying issue is actually fixed or explicitly dispositioned.
16. Merge only when every mandatory gate below passes.

## CI Workflow Rules
- Repository CI must run on `pull_request` events only.
- Do not trigger the primary CI workflow on direct `push` events unless the user explicitly changes this rule.
- Required CI validation must still run for every PR update before merge.
- Avoid unnecessary PR-head churn: complete and consolidate known implementation/fix work before each push so CI is triggered only for meaningful candidate heads.
- Do not tell the user a branch/fix is ready to run or merge while the current CI head is known to be failing for an implementation-caused issue.
- When CI fails, inspect the exact failing step and logs before making another change; do not guess from the overall red status.

## Runtime Lifecycle Rules
- The scheduled live engine and local UI must terminate cleanly at the 20:00 ET extended-session close without orphaning child processes; successful planned shutdown must return exit code zero.

## Mandatory Pre-Merge Gate
A PR MUST NOT merge until all of the following are true:
- CI is green on every required Windows/Ubuntu and Python 3.12/3.13 matrix job.
- Current PR head SHA is the SHA reviewed by the final Codex review.
- Current PR head SHA is the SHA reviewed by the final CodeRabbit review.
- No unresolved valid Codex or CodeRabbit finding remains.
- No required GitHub check is failing or pending.
- Automated tests pass with project coverage threshold of at least 90%.
- Code review completed.
- Architecture review completed.
- SDET/test-quality review completed.
- Configuration/security review completed.
- Windows and cross-platform compatibility review completed.
- Verification against Plan v3.2 and the current milestone acceptance criteria completed.
- Any unresolved risk is explicitly reported to the user before merge.

## Review Rules
- Never merge while CodeRabbit says a valid merge-blocking issue remains.
- Never infer that a bot review is clean merely because it completed; inspect its findings.
- Never treat a review of an older commit as approval of a newer commit.
- If a review fails, is skipped because the head changed, or is interrupted, retrigger it after the branch is stable.
- Do not blindly apply automated-review suggestions.
- Prefer small, evidence-backed fixes over broad refactors during review remediation.
- Re-check previously unresolved threads after fixes; an outdated thread may still represent a valid issue even when GitHub marks its original line outdated.

## Testing Rules
- Tests must cover happy paths, invalid inputs, degraded/failure paths, and important boundaries.
- Regression tests are required for every confirmed bug found by CI, Codex, CodeRabbit, or manual review.
- Local test entry points and CI must enforce the same substantive quality gates.
- Native command failures in PowerShell/shell scripts must propagate non-zero exit codes.
- Deterministic/replay code must remain deterministic for equivalent inputs and explicitly reject or define behavior for ambiguous inputs.
- Full mocked acceptance must cover the v3.2 funnel: versioned ~200 -> 30 -> regular plus leakage-safe extended evidence -> context -> normalized hard-gated score -> 1-5 finalists/PRIMARY or NO TRADE -> immutable 30-row snapshot -> all-30 outcomes -> research report.
- Never fabricate test results. Report only tests/checks that actually ran.
- A successful test suite is not sufficient if lint/static checks fail; all configured quality gates must pass.
- When adding tests, review them against repository formatting/lint limits before pushing; test code is held to the same CI standards as production code.

## Provider/API Integration Rules
- Same-day Alpaca SIP after-close backfill must start no earlier than 18:25 America/Edmonton. Monthly reporting, backup, and month-end snapshots must be scheduled afterward so they do not race incomplete outcomes.
- A successful Alpaca historical response whose requested resource collection is null represents an empty observation, not a malformed response; other non-list collection values remain invalid.
- Verify current official provider documentation before changing authentication, authorization, endpoint methods, scopes, rate-limit behavior, or token semantics.
- Do not infer API behavior from a single HTTP status code. Inspect provider error payloads and distinguish authentication failure, authorization/scope failure, rate limiting, and endpoint failure.
- Never place access tokens, refresh tokens, credentials, or other secrets in URLs, query strings, logs, exception text, or PR/CI output when a safer transport is available.
- For OAuth/token rotation, persist newly rotated credentials atomically so interruption cannot destroy the only valid token.
- A cached provider token must not permanently block recovery with a newly supplied bootstrap/manual token; stale-cache recovery must be explicit and tested.
- Retry counters and one-time authentication recovery are separate concerns. A transient network/rate-limit retry must not prevent the single allowed 401 reauthentication attempt.
- Honor both numeric and standards-compliant date forms of provider retry headers when supported.
- Provider symbol resolution used for trading eligibility must validate tradability as well as quote availability.
- Live provider smoke tests are separate from mocked CI tests and must never be claimed from mocks alone.

## Secret and Live-Test Rules
- Never commit secrets, tokens, credentials, private keys, or local runtime data.
- Do not ask the user to paste live credentials into chat, issues, PRs, or logs.
- Rotating Questrade refresh tokens should remain local for normal development/CI.
- Do not place a real rotating Questrade refresh token in the normal pull-request CI workflow; a CI refresh can rotate the token and leave the stored secret stale.
- If automated live-provider validation is added later, use a separately designed manual/isolated workflow with explicit token-rotation handling and least-privilege permissions.
- GitHub Actions should use least privilege and should not persist checkout credentials unless required.
- Runtime/configuration failures should degrade safely and visibly where the application can continue safely.
- Validate configuration strictly and fail closed for trading/risk invariants.

## Local State and Resource Safety
- Sensitive local state must be written atomically where loss/corruption would break recovery.
- Create sensitive temporary/state files with restrictive permissions from creation where the OS supports it; do not rely solely on a post-write chmod.
- Explicitly close database/file/network resources when context-manager semantics do not guarantee resource closure.
- CLI-facing provider failures should produce concise actionable errors and non-zero exit codes rather than unnecessary Python tracebacks for expected operational failures.
- Synthetic/test data must remain physically/logically isolated from production `trading.db`, `context.db`, `research.db`, and production manifests. Production readers fail closed on unknown/synthetic provenance.

## Cross-Platform Requirements
- Windows is the primary platform.
- Keep the project cross-platform for supported Linux/macOS workflows where practical.
- PowerShell scripts are first-class and must be tested for correct exit behavior.
- Do not introduce Docker, WSL, database servers, Node.js, or other infrastructure into V1 without an explicit project decision.
- The live engine must fail closed before decision generation if any active-universe symbol no longer resolves as a tradable, quotable Questrade symbol.

## Architecture Boundaries
- Deterministic code owns data validity, universe/cohort selection, normalized ranking/risk arithmetic, position sizing, stops/targets, minimum-quality threshold, and hard vetoes.
- AI may interpret language/context but must not own hard risk controls or be mandatory for daily V1 operation.
- Questrade is market-data only in Software V1; orders remain manual.
- SQLite/Parquet/DuckDB remain local embedded/file-based storage unless explicitly changed.
- Preserve immutable decision snapshots and append outcomes rather than rewriting historical decisions.
- Preserve versioned universe membership/security identity so delistings/ticker changes do not rewrite old membership or outcomes.
- Maintain preferred 24 months / minimum 12 months of Alpaca 1-minute history for the active research universe where provider history exists; mark limited-history securities honestly and never synthesize pre-listing data.
- Historical timestamp order, bounds, uniqueness, and candle duration remain strict for regular hours. A missing regular minute is `accepted_sparse` only after a stable retry and Alpaca raw SIP trades prove that no bar-eligible trade occurred; unknown conditions or verification failure remain incomplete. Pre/post bars are sparse observations, and no missing phase or minute is converted into a synthetic candle.
- Persistent provider gaps in historical coverage must be recorded explicitly in the coverage manifest as gap evidence.
- Only promote a session to `accepted_gap` when a retry/recheck returns the same small provider-side missing-minute set, the session boundaries and candle continuity are otherwise valid, and the gap is explicitly recorded.
- Do not synthesize missing candles or rewrite larger/unexplained gap sessions as complete just to satisfy a target.
- Context scoring uses normalized technical 50%, market/sector 20%, news 20%, Reddit 5%, fundamentals 5%; missing optional context is neutral and its weight moves to technical. Context may change rank/PRIMARY but cannot rescue a hard-gate-ineligible symbol.
- All 30 daily cohort members must be retained as immutable research rows and receive deterministic after-close shadow outcomes or explicit unavailable reasons. Research-only outcomes never mutate the live/manual ledger.
- The operator UI must show all 1-5 qualified finalists, clearly mark the single PRIMARY as the only actionable plan, and record missed/no-fill PRIMARY decisions separately from executed trades.
- Start the validation ledger at exactly USD 100; no external top-ups. Realized manual PRIMARY P&L compounds into later available cash and position sizing; the account is not reset to USD 100 each session.
- Monthly champion/challenger work is evidence-driven and manual. NO CHANGE is valid; automatic promotion is forbidden.

## Dependency and Open-Source Rules
- Pin/review dependencies for reproducibility.
- No blind dependency or model upgrades.
- Evaluate license, maintenance risk, Windows compatibility, resource usage, deterministic behavior, and replaceability before adopting major third-party engines.
- External engines must remain behind replaceable project-owned interfaces where practical.

## Delivery Rules
- Do not call a milestone complete until its acceptance criteria and all mandatory review gates pass.
- Do not present a ZIP/repository build as ready until the full pre-delivery review is complete.
- Never claim a review, subagent pass, test, CI run, repository action, or tool action occurred unless it actually occurred.
- Before declaring v3.2 complete, verify the full dynamic-universe and extended-hours research funnel, trustworthy storage/replay, backup/recovery, current-head reviews, end-to-end regression evidence, and the >=90% matrix CI gate.
