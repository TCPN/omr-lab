# Pipeline

`omr_lab.pipeline.run_pipeline()` runs one input file through one engine and writes a validated [run document](json-schema.md). It orchestrates only: engine-specific invocation stays in the adapters (`omr_lab.engines`). MusicXML validation (issue #8) and a CLI (issue #9) are not part of it yet.

```python
from omr_lab.engines.mock import MockEngine
from omr_lab.pipeline import run_pipeline

result = run_pipeline("score.png", "runs/0001", MockEngine(), timeout_seconds=60, options={})
result.succeeded  # pipeline completion, not musical correctness
result.document  # the validated run document (dict)
result.document_path  # runs/0001/run.json
```

## Output layout

```
<output_dir>/
  run.json   normalized run document (schema 0.1.0)
  raw/       raw engine artifacts; the engine's output_dir
```

- `output_dir` is created (with parents) if missing. It must not exist or be an empty directory; otherwise `OutputDirectoryError` (a `ValueError`) is raised before anything is written, so earlier results are never overwritten or mixed with a new run. This also keeps the input file's own directory from being used as the output directory.
- The input file is only read (for its SHA-256 and by the engine); it is never modified, moved, or copied.
- `run.json` is UTF-8 (non-ASCII text such as Chinese lyrics in diagnostics is written as-is), validated with `validate_run_document()` before it is written, and written through a temporary file that is then renamed.

## Steps

1. Check arguments that make a record impossible and raise `ValueError` for them: `timeout_seconds <= 0`, an empty engine name, an input file name that is empty or contains a backslash or control character (the schema stores the base name only), and an unusable `output_dir` (`OutputDirectoryError`: not a directory, not empty, or any `OSError` while checking or creating it, such as a dangling symlink or a file as a parent).
2. Check the input with one `stat()`: it must exist, be a regular file, be non-empty, be readable, and have a supported extension (case-insensitive): `.png`, `.jpg`/`.jpeg`, `.tif`/`.tiff`, `.bmp`, `.pdf`. `input.media_type` is derived from the extension (content is not sniffed); `input.sha256` is the digest of the input bytes.
3. `engine.check_available()`; its version is recorded as `engine.version`.
4. `engine.recognize(input, output_dir / "raw", timeout_seconds=..., options=...)`. A non-empty `EngineResult.version` replaces the availability version.
5. Map the `EngineResult` to the run document (below), add the `score_not_normalized` warning on success, validate, and write.

Run failures do not raise; each produces a failed run document (`status: "failed"`, `score: null`) with the error code below. The run still writes `run.json` and returns a `PipelineResult`.

| Situation | `error.code` | Notes |
| --- | --- | --- |
| Input missing, not a file, empty, unreadable, or unsupported extension | `invalid_input` | Engine is not called. |
| `check_available()` reports unavailable | `engine_unavailable` | Message is the engine's actionable message. |
| `recognize()` raises `InvalidEngineOptionError` (including `UnknownEngineOptionError`) | `invalid_option` | Message names the engine and the rejected option(s). |
| `EngineResult.success` is false | `engine_failed` | Message is `EngineResult.error` (or a generic one naming the return code); `returncode` is copied. |
| Any other exception while checking or running the engine | `internal_error` | Message names the exception type; the traceback is logged with `logging`. If a failure was already recorded (for example `engine_failed` before the rest of the `EngineResult` is read), that error is kept and the exception becomes an `unexpected_error` warning. |
| The finished document cannot be serialized or fails validation (an adapter or pipeline bug) | original code, or `internal_error` | A reduced failed record is written instead, with `options` and `artifacts` empty. It keeps the input, `engine.version` and the warnings. A failed run keeps its original `error` and gets a `run_document_incomplete` warning; a run that had succeeded becomes `internal_error` and loses `score_not_normalized`. If that record still does not validate (for example a non-integer `returncode`), a minimal `internal_error` record with input provenance only is written. |

`engine_timeout` cannot be told apart yet: `EngineResult` has no timeout flag, so an adapter-reported timeout is recorded as `engine_failed` with the adapter's message. Distinguishing it needs an interface change in a separate issue.

## EngineResult to run document

- `options`: a copy of `EngineResult.options` (the effective options echoed by the adapter, defaults included). It is `{}` when `recognize()` did not return, i.e. for `invalid_input`, `engine_unavailable`, `invalid_option`, and unexpected exceptions; the requested options are not recorded there because they were never confirmed by the engine. An `invalid_option` message names the rejected option.
- `artifacts`: each engine artifact gets a snake_case kind and a path:
  - Kind: a key that already matches `^[a-z][a-z0-9_]*$` is kept. Other keys use the part before the first `:` (so `musicxml:sub/score.mvt2.musicxml` becomes `musicxml`), lowercased, with runs of other characters replaced by `_`; `artifact_` is prefixed when the result does not start with a letter, and an empty result becomes `artifact`. Kinds are assigned only to artifacts that are recorded, so a dropped artifact never pushes a recorded one to a numbered kind. Duplicates get `_2`, `_3`, ... in sorted order of the original keys, skipping kinds already in use. Example: `musicxml:sub/a.musicxml` and `musicxml:sub/b.musicxml` become `musicxml` and `musicxml_2`.
  - Path: relative paths are resolved against the current working directory, then made relative to the resolved run output directory and written with `/` separators (for example `raw/sub/a.musicxml`).
  - An artifact is **not recorded** when its path cannot be resolved (for example a symlink loop), when it is not inside the engine's `raw/` directory (or is that directory itself; `run.json` and other files the pipeline owns are therefore never recorded), does not exist, or its relative path breaks a `relativePosixPath` rule of the run schema (control character, backslash, and so on; the rules are read from the schema, so they cannot drift). Each such artifact adds an `artifact_not_recorded` warning naming the engine's key and the reason, but not the path, which may be an absolute local path. The run status is not changed: an adapter that writes outside its `output_dir` is a bug to fix in that adapter, and the warning makes it visible without discarding the rest of the result.
- `warnings`, in this order: one `engine_diagnostic` per non-blank `EngineResult.diagnostics` entry (message verbatim, order kept, also on failed runs; a non-string entry gives `engine_diagnostic_not_recorded` instead), then `artifact_not_recorded` warnings, then `score_not_normalized` on succeeded runs.
- `score` is always `null` (schema 0.1.0); the recognized notation exists only in the raw artifacts.
- `timing.duration_seconds` covers input checks through mapping (not writing the file); `timing.started_at` is a UTC RFC 3339 timestamp with milliseconds.

## Mock engine

`omr_lab.engines.mock.MockEngine` is a deterministic engine for tests and demos. It performs no recognition: it ignores the image and writes a fixed placeholder MusicXML (one part, one empty measure, no notes) and a log to `raw/`. It lives in the package rather than in `tests/` so that the CLI (issue #9) and end-to-end checks can run the whole pipeline without Java or models; its runs are always identifiable by `engine.name == "mock"`.

- `MockEngine(available=False)` simulates missing prerequisites (`engine_unavailable`).
- Option `outcome`: `"succeeded"` (default) or `"failed"` (an `engine_failed` run with return code 1 and the log artifact). Any other value raises `InvalidEngineOptionError`; unknown option names raise `UnknownEngineOptionError`.
- It is not exported from `omr_lab.engines`; import it from `omr_lab.engines.mock`.
