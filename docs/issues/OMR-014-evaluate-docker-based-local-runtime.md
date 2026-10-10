# OMR-014 — Evaluate Docker-Based Local Runtime

**Proposed milestone:** M4
**Priority:** P2

## Outcome
Decide whether containerization improves reproducibility and ease of local setup.

## Scope
Prototype a minimal image with documented engine installation, version pinning where feasible, volume-mounted inputs/outputs, and non-root execution where practical.

## Non-goals
Cloud deployment or embedding private scores and credentials in images.

## Acceptance criteria
- [ ] Local native setup and container setup are compared.
- [ ] Image build and one permitted sample run are documented.
- [ ] Input/output mounting and permissions are tested.
- [ ] Image size, setup friction, and engine licensing are considered.
- [ ] Adopt Docker only if evidence supports it.

## Verification
Build the image and run smoke tests when Docker is available; document skipped checks otherwise.
