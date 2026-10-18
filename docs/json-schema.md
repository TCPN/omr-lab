# Run Document JSON Schema (v0.1.0)

Each pipeline run produces one **run document**: a JSON object recording what was run, how, and what came out. It records only facts supported by the pipeline and engine output. A valid run document is not proof that the music was recognized correctly.

- Schema (source of truth): [`src/omr_lab/schemas/run-0.1.0.schema.json`](../src/omr_lab/schemas/run-0.1.0.schema.json), JSON Schema draft 2020-12, `$id` `urn:omr-lab:schema:run:0.1.0`. It ships inside the `omr_lab` package.
- Validation: `omr_lab.schemas.validate_run_document(document, *, version=None)` raises `RunDocumentValidationError` (a `ValueError`) whose `errors` list holds one `"<json path>: <message>"` entry per violation, ordered by location (array indexes numerically). Artifact path errors name the broken rule instead of printing a regular expression. `omr_lab.schemas.load_run_schema(version=CURRENT_RUN_SCHEMA_VERSION)` returns a schema as a dict.
- Examples: `tests/fixtures/run-documents/valid/` and `tests/fixtures/run-documents/invalid/`.

## Example: succeeded run

```json
{
  "schema_version": "0.1.0",
  "status": "succeeded",
  "input": {"filename": "score.png", "media_type": "image/png"},
  "engine": {"name": "audiveris", "version": null},
  "options": {},
  "artifacts": {"musicxml": "result.musicxml"},
  "timing": {"duration_seconds": 12.5},
  "score": null,
  "warnings": [],
  "error": null
}
```

`score: null` means no normalized musical content is available; it is not an empty or recognized score. The recognized notation is only in the raw `musicxml` artifact until the score content model (issue #19) exists.

## Example: failed run

```json
{
  "schema_version": "0.1.0",
  "status": "failed",
  "input": {"filename": "score.png", "media_type": "image/png"},
  "engine": {"name": "audiveris", "version": "5.4"},
  "options": {},
  "artifacts": {"engine_log": "raw/audiveris.log"},
  "timing": {"duration_seconds": 41.0},
  "score": null,
  "warnings": [],
  "error": {"code": "engine_failed", "message": "Audiveris exited with status 1; see raw/audiveris.log.", "returncode": 1}
}
```

## Fields

All top-level fields are **required**; unknown top-level fields are rejected. "Nullable" means the key must be present but may be `null`.

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | `"0.1.0"` | Exact version of this contract. |
| `status` | `"succeeded"` \| `"failed"` | Pipeline outcome, not a correctness claim. |
| `input` | object | See below. |
| `engine` | object | Engine provenance. See below. |
| `options` | object | Effective engine options as echoed in `EngineResult.options`; `{}` when none or when the engine did not return a result. Values are any JSON. See [pipeline.md](pipeline.md). |
| `artifacts` | object | Raw artifacts by kind (e.g. `musicxml`, `engine_log`, `engine_diagnostics`); may be `{}`. |
| `timing` | object | See below. |
| `score` | `null` | Normalized musical content. Always `null` in 0.1.0 (placeholder for issue #19). |
| `warnings` | array of objects | Non-fatal problems in order of occurrence; may be `[]`. |
| `error` | object \| `null` | Object when `status` is `failed`; `null` when `status` is `succeeded`. |

### `input`
- `filename` (required, string): base name only; `/` and `\` are rejected so local directory paths are not recorded.
- `media_type` (required, nullable string): IANA media type such as `image/png` or `application/pdf`; `null` when not determined.
- `sha256` (optional, nullable string): lowercase hex SHA-256 of the input bytes.

### `engine`
- `name` (required, non-empty string): `Engine.name` of the selected adapter.
- `version` (required, nullable string): version reported by the engine; `null` when unknown. Never guessed.

### `artifacts`
Keys are snake_case artifact kinds. Values are POSIX-style paths relative to the run output directory: absolute paths, Windows drive prefixes, backslashes, control characters (including newlines), and `..` segments are rejected. Raw engine artifacts stay in these files and are never merged into `score`.

### `timing`
- `duration_seconds` (required, number ≥ 0): total elapsed wall-clock time.
- `started_at` (optional, nullable string): RFC 3339 timestamp. `format: date-time` is an annotation and is not enforced by the validator.

### `warnings[]` and `error`
Both use a snake_case `code` (pattern `^[a-z][a-z0-9_]*$`) and a non-empty, human-readable `message`; no other warning fields are allowed. `error` may also carry `returncode` (nullable integer) when the failure came from an external process.

Codes are not an enum in 0.1.0. Codes in use so far:

| Code | Kind | Meaning |
| --- | --- | --- |
| `invalid_input` | error | Input path missing, unreadable, or unsupported type. |
| `engine_unavailable` | error | Engine prerequisites not found (`check_available()` failed). |
| `engine_failed` | error | Engine process exited non-zero or the adapter reported failure. |
| `invalid_option` | error | The engine rejected an option name or value (`InvalidEngineOptionError`, including `UnknownEngineOptionError`). |
| `engine_timeout` | error | Engine process exceeded its timeout. |
| `musicxml_invalid` | error | Engine output could not be parsed or validated as MusicXML. |
| `interrupted` | error | The run was stopped by a `KeyboardInterrupt` (Ctrl-C) before it finished. |
| `internal_error` | error | Unexpected pipeline error; the message should say what failed. |
| `score_not_normalized` | warning | Musical content exists only in raw artifacts. |
| `artifact_not_recorded` | warning | An engine artifact was left out of `artifacts` (outside the engine's `raw/` directory, missing, not a regular file, or an unrepresentable path). |
| `engine_diagnostic_not_recorded` | warning | An `EngineResult.diagnostics` entry was not a string, or the diagnostics file could not be written, so diagnostics were left out. |
| `unexpected_error` | warning | An unexpected exception after a failure was already recorded; the original `error` is kept. |
| `run_document_incomplete` | warning | The full run document did not validate, so `options` and `artifacts` were left out; the original `error` is kept. |

The pipeline's use of these codes is described in [pipeline.md](pipeline.md); `omr_lab.pipeline.RunErrorCode` and `RunWarningCode` list the same codes, and a unit test keeps them in step with this table. `engine_timeout` is reserved: `EngineResult` cannot report a timeout separately yet, so the pipeline records adapter timeouts as `engine_failed` (issue #48). `musicxml_invalid` is reserved for MusicXML validation in the pipeline.

Messages must not contain absolute local paths: they name the user and private folders, and they are wrong once the run directory is moved. The pipeline replaces the run output directory, the input file's directory and the home directory with `<output_dir>`, `<input_dir>` and `~` in every message and in the diagnostics file.

## Rules
- Do not fabricate coordinates, confidence values, or musical properties absent from engine output. Unknown values are `null` or absent, never guessed.
- `score` is `null` whenever normalized content is unavailable, including every failed run.
- Raw engine artifacts are kept separate from normalized content.

## Versioning
`schema_version` is fixed per schema file. Any change to the contract adds a new schema file with a new version (for example, `run-0.2.0.schema.json` when issue #19 replaces the `score` placeholder with the score content model) and updates this document. Within 0.x, any change may be breaking.

- Old schema files stay in the package. `SUPPORTED_RUN_SCHEMA_VERSIONS` lists every version that can still be validated; `CURRENT_RUN_SCHEMA_VERSION` is the one writers emit.
- Writers always emit the current version.
- Readers validate according to the document's own `schema_version`: `validate_run_document(document)` picks that schema when the version is supported. A missing or unknown `schema_version` is validated against the current schema and reported as a violation at `$.schema_version` (or `$` when missing). Passing an unsupported `version=` explicitly raises `ValueError`.
