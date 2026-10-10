# OMR-012 — Compare Audiveris and homr

**Proposed milestone:** M3
**Priority:** P1

## Outcome
Produce an evidence-based comparison of the two local OMR engines.

## Scope
Run both engines against the same permitted corpus; record versions, options, runtime, output validity, failures, and human/ground-truth correctness.

## Non-goals
Declare a winner from one sample or compare runs with different preprocessing/settings.

## Acceptance criteria
- [ ] Shared benchmark manifest is used.
- [ ] Missing/failed outputs are reported, not silently excluded.
- [ ] Metrics and exclusions are documented.
- [ ] Report distinguishes syntactic validity from musical correctness.
- [ ] Recommendation and remaining uncertainty are recorded.

## Verification
Repeat the benchmark on a fixed sample set and inspect the generated report.
