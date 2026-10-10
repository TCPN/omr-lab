# Issue and Pull Request Workflow

## Lifecycle
Proposed → Ready → In progress → In review → Done. An issue is Ready only with a clear outcome, scope/non-goals, testable acceptance criteria, dependencies, verification steps, and known risks.

## Branches
Use names such as feat/omr-001-project-bootstrap, fix/omr-006-musicxml-validation, docs/architecture-decisions.

## PR rules
Prefer one issue per PR; link with Closes #N; keep diffs reviewable; include exact test commands/results; explain skipped integration tests; update docs for public behavior/schema changes; do not merge with required CI failing. Human review is required for schema, licensing, security, privacy, and architectural changes.

## Definition of Done
Acceptance criteria met; tests and lint pass; CI green; docs updated; no secrets/unauthorized data; limitations recorded.
