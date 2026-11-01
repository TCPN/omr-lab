# Command-Line Interface

`omr-lab` is installed with the package (`uv sync`, then `uv run omr-lab ...`). It has four commands: `doctor` checks what this machine can run, `run` recognizes one file with the pipeline ([pipeline.md](pipeline.md)), `benchmark` runs every sample of a manifest and writes a report ([benchmark.md](benchmark.md)), and `synthesize` renders and degrades a synthetic corpus ([synthetic-corpus.md](synthetic-corpus.md)). `omr-lab --help` and `omr-lab COMMAND --help` describe every option.

## doctor

```bash
uv run omr-lab doctor                 # all engines
uv run omr-lab doctor --engine mock   # one engine
```

It prints the package version, the Python version and interpreter path, then one line per engine:

```text
omr-lab 0.1.0
Python 3.12.3 (/home/user/projects/omr-lab/.venv/bin/python)
[missing] audiveris: Audiveris was not found. Install it from https://github.com/Audiveris/audiveris/releases ...
[ok]      mock 0.1.0
```

Each engine's own `check_available()` decides the result, so the `[missing]` text is the adapter's actionable message (for Audiveris: where to install it, and `AUDIVERIS_EXE`). Checking Audiveris starts Java once to read its version. The exit code is 0 when every checked engine is available and 4 otherwise.

## run

```bash
uv run omr-lab run score.png -o runs/0001                  # Audiveris (the default engine)
uv run omr-lab run score.png -o runs/0002 --engine mock    # no recognition, for trying the CLI
uv run omr-lab run score.pdf -o runs/0003 --option outcome=failed --engine mock
uv run omr-lab run score.png -o runs/0004 --timeout 1200
uv run omr-lab run photo.jpg -o runs/0005 --preprocess page   # needs the preprocess extra
```

| Argument | Meaning |
| --- | --- |
| `INPUT` | Image (`.png`, `.jpg`/`.jpeg`, `.tif`/`.tiff`, `.bmp`) or PDF. It is only read. |
| `-o`, `--output DIR` | Run directory. It must not exist or be empty; use a new one per run. |
| `--engine NAME` | `audiveris` (default) or `mock`. The mock engine recognizes nothing; see [pipeline.md](pipeline.md#mock-engine). |
| `--option KEY=VALUE` | Engine option, repeatable; a key may be given once. |
| `--timeout SECONDS` | Engine timeout, a finite number greater than zero (default 600). |
| `--preprocess STEP[,STEP...]` | Preprocess a copy of the input before recognition, with these steps in order: `grayscale`, `page`, `deskew`, `threshold` ([preprocessing.md](preprocessing.md)). The input is not modified; the engine reads `DIR/preprocessed/input.png`. Needs `uv sync --extra preprocess`; without it the command exits with 4 before running. An unknown or repeated step is a usage error (2). |

`--option` values are read as JSON when they are a JSON scalar: a number (`3`, `0.5`), `true`, `false`, `null`, or a quoted string (`'"3"'` for the text 3). Anything else is plain text, so `outcome=failed` is the string `failed`. A JSON array or object, `NaN` and `Infinity` stay text, because engine options are JSON scalars (`EngineOptions`). The engine decides which keys it accepts; an unknown key fails the run with `invalid_option` and the engine's message.

On every run that writes a record, `run` prints `Run document: DIR/run.json` and then either `Status: succeeded (engine NAME)` on stdout or `Status: failed [CODE]: MESSAGE` on stderr. Each run warning is printed to stderr as `warning [CODE]: MESSAGE`. The run document is the full record; the printed lines are a summary. Characters that the console's encoding cannot show, such as a path that is not valid UTF-8, are printed as backslash escapes instead of stopping the command. A succeeded run is not evidence that the music was recognized correctly.

A directory that has `raw/` but no `run.json` is reported as an aborted run (a run that could not write its record, for example because the disk was full, or another run still in progress). It is left as it is for inspection; choose a new directory.

## benchmark

```bash
uv run omr-lab benchmark tests/fixtures/e2e/manifest.json -o benchmarks/reports/generated/0001 --engine mock
```

`MANIFEST` and `-o`/`--output DIR` (missing or empty) are required; `--engine`, `--option`, `--timeout` and `--preprocess` are as for `run` and apply to every sample. It prints the report paths, one line per sample and, when samples have a ground truth, the total note precision and recall and the lyrics character error rate ([metrics.md](metrics.md)). The manifest, report and exit codes are described in [benchmark.md](benchmark.md); the table below is for `doctor` and `run`.

## synthesize

```bash
uv sync --extra synthetic
uv run omr-lab synthesize benchmarks/synthetic/corpus.json -o benchmarks/synthetic/generated/v1
```

`SPEC` and `-o`/`--output DIR` (missing or empty) are required; `--seed N` replaces the specification's corpus seed. It prints the manifest path and one line per image. The specification, output and exit codes are described in [synthetic-corpus.md](synthetic-corpus.md); it needs the optional `synthetic` extra.

## Exit codes

| Code | Meaning | `error.code` in `run.json` |
| --- | --- | --- |
| 0 | `run` succeeded, or `doctor` found every checked engine available | none |
| 1 | The run failed for another reason | `engine_failed`, `engine_timeout`, `musicxml_invalid`, `preprocessing_failed`, `internal_error` |
| 2 | Invalid command line: unknown command or engine, malformed `--option`, invalid `--timeout`, unknown or repeated `--preprocess` step, or an input file name the run document cannot store; no run directory is created | none |
| 3 | Invalid input: missing, not a file, empty, unreadable, or unsupported extension | `invalid_input` |
| 4 | Engine unavailable (`run`), a checked engine unavailable (`doctor`), or `--preprocess` without the `preprocess` extra (no run directory is created) | `engine_unavailable`, or none |
| 5 | The engine rejected an option name or value | `invalid_option` |
| 6 | The output directory cannot be used (not a directory, not empty, an aborted run, or not creatable), or `run.json` could not be written (for example a full disk, which leaves an aborted run); no `run.json` is written | none |
| 130 | Interrupted with Ctrl-C. During the engine step `run.json` records `interrupted`; before or after it, no `run.json` is written and the CLI says so | `interrupted`, or no `run.json` |

Codes 3 to 5 and 1 come with a written `run.json` whose `error` gives the details. These numbers are part of the CLI's interface: scripts may rely on them, and changing one is a breaking change.
