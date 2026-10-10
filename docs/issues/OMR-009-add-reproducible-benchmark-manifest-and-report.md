# OMR-009 — Add Reproducible Benchmark Manifest and Report

**Proposed milestone:** M2
**Priority:** P1

## Outcome
Create repeatable benchmark manifests and comparable run reports.

## Scope
Record sample IDs/checksums, source and license, input condition, engine/version/options, duration, output validity, correctness review, and exclusions.

## Non-goals
Compare engines on different or undocumented datasets.

## Acceptance criteria
- [ ] Manifest format is documented and validated.
- [ ] Reports record environment and engine configuration.
- [ ] Failed runs and exclusions remain visible.
- [ ] Generated reports are not confused with source-controlled ground truth.

## Verification
Run the report generator against a small synthetic manifest and check deterministic fields.
