# OMR-011 — Implement homr Adapter

**Proposed milestone:** M3
**Priority:** P1

## Outcome
Add homr as a second engine behind the shared interface.

## Scope
Verify upstream installation, CLI/API, output artifacts, version reporting, and licensing; implement a safe adapter.

## Non-goals
Change the normalized contract solely to favor one engine without an ADR.

## Acceptance criteria
- [ ] Missing dependencies yield actionable diagnostics.
- [ ] Adapter output is converted through the common MusicXML/normalization path where supported.
- [ ] Engine-specific metadata and limitations are documented.
- [ ] Unit tests mock external invocation.

## Verification
Run mocked tests and a real integration sample when prerequisites are available.
