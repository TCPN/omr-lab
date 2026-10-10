# OMR-010 — Add Basic OpenCV Preprocessing

**Proposed milestone:** M2
**Priority:** P1

## Outcome
Add optional, measurable preprocessing for common scan/photo issues.

## Scope
Evaluate grayscale conversion, thresholding, deskewing, and safe output naming behind explicit options.

## Non-goals
Overwrite original images or assume preprocessing improves every score.

## Acceptance criteria
- [ ] Original image remains unchanged.
- [ ] Transformations are optional and independently tested.
- [ ] Parameters and transformed artifact paths are recorded.
- [ ] Benchmark protocol compares downstream recognition quality.

## Verification
Use synthetic image fixtures; benchmark with the same engine/settings on original and transformed inputs.
