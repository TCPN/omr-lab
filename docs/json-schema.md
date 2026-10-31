# Run Document JSON Schema (v0.3.0)

Each pipeline run produces one **run document**: a JSON object recording what was run, how, and what came out. It records only facts supported by the pipeline and engine output. A valid run document is not proof that the music was recognized correctly.

- Schema (source of truth): [`src/omr_lab/schemas/run-0.3.0.schema.json`](../src/omr_lab/schemas/run-0.3.0.schema.json), JSON Schema draft 2020-12, `$id` `urn:omr-lab:schema:run:0.3.0`. It ships inside the `omr_lab` package together with the previous versions, `run-0.1.0.schema.json` and `run-0.2.0.schema.json`.
- Validation: `omr_lab.schemas.validate_run_document(document, *, version=None)` raises `RunDocumentValidationError` (a `ValueError`) whose `errors` list holds one `"<json path>: <message>"` entry per violation, ordered by location (array indexes numerically). Artifact path errors name the broken rule instead of printing a regular expression. `omr_lab.schemas.load_run_schema(version=CURRENT_RUN_SCHEMA_VERSION)` returns a schema as a dict.
- Examples: `tests/fixtures/run-documents/valid/` and `tests/fixtures/run-documents/invalid/`.

## Example: succeeded run

```json
{
  "schema_version": "0.3.0",
  "status": "succeeded",
  "input": {"filename": "score.png", "media_type": "image/png"},
  "engine": {"name": "audiveris", "version": null},
  "options": {"min_staff_length": null},
  "artifacts": {"musicxml": "result.musicxml"},
  "preprocessing": null,
  "timing": {"duration_seconds": 12.5},
  "score": null,
  "warnings": [],
  "error": null
}
```

`score: null` means no normalized musical content is available; it is not an empty or recognized score. The pipeline does not fill `score` yet, so the recognized notation is only in the raw `musicxml` artifact. A document with a score object is `tests/fixtures/run-documents/valid/succeeded-with-score.json`.

## Example: failed run

```json
{
  "schema_version": "0.3.0",
  "status": "failed",
  "input": {"filename": "score.png", "media_type": "image/png"},
  "engine": {"name": "audiveris", "version": "5.4"},
  "options": {"min_staff_length": null},
  "artifacts": {"engine_log": "raw/audiveris.log"},
  "preprocessing": null,
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
| `schema_version` | `"0.3.0"` | Exact version of this contract. |
| `status` | `"succeeded"` \| `"failed"` | Pipeline outcome, not a correctness claim. |
| `input` | object | See below. |
| `engine` | object | Engine provenance. See below. |
| `options` | object | Effective engine options as echoed in `EngineResult.options`; `{}` when none or when the engine did not return a result. Values are any JSON. See [pipeline.md](pipeline.md). |
| `artifacts` | object | Raw artifacts by kind (e.g. `musicxml`, `engine_log`, `engine_diagnostics`, `preprocessed_image`); may be `{}`. |
| `preprocessing` | object \| `null` | Image preprocessing applied to a copy of the input before recognition; `null` when none was requested or the run stopped before it. See below. |
| `timing` | object | See below. |
| `score` | object \| `null` | Normalized musical content in the score model ([score-model.md](score-model.md)); `null` when none is available. |
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
Keys are snake_case artifact kinds. Values are POSIX-style paths relative to the run output directory: absolute paths, Windows drive prefixes, backslashes, control characters (including newlines), and `..` segments are rejected. Raw engine artifacts stay in these files and are never merged into `score`. Artifacts are engine output under `raw/`, except kinds the pipeline writes itself outside `raw/`: `engine_diagnostics` (`engine_diagnostics.log`, the engine's diagnostics with local paths redacted) and `preprocessed_image` (`preprocessed/input.png`, the preprocessed copy of the input that the engine read). Tools that archive or re-hash raw engine output should skip pipeline-owned kinds.

### `preprocessing`
Present as an object when preprocessing steps were requested ([preprocessing.md](preprocessing.md)). `input` still describes the original file, which is never modified; the engine read `output`.

- `requested` (array of step names, at least one): the steps as requested, in order.
- `orientation_applied` (nullable boolean): whether the input's EXIF orientation tag turned or mirrored the image before the steps ran (the output PNG has no tag, so the engine sees it upright); `null` when preprocessing failed before the image was read.
- `steps` (array): one record per step that ran, in order; every requested step on success, `[]` when preprocessing failed (`error.code` is `preprocessing_failed`). Each record has:
  - `name`: the step name;
  - `params` (object of numbers): the step's effective parameters;
  - `applied` (boolean): whether the step changed the image. A step that finds nothing to correct, or no reliable evidence, leaves the image unchanged and says why in `message`;
  - `result` (object): what the step measured, such as the detected page corners in pixels of the step's input image or the rotation angle; measurements, not ground truth;
  - `message` (nullable string) and `duration_seconds` (number ≥ 0).
- `output` (nullable relative path): the image given to the engine, also recorded as the `preprocessed_image` artifact; `null` when preprocessing failed.
- `output_sha256` (nullable string): SHA-256 of `output`.
- `duration_seconds` (number ≥ 0): time spent reading, transforming and writing the image.

Example: `tests/fixtures/run-documents/valid/succeeded-preprocessed.json`.

### `timing`
- `duration_seconds` (required, number ≥ 0): total elapsed wall-clock time.
- `started_at` (optional, nullable string): RFC 3339 timestamp. `format: date-time` is an annotation and is not enforced by the validator.

### `warnings[]` and `error`
Both use a snake_case `code` (pattern `^[a-z][a-z0-9_]*$`) and a non-empty, human-readable `message`; no other warning fields are allowed. `error` may also carry `returncode` (nullable integer) when the failure came from an external process.

Codes are not an enum in the schema. Codes in use so far:

| Code | Kind | Meaning |
| --- | --- | --- |
| `invalid_input` | error | Input path missing, unreadable, or unsupported type. |
| `engine_unavailable` | error | Engine prerequisites not found (`check_available()` failed). |
| `engine_failed` | error | Engine process exited non-zero or the adapter reported failure. |
| `invalid_option` | error | The engine rejected an option name or value (`InvalidEngineOptionError`, including `UnknownEngineOptionError`). |
| `engine_timeout` | error | Engine process exceeded its timeout. |
| `musicxml_invalid` | error | The engine's primary MusicXML artifact could not be loaded (missing, unreadable, too large, not valid MusicXML, or unsupported); the message names the loader's own code. |
| `preprocessing_failed` | error | The requested preprocessing could not run (a PDF input, an image OpenCV cannot decode, the `preprocess` extra not installed, or an OpenCV error); the engine was not called. |
| `interrupted` | error | The run was stopped by a `KeyboardInterrupt` (Ctrl-C) before it finished. |
| `internal_error` | error | Unexpected pipeline error; the message should say what failed. |
| `score_not_normalized` | warning | Musical content exists only in raw artifacts. Written by pipeline versions before issue #50; no longer produced. |
| `artifact_not_recorded` | warning | An engine artifact was left out of `artifacts` (outside the engine's `raw/` directory, missing, not a regular file, or an unrepresentable path). |
| `engine_diagnostic_not_recorded` | warning | An `EngineResult.diagnostics` entry was not a string, or the diagnostics file could not be written, so diagnostics were left out. |
| `unexpected_error` | warning | An unexpected exception after a failure was already recorded; the original `error` is kept. |
| `run_document_incomplete` | warning | The full run document did not validate, so `options` and the engine's `artifacts` were left out; the original `error` is kept. |
| `score_unsupported_element` | warning | The source uses a construct the score model cannot hold; it is missing from `score` ([score-model.md](score-model.md#warnings)). |
| `score_invalid_value` | warning | A source value was malformed or out of range; the field is `null` or the element is missing from `score`. |
| `score_divisions_missing` | warning | A duration or `<offset>` appeared before any `<divisions>`; that value is `null`, and for a duration that moves the position, so are later offsets in the measure. |
| `score_timing_inconsistent` | warning | A `<backup>` moved before the start of its measure, or the position became too precise to record (later offsets are `null`); or an `<offset>` pointed before the start of its measure (only its own offset is `null`). |
| `musicxml_unknown_element` | warning | The engine's MusicXML has a top-level element outside the MusicXML 4.0 schema. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_unresolved_entity` | warning | The engine's MusicXML uses an entity it does not define; that text is left out. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_processing_instructions_dropped` | warning | The engine's MusicXML contains processing instructions; they are not used. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_xlink_namespace_assumed` | warning | The engine's MusicXML uses `xlink:` without declaring it; the standard namespace was assumed. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_part_missing` | warning | A `<score-part>` in the engine's MusicXML has no matching `<part>`. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_part_undeclared` | warning | A `<part>` in the engine's MusicXML is not declared in `<part-list>`. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_part_without_id` | warning | A `<part>` or `<score-part>` in the engine's MusicXML has no `id`. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_duplicate_part` | warning | A part id in the engine's MusicXML is declared or used more than once (the first one is used). See [musicxml.md](musicxml.md#warnings). |
| `musicxml_mxl_container_missing` | warning | The engine's `.mxl` has no `META-INF/container.xml`; a fallback entry was read. See [musicxml.md](musicxml.md#warnings). |
| `musicxml_mxl_container_invalid` | warning | The engine's `.mxl` has an unusable `container.xml`; a fallback entry was read. See [musicxml.md](musicxml.md#warnings). |

The pipeline's use of these codes is described in [pipeline.md](pipeline.md); `omr_lab.schemas.RunErrorCode` and `RunWarningCode` (re-exported by `omr_lab.pipeline`) list the same codes, and a unit test keeps them in step with this table. `engine_timeout` is reserved: `EngineResult` cannot report a timeout separately yet, so the pipeline records adapter timeouts as `engine_failed` (issue #48). The `musicxml_*` warnings are the MusicXML loader's warnings, copied with their own codes and messages.

Messages must not contain absolute local paths: they name the user and private folders, and they are wrong once the run directory is moved. The pipeline replaces the run output directory, the input file's directory and the home directory with `<output_dir>`, `<input_dir>` and `~` in every message and in the diagnostics file.

## Rules
- Do not fabricate coordinates, confidence values, or musical properties absent from engine output. Unknown values are `null` or absent, never guessed.
- `score` is `null` whenever normalized content is unavailable, including every failed run.
- Raw engine artifacts are kept separate from normalized content.

## Versioning
`schema_version` is fixed per schema file. Any change to the contract adds a new schema file with a new version and updates this document. 0.2.0 replaced the `score` placeholder of 0.1.0 (always `null`) with the score model (issue #19). 0.3.0 added the required `preprocessing` field (issue #58); everything else is unchanged. Within 0.x, any change may be breaking.

- Old schema files stay in the package. `SUPPORTED_RUN_SCHEMA_VERSIONS` lists every version that can still be validated; `CURRENT_RUN_SCHEMA_VERSION` is the one writers emit.
- Writers always emit the current version.
- Readers validate according to the document's own `schema_version`: `validate_run_document(document)` picks that schema when the version is supported. A missing or unknown `schema_version` is validated against the current schema and reported as a violation at `$.schema_version` (or `$` when missing). Passing an unsupported `version=` explicitly raises `ValueError`.
