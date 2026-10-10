# OMR-004 — Implement Pipeline with Mock Engine

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Implement end-to-end orchestration independently of any installed OMR engine.

## Scope
Validate inputs, create run output directories, call an engine interface, retain raw artifacts, normalize results, and record provenance/timing.

## Non-goals
Real optical music recognition or network services.

## Acceptance criteria
- [ ] Pipeline is testable with a deterministic mock engine.
- [ ] Failures preserve useful diagnostics and a failed-run record.
- [ ] Raw artifacts and normalized JSON are stored separately.
- [ ] Tests require no Java, model downloads, network, or credentials.

## Verification
Run unit tests for success, missing input, engine failure, and output handling.
