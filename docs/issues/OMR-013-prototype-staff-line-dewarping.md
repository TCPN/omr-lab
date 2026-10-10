# OMR-013 — Prototype Staff-Line Dewarping

**Proposed milestone:** M4
**Priority:** P2

## Outcome
Evaluate whether staff-aware dewarping improves recognition on curved or warped pages.

## Scope
Build a reversible, optional prototype and compare original/corrected images with identical engine settings.

## Non-goals
Assume straighter staff lines necessarily yield more correct notes.

## Acceptance criteria
- [ ] Prototype does not overwrite source images.
- [ ] Transformation settings and outputs are recorded.
- [ ] Evaluation includes pitch/rhythm/notation correctness when ground truth exists.
- [ ] Failure cases and geometric artifacts are documented.
- [ ] Keep or reject the approach based on benchmark evidence.

## Verification
Use synthetic curved staff images plus permitted real examples; compare downstream recognition outcomes.
