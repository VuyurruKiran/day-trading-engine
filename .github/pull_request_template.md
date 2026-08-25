## Summary

Describe the change and the plan/milestone it implements.

## Validation checklist

- [ ] Change is scoped to the intended milestone/fix.
- [ ] Tests added/updated for behavior changes and confirmed bugs.
- [ ] Happy path, boundary, invalid-input, and degraded/failure paths considered.
- [ ] Local lint/static checks pass.
- [ ] Local automated tests pass with required coverage (minimum 85%).
- [ ] Windows behavior reviewed.
- [ ] Cross-platform behavior reviewed.
- [ ] Configuration/security review completed.
- [ ] Architecture review completed.
- [ ] SDET/test-quality review completed.
- [ ] Plan v2.2/current acceptance criteria verified.

## Review gate

Do not merge until all items below are complete against the CURRENT PR head SHA.

- [ ] All required GitHub Actions jobs are green.
- [ ] Codex review completed against current head SHA.
- [ ] CodeRabbit review completed against current head SHA.
- [ ] Every review comment/thread was read and independently verified.
- [ ] Every valid finding is fixed.
- [ ] Invalid/not-applicable findings have a documented disposition.
- [ ] Review-fix commits were followed by fresh CI and fresh Codex/CodeRabbit reviews.
- [ ] No unresolved valid review threads remain.
- [ ] No required check is pending or failing.
- [ ] Any remaining risk has been explicitly reported before merge.

## Final head SHA

`<fill immediately before merge>`

## Notes / residual risks

None, or list explicitly.
