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

## Test selection
Tests that need more than a plain Python environment carry a pytest marker:

| Marker | Needs |
| --- | --- |
| `integration` | a real OMR engine, Java, or other external tools |
| `gpu` | a CUDA-capable GPU |
| `network` | network access, model downloads, or API credentials |

`uv run pytest` (and CI) deselects all marked tests; the summary line reports them as
`deselected`. A `-m` on the command line replaces the default selection, so opt in with:

```bash
uv run pytest -m integration            # only integration tests
uv run pytest -m "integration or gpu"   # several groups
uv run pytest -m ""                     # everything, marked or not
```

`--strict-markers` is on, so an unregistered marker is an error; register new markers in
`pyproject.toml`. If no test matches the selection, pytest exits with code 5.

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
