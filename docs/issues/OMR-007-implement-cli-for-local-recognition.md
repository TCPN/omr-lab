# OMR-007 — Implement CLI for Local Recognition

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Provide a documented CLI for diagnostics and local recognition runs.

## Scope
Implement `doctor` and `run`, input/output arguments, engine selection, actionable errors, and stable exit codes.

## Non-goals
Cloud execution, background jobs, or claiming recognition is implemented before an adapter is available.

## Acceptance criteria
- [ ] Help text documents commands and options.
- [ ] Doctor reports required local dependencies.
- [ ] Run validates inputs and reports output location and failures.
- [ ] CLI tests cover argument parsing and exit codes.
- [ ] README examples match actual behavior.

## Verification
Run CLI unit tests and manually check help and doctor commands.
