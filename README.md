# OMR Lab

Local-first, modular Optical Music Recognition (OMR) pipeline:

`image/PDF → optional preprocessing → OMR engine → MusicXML validation → normalized JSON`.

## Status
- Phase 1: Local OMR Foundation
- Initial engine candidate: Audiveris, adapter in `omr_lab.engines.AudiverisEngine`; setup, verified CLI, and AGPL notes in `docs/engines/audiveris.md` (run only as an unmodified external program, see [ADR 0002](docs/decisions/0002-project-license-and-agpl-engines.md))
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

Installing and testing the project does not require Java or an OMR engine.

## Command line
```bash
uv run omr-lab doctor                                    # which engines this machine can run
uv run omr-lab run score.png -o runs/0001                # recognize with Audiveris (the default)
uv run omr-lab run score.png -o runs/0002 --engine mock  # try the CLI without an engine
```

`run` writes `runs/0001/run.json` (the run document, [docs/json-schema.md](docs/json-schema.md)) and the engine's raw output under `runs/0001/raw/`, and exits with a documented code (0 on success). Options, output and exit codes are in [docs/cli.md](docs/cli.md). `omr-lab benchmark MANIFEST -o DIR` runs every sample of a benchmark manifest and writes a report ([docs/benchmark.md](docs/benchmark.md)). `omr-lab synthesize SPEC -o DIR` renders and degrades a synthetic corpus with exact ground truth ([docs/synthetic-corpus.md](docs/synthetic-corpus.md)); it needs `uv sync --extra synthetic`. The mock engine recognizes nothing; real recognition needs Audiveris (see `docs/engines/audiveris.md`).

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

## End-to-end test
`tests/fixtures/e2e/` holds one redistributable sample: `twinkle.png`, rendered from `twinkle.musicxml`, which is its exact ground truth (12 measures, chord symbols, one lyric verse; provenance and CC0 license in its README). Two tests use it:

- `tests/unit/test_e2e_sample.py` (default suite, no engine): checks the sample's checksums and runs it through `omr-lab run` with a stand-in engine that returns the ground truth, so the CLI, pipeline, MusicXML loading, score conversion and run document are checked end to end. It also tests the correctness report (`tests/e2e_report.py`) that the integration test uses.
- `tests/integration/test_e2e_audiveris.py` (marker `integration`): runs `omr-lab run` with the real Audiveris. It asserts output generation (exit 0, a valid `run.json` with `status: succeeded`, a loadable MusicXML, a `score`) and reports musical correctness against the ground truth separately, in `correctness.json` next to `run.json`, without asserting it.

```bash
OMR_LAB_E2E_OUTPUT=outputs/e2e uv run pytest -m integration -s tests/integration/test_e2e_audiveris.py
```

`OMR_LAB_E2E_OUTPUT` keeps each run directory (`outputs/e2e/twinkle-<UTC timestamp>/`, ignored by Git; the timestamp has microseconds, so repeated runs never share a directory) as evidence; without it the run goes to pytest's temporary directory. The test skips when Audiveris is not installed.

## Source of truth
- `AGENTS.md`: coding-agent rules (`CLAUDE.md` imports it for Claude Code)
- `docs/architecture.md`: boundaries and data flow
- `docs/json-schema.md`: normalized run document contract (schema in `src/omr_lab/schemas/`)
- `docs/pipeline.md`: pipeline orchestration, output layout, and the mock engine
- `docs/cli.md`: `omr-lab doctor`, `omr-lab run`, `omr-lab benchmark` and `omr-lab synthesize`, options and exit codes
- `docs/benchmark.md`: benchmark manifest and report formats
- `docs/synthetic-corpus.md`: synthetic corpus specification, degradations and reproducibility
- `docs/musicxml.md`: MusicXML loading, validation errors/warnings, and XML safety
- `docs/governance/`: roles, workflow, milestones and labels
- `docs/issues/`: proposed issue bodies (not yet live GitHub Issues)
- `docs/research/`: research notes and sources
- `docs/engines/`: per-engine setup, verified CLI behavior, and license notes
- `src/omr_lab/`: Python package
- `tests/`: automated tests

## License
omr-lab is released under the [MIT License](LICENSE). OMR engines are installed separately and keep their own licenses; Audiveris is AGPL-3.0. The policy for such engines is [ADR 0002](docs/decisions/0002-project-license-and-agpl-engines.md).

## Collaboration
ChatGPT plans and reviews; GitHub Issues define bounded tasks; Codex and Claude Code implement and test; a human reviews, merges, and answers `decision: needed` issues. Durable decisions must be written to this repository rather than relying on chat history.
