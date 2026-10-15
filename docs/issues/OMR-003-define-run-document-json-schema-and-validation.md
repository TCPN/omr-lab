# OMR-003 — Define Run Document JSON Schema and Validation

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Specify a versioned normalized JSON contract that records only facts supported by OMR output.

## Scope
Define required and nullable fields, run status/error shape, provenance, artifacts, timing, warnings, and schema validation.

## Non-goals
Invent coordinates, confidence values, or musical properties absent from engine output.

## Acceptance criteria
- [ ] A checked-in JSON Schema defines the contract.
- [ ] Valid and invalid example documents are tested.
- [ ] Missing musical content is represented explicitly, not fabricated.
- [ ] Schema version and engine provenance are represented.

## Verification
Run positive and negative schema-validation tests.
