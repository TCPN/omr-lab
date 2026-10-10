# OMR-006 — Parse and Validate MusicXML Output

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Validate MusicXML artifacts and normalize supported metadata without conflating parseability with musical correctness.

## Scope
Handle XML syntax errors, missing files, unsupported constructs, and parser diagnostics.

## Non-goals
Claim recognition accuracy based only on valid XML.

## Acceptance criteria
- [ ] Valid MusicXML is parsed into the internal representation.
- [ ] Malformed or missing output yields actionable errors.
- [ ] Unsupported fields are preserved where practical or explicitly warned about.
- [ ] Tests cover valid, malformed, and missing-file cases.

## Verification
Run parser unit tests using small redistributable synthetic MusicXML fixtures.
