# OMR-005 — Implement Audiveris Environment Check and Adapter

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Add a local Audiveris adapter with actionable environment diagnostics.

## Scope
Verify executable availability/version and actual CLI behavior; invoke it safely; collect exit code, logs, and output artifacts.

## Non-goals
Assume a specific CLI without verification or bundle the executable.

## Acceptance criteria
- [ ] Missing Audiveris produces a clear actionable message.
- [ ] Invocation uses subprocess argument lists and timeout handling.
- [ ] Engine version and diagnostics are captured when available.
- [ ] Tests mock process execution and do not require Audiveris.
- [ ] License obligations are documented for human review.

## Verification
Run mocked unit tests; run real-engine integration tests only where Audiveris is installed.
