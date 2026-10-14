# OMR Engine Interface

This contract separates pipeline orchestration from engine-specific behavior. Adapters for Audiveris and homr should implement the same protocol; this issue does not add either production adapter.

## Contract

- Engine.name: stable engine identifier.
- Engine.check_available(): local prerequisite check, returning EngineAvailability with availability, optional version, and an actionable message when unavailable.
- Engine.recognize(input_path, output_dir, *, timeout_seconds, options=None): run recognition and return an EngineResult. See [Engine options](#engine-options).
- EngineResult: success/failure, engine name/version, raw artifact paths, diagnostics, process return code, an optional error message, and the effective options (`options`, `{}` by default).
- run_command(...): shared subprocess helper that passes an argument sequence without a shell, captures stdout/stderr, enforces a timeout, and normalizes launch/timeout failures. See [Process execution](#process-execution).

## Engine options

Engine-specific settings (for example `device` = `"cpu"`/`"cuda"`, a model variant, or an Audiveris flag) are passed as a keyword-only `options` mapping so that they can be recorded for provenance and fair benchmarking.

- Types: `EngineOptions = Mapping[str, EngineOptionValue]` with `EngineOptionValue = str | int | float | bool | None`. Option names are strings; values are JSON scalars, and floats must be finite. `options=None` is treated as `{}`.
- Why scalars only: the effective options are written verbatim to the run document's top-level `options` object (docs/json-schema.md), so every value must round-trip through `json.dumps(..., allow_nan=False)` without a custom encoder. Scalars also compare and diff cleanly between benchmark runs. Paths (for example a custom model file) are passed as strings; the adapter converts them with `pathlib`. Lists or nested objects are not supported yet; extend the alias in a separate issue if an engine needs them.
- Validation: each adapter declares its supported options with default values and calls `resolve_options(engine_name, options, defaults)` before running anything. It returns the effective options (defaults overridden by requested values) and raises:
  - `UnknownEngineOptionError` (a `ValueError`) naming the engine, the unknown option names, and the supported names, for example `Unknown option(s) for engine 'fake': devcie. Supported options: device, model.` Its `engine_name`, `unknown`, and `supported` attributes hold the same information.
  - `InvalidEngineOptionError` (a `ValueError`, base class of the above) for non-string names and for non-scalar or non-finite values.
  A default of `None` means "not set; the engine's own default applies" and is recorded as `null`. Per-option type/range checks (for example accepting only `cpu`/`cuda`) stay in the adapter and should raise `InvalidEngineOptionError` with an equally actionable message.
- Echo: the adapter returns the effective options in `EngineResult.options`, including on failed runs, so that the run record shows exactly which settings were used.
- Run output: the pipeline copies `EngineResult.options` into the run document's top-level `options` object. Invalid options are raised before recognition starts; the pipeline records them as a failed run with error code `invalid_option` and `options: {}`. See docs/pipeline.md.
- Mapping options to command-line arguments is the adapter's job and must keep using argument lists via `run_command`; never interpolate option values into a shell string.

## Process execution

- Output decoding: stdout and stderr are captured as bytes and decoded as UTF-8 with `errors="replace"`, independent of the locale (for example cp950 on Traditional Chinese Windows). Invalid bytes become U+FFFD. Line endings are normalized to `\n`, as in text-mode pipes.
- Process tree on timeout: the command starts in its own process group so that a wrapper script and the processes it launches (for example `java` behind the Audiveris launcher) are terminated together.
  - POSIX: the child starts in a new session (`start_new_session=True`); on timeout the whole process group receives `SIGKILL` via `os.killpg`.
  - Windows: the child starts with `CREATE_NEW_PROCESS_GROUP`; on timeout `taskkill /T /F /PID <pid>` terminates the child and its descendants, then the direct child is killed as a fallback.
- Partial output written before a timeout is returned in `stdout`/`stderr`; `returncode` stays `None` for a timed-out command.
- Known limitations: on POSIX, a descendant that moves itself into another session or process group (for example a daemonizing process) escapes the group kill. On Windows, `taskkill /T` follows parent process ids, so a descendant whose parent already exited is not found; a job object would close this gap and is not implemented. If such an escaped process keeps the output pipes open, run_command stops collecting output after a bounded drain period instead of hanging. On Windows the output pipes are then left open to the reader threads of `communicate()` (closing them while a read is blocked would hang); they finish once the escaped process exits.

## Result semantics

A successful adapter result means the adapter completed its operation and reported its artifacts. It is **not** a claim that the score was transcribed correctly. MusicXML validation/parsing and musical correctness evaluation belong to later pipeline stages. Adapters must not invent coordinates, confidence values, or score properties absent from engine output.

## Error handling

- Missing executables and other process launch errors are returned as diagnostics in CommandResult.error.
- Timeouts are indicated by timed_out=True and a descriptive error.
- Non-zero process exits remain visible through returncode, stdout, and stderr; adapter code maps these to EngineResult(success=False, ...).
- Adapter-specific errors should be actionable and should not silently discard stderr or raw artifacts.

## Test strategy

Unit tests use a fake engine and monkeypatched subprocess calls. They must not require Java, network access, model downloads, or installed OMR engines. Real adapter checks are integration tests and are separate from the default unit-test suite.
