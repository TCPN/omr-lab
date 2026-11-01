# Benchmark Manifest and Report

`omr-lab benchmark` runs every sample of a manifest through the [pipeline](pipeline.md) with one engine and writes a report (issue #57, OMR-009). The rules for choosing data and comparing engines are in [benchmark-protocol.md](benchmark-protocol.md).

```bash
uv run omr-lab benchmark tests/fixtures/e2e/manifest.json \
  -o benchmarks/reports/generated/e2e-audiveris-0001 --engine audiveris
```

`--engine`, `--option` and `--timeout` work as for `omr-lab run` ([cli.md](cli.md)) and apply to every sample. From Python: `omr_lab.benchmark.load_manifest()` and `run_benchmark()`.

## Manifest

A UTF-8 JSON file validated against `src/omr_lab/schemas/benchmark-manifest-<version>.schema.json` for its `schema_version`. Both versions are read: 0.2.0 adds the optional `synthesis` field and is what `omr-lab synthesize` writes; 0.1.0 files such as [`tests/fixtures/e2e/manifest.json`](../tests/fixtures/e2e/manifest.json) stay valid.

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | `"0.2.0"` or `"0.1.0"` |
| `name`, `description` | no | Name of the set (copied into reports) and free text. |
| `samples` | yes | One or more samples, run and reported in this order. |

Each sample:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique in the manifest; lowercase letters, digits, `_`, `.`, `-` (up to 64, starting with a letter or digit, not ending with `.`, and not a Windows device name such as `con` or `nul`). Also the name of its run directory. |
| `image`, `image_sha256` | yes | Input image or PDF, relative to the manifest's directory (`/` separators, `..` allowed, no absolute path), and its SHA-256. |
| `ground_truth`, `ground_truth_sha256` | no | MusicXML ground truth and its SHA-256; a `ground_truth` path needs a non-null checksum. |
| `source`, `license`, `condition` | yes | Provenance, license (SPDX identifier where possible) and input condition (clean rendering, phone photo, skew, ...). |
| `work`, `renderer`, `notes` | no | Title, tools that rendered a synthetic image, free text. |
| `synthesis` | no (0.2.0 only) | How a synthetic image was generated: corpus name and seed, source and variant ids, sample seed and the resolved degradation parameters ([synthetic-corpus.md](synthetic-corpus.md#output)). |
| `excluded` | no | `{"reason": "..."}` keeps the sample in the manifest but does not run it; reports list it with the reason. |

Private or unlicensed scores stay outside Git (for example in `data/private/`, which is ignored); only their manifests may be committed if they contain no score content, and only when the data use is decided (issue #29).

## Output

```
<output_dir>/
  report.json      the report (schema below)
  report.md        short human-readable tables of the same report (runs, then correctness)
  runs/<id>/       one ordinary pipeline run directory per sample that ran (run.json, raw/, ...)
```

`<output_dir>` must be missing or empty. Put generated reports under `benchmarks/reports/generated/`, which Git ignores: a report is a generated record, never ground truth.

## Report

`report.json` is validated against `src/omr_lab/schemas/benchmark-report-0.3.0.schema.json` before it is written. Report 0.2.0 added the correctness fields below to 0.1.0, and 0.3.0 added `preprocessing` and `run.preprocessing`; `validate_benchmark_report` still accepts 0.1.0 and 0.2.0 reports, choosing the schema by `schema_version`.

- `generated_at`: UTC start time.
- `manifest`: file name (no directory), SHA-256 of the manifest bytes, `name`, `schema_version`.
- `environment`: omr-lab version, Python version and implementation, OS, OS release, machine architecture. No host name, user name or path.
- `engine`: name, `requested_options` and `timeout_seconds` as given. The version and effective options each run actually used are in its sample entry, because an engine can change between samples.
- `preprocessing`: `{"requested": [...]}`, the preprocessing steps applied to a copy of every sample image (`--preprocess`, [preprocessing.md](preprocessing.md)), or `null` when none. The manifest's images are not modified, so `image_sha256` still describes the original.
- `samples`: one entry per manifest sample, in manifest order, with the manifest's `image`, `image_sha256`, `ground_truth`, `source`, `license`, `condition`, and:
  - `status`: `succeeded` or `failed` (the run's status), `excluded` (from the manifest; `exclusion_reason` gives why) or `not_run` (`problem` gives why).
  - `problem`: for `not_run`, a `code` and `message`. Before a run, the image and the ground truth are read and their SHA-256 compared with the manifest: `image_unreadable` (missing, not a regular file, or not readable), `image_checksum_mismatch`, `ground_truth_unreadable`, `ground_truth_checksum_mismatch`. `run_not_started` is a run the pipeline refused to start (for example an image file name the run document cannot store); local paths in its message are replaced by `<output_dir>`, `<input_dir>` (the image's directory) and `~`, as in run documents.
  - `run`: for samples that ran, the run document path relative to the report, its status and `error_code`, `engine_version`, effective `options`, `duration_seconds`, `warning_codes` (number of warnings per code), and `preprocessing`: for each step whether it changed the image (`{"page": true, "deskew": false}`), or `null` when the run did no preprocessing. The run document has the details, such as detected page corners and angles; report.md adds a "Preprocessing applied" column.
  - `correctness`: for a sample with a `ground_truth` whose run succeeded or failed, how well the run's score matches the ground truth: key and time signatures, measures, notes (pitch, duration, both) and rests with precision, recall and F1, chord symbols, and lyric error rates per verse. A failed run, or one without a score, is compared as a missing prediction. Definitions and a worked example are in [metrics.md](metrics.md). `null` for other samples.
  - `correctness_problem`: why a sample that has a ground truth and ran could not be measured normally; `null` otherwise. `ground_truth_invalid`: not loadable MusicXML (the ground truth's directory is written `<input_dir>` in the message); `correctness` is `null`. `comparison_too_large`: too large to align; `correctness` counts everything as missed or extra ([metrics.md](metrics.md#which-samples-are-measured)). `comparison_failed`: an unexpected error while measuring (it is also logged); `correctness` is computed as a missing prediction (`null` if the ground truth itself fails), and the report is still written.
  - `ground_truth_warning_codes`: the number of warnings per code from loading the ground truth and converting it to a score, when it was converted; `null` otherwise. The converter drops or nulls what it cannot read (for example a note without a duration, or a part without an id), so warnings here mean low scores may not be the engine's fault. report.md lists them under the correctness table.
- `summary`: counts of samples, `excluded`, `not_run`, `attempted` (= `succeeded` + `failed`), `success_rate` (succeeded / attempted, rounded down to 3 decimals, `null` when nothing ran), distinct `engine_versions`, failed runs per `error_codes`, the summed `run_duration_seconds`, and `correctness`: the counts of all measured samples added up, with rates computed from the sums (`null` when no sample was measured).

Two runs of the same manifest and engine on the same machine give the same report except `generated_at` and the durations (a unit test checks this).

A failed or timed-out engine run does not stop the benchmark: each failure is recorded in its sample. Ctrl-C, or a run record that cannot be written (a full disk), stops it without writing a report; the runs already finished stay in `runs/`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Report written; every sample that was not excluded ran and succeeded (also when every sample is excluded, so check `summary.attempted` when gating on a benchmark). |
| 1 | Report written; at least one sample failed or was not run. |
| 2 | Invalid command line, including an `--option` value the report cannot store (not valid UTF-8). Nothing is written. |
| 3 | The manifest cannot be read, is not JSON, breaks the schema, or repeats a sample id. Nothing is written. |
| 4 | The engine is not available (`doctor` reports why); checked once before anything is written. Nothing is written. |
| 6 | The output directory cannot be used, or the report could not be written (including the internal error of a report that fails its own schema; the runs stay in `runs/`). |
| 130 | Interrupted with Ctrl-C; no report. |
