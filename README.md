# OMR Lab

Local-first, modular Optical Music Recognition (OMR) pipeline:

`image/PDF → optional preprocessing → OMR engine → MusicXML validation → normalized JSON`.

## Status
- Phase 1: Local OMR Foundation
- Initial engine candidate: Audiveris (verify version, CLI, and license before use/distribution)
- Second candidate: homr
- Interchange format: MusicXML
- Python, uv, pytest, Ruff

## Quick start
Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run omr-lab --help
```

The bootstrap project does not require Java or an OMR engine. Real recognition is a later milestone.

## Source of truth
- `AGENTS.md`: coding-agent rules (`CLAUDE.md` imports it for Claude Code)
- `docs/architecture.md`: boundaries and data flow
- `docs/json-schema.md`: normalized run document contract (schema in `src/omr_lab/schemas/`)
- `docs/governance/`: roles, workflow, milestones and labels
- `docs/issues/`: proposed issue bodies (not yet live GitHub Issues)
- `docs/research/`: research notes and sources
- `src/omr_lab/`: Python package
- `tests/`: automated tests

## Collaboration
ChatGPT plans and reviews; GitHub Issues define bounded tasks; Codex and Claude Code implement and test; a human reviews, merges, and answers `decision: needed` issues. Durable decisions must be written to this repository rather than relying on chat history.
