# OMR-008 — Add End-to-End Test with a Sample Score

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Demonstrate one complete local OMR path using a permitted score sample.

## Scope
Document sample provenance and permission, run the selected engine, validate MusicXML, validate normalized JSON, and retain test evidence.

## Non-goals
Commit copyrighted or unlicensed score material.

## Acceptance criteria
- [ ] Sample licensing/provenance is recorded.
- [ ] Test exercises input-to-normalized-output flow.
- [ ] Engine-dependent tests are marked as integration tests.
- [ ] Output generation and musical correctness are reported separately.
- [ ] If no redistributable sample is available, the test documents external fixture setup.

## Verification
Run unit tests by default and the real-engine test when prerequisites are available.
