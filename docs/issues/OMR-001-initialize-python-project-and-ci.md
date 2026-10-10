# OMR-001 — Initialize Python Project and CI

**Proposed milestone:** M1
**Priority:** P0

## Outcome
Establish a reproducible Python development environment and automated quality checks.

## Scope
- Confirm the src-layout package, pyproject.toml, and uv workflow.
- Add or verify pytest and Ruff configuration.
- Configure GitHub Actions to install dependencies, lint, and test on pushes and pull requests.

## Non-goals
- Implement OMR engine integration.
- Add network-dependent tests or score datasets.

## Acceptance criteria
- [ ] A clean Python 3.11+ environment can install the project and development dependencies.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run pytest` passes.
- [ ] CI runs lint and tests for pushes and pull requests.
- [ ] README documents the local setup commands.

## Verification
Run the documented setup, lint, and test commands in a clean environment and inspect the GitHub Actions run.

## Risks
Keep dependencies minimal; do not commit virtual environments, generated files, credentials, or unlicensed scores.
