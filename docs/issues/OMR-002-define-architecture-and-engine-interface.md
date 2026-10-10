# OMR-002 — Define Architecture and Engine Interface

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Define a stable boundary between pipeline orchestration and pluggable OMR engines.

## Scope
Document input/output contracts, engine availability checks, process execution, artifact paths, errors, and engine metadata.

## Non-goals
Implement a production engine adapter or cloud service.

## Acceptance criteria
- [ ] Architecture docs describe pipeline stages and module responsibilities.
- [ ] Engine interface represents success/failure, output artifacts, version, and diagnostics.
- [ ] External process invocation uses argument lists and timeouts.
- [ ] Unit tests can use a mock engine without Java or network access.

## Verification
Review interface examples and add tests for mock-engine behavior and failure reporting.
