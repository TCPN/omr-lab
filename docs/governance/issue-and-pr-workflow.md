# Issue and Pull Request Workflow

## Lifecycle
Proposed → Ready → In progress → In review → Done. An issue is Ready only with a clear outcome, scope/non-goals, testable acceptance criteria, dependencies, verification steps, and known risks.

## Branches
Use names such as feat/omr-001-project-bootstrap, fix/omr-006-musicxml-validation, docs/architecture-decisions.

## PR rules
Prefer one issue per PR; link with Closes #N; keep diffs reviewable; include exact test commands/results; explain skipped integration tests; update docs for public behavior/schema changes; do not merge with required CI failing. Human review is required for schema, licensing, security, privacy, and architectural changes.

## Decisions
Questions the human owner must decide get their own issue from the **Decision request** template, labeled `decision: needed`. Use one for anything that blocks work or affects architecture, cost, licensing, privacy, or data use; small questions can be answered in the working conversation.

- The issue states the question, context, options with trade-offs, a recommendation, and what it blocks. Blocked issues link back to it.
- The owner answers or asks follow-up questions in the issue thread.
- Once decided, record the outcome where it belongs (an ADR in `docs/decisions/` for consequential choices, otherwise the affected docs), link it in the issue, and close the issue.
- List open decisions with `gh issue list --label "decision: needed"`.

## Definition of Done
Acceptance criteria met; tests and lint pass; CI green; docs updated; no secrets/unauthorized data; limitations recorded.
