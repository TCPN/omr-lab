# homr Adapter

`omr_lab.engines.HomrEngine` (`src/omr_lab/engines/homr.py`) runs a separately installed [homr](https://github.com/liebharc/homr) on one image or PDF and collects the MusicXML it writes. It implements the Engine protocol from [engine-interface.md](../engine-interface.md). Engine name: `homr` (`omr-lab run ... --engine homr`).

## What homr is

homr is a deep-learning OMR engine aimed at camera pictures of printed sheet music:

1. UNet segmentation models (adapted from [oemer](https://github.com/BreezeWhite/oemer)) find staff lines, noteheads, clefs and other symbols.
2. Staffs are built from the segmentation and each staff is straightened (dewarped).
3. A transformer model (based on Polyphonic-TrOMR) reads each staff as a sequence of symbols, which homr writes as MusicXML.

The models are ONNX files run with ONNX Runtime, on the CPU or on an NVIDIA (CUDA) or AMD (ROCm) GPU, depending on how homr was installed. homr also uses an OCR model to read the title. It does not recognize chord symbols or lyrics.

## License

homr is licensed under **AGPL-3.0** (its `LICENSE`; PyPI `homr` 0.7.1). Following [ADR 0002](../decisions/0002-project-license-and-agpl-engines.md), omr-lab only runs an unmodified, separately installed `homr` command as an external process; it never imports homr, adds it as a dependency, or bundles it. Distributing homr (for example in a Docker image, OMR-014) or running it in a hosted service needs the license review of ADR 0002 rule 3 first. The models are downloaded by homr itself from the homr GitHub releases.

## Setup

1. Install homr in its own environment (Python 3.11 or 3.12), for example with uv:
   - CPU: `uv tool install 'homr[cpu]'`
   - NVIDIA GPU: `uv tool install 'homr[cuda]'` (needs a CUDA runtime that matches homr's ONNX Runtime build)

   `pip install 'homr[cpu]'` into a dedicated virtual environment works too. Do not install homr into the omr-lab environment.
2. Download the models once: `homr --init` (about 90 MB from the homr GitHub releases). Without this step, the first recognition downloads them and needs network access.
3. Make the command discoverable. The adapter looks, in order, at:
   1. the `executable` argument of `HomrEngine(...)`;
   2. the `HOMR_EXE` environment variable, the **full path of the `homr` command** (for example `~/.local/bin/homr` or `<venv>/bin/homr`);
   3. `homr` on `PATH`.

   If an explicit path or `HOMR_EXE` is set but wrong, the adapter reports that instead of falling back to `PATH`. Relative paths are made absolute against the current directory.
4. Check: `omr-lab doctor` or `HomrEngine().check_available()`.

### Version check

homr has no `--version` option. The adapter reads the installed version from the package metadata with the Python interpreter of homr's own environment: `python -I -c "import importlib.metadata as m; print(m.version('homr'))"`, using `python`/`python3` (`python.exe` on Windows) next to the `homr` command, or next to the file a symlinked command points to (a `uv tool` shim on Linux and macOS). If no such interpreter is found, or it does not print a version, the adapter runs `homr --help` instead: when that succeeds, homr is reported available without a version (the message says how to record it: point `HOMR_EXE` at the command inside the environment); when it fails, homr is reported unavailable with the exit code and last stderr line. The result, including a failure, is cached per `HomrEngine` instance; create a new instance to re-check.

When homr is missing or unusable, `check_available()` returns `available=False` and `recognize()` returns `EngineResult(success=False, error=...)` with the same message; neither raises.

## Invocation

```text
<homr> <output_dir>/homr-XXXXXXXX/input.<suffix>
```

- homr writes its output next to its input. So each `recognize()` call creates a new run directory `<output_dir>/homr-XXXXXXXX` (with `tempfile.mkdtemp`), copies the input there as `input.png`, `input.jpg`, `input.jpeg` or `input.pdf` (the suffix in lower case), and runs homr on the copy. The source file and its directory are never written, and concurrent runs sharing `output_dir` do not see each other's files.
- The argument list goes through `run_command` (no shell) with the caller's `timeout_seconds`; on timeout the whole process tree is killed and the result has `timed_out=True` (recorded as `engine_timeout`).
- Accepted inputs: `.png`, `.jpg`, `.jpeg`, `.pdf` (any case), the file types homr 0.7.1 handles for a single file. Anything else fails before homr starts, with a message to convert the input to PNG. homr renders a PDF to PNG pages (300 dpi) next to the copy and merges their results into one MusicXML.
- Options: none. CPU or GPU is chosen when homr is installed, and homr's other flags (`--output-large-page`, `--output-tempo`, ...) only change layout hints in the MusicXML. Any option raises `UnknownEngineOptionError` before homr runs.

## Results and artifacts

- `artifacts["musicxml"]` is `input.musicxml` in the run directory, written by homr (uncompressed MusicXML). The run directory also keeps the input copy, homr's `input_teaser.png` (a picture of the detected staffs) and, for a PDF, the rendered pages; they are not reported as artifacts.
- `success` is `True` only if homr exited with code 0, did not time out, **and** wrote `input.musicxml`. homr 0.7.1 exits with 1 when processing fails (for example "No noteheads or clefs found" on an empty page) and 2 for an unsupported or missing file; the error message then includes homr's last stderr line. A MusicXML written before a failure is still listed for inspection.
- `diagnostics` contain the argument list, the return code or launch/timeout error, the last 20 non-empty lines of stdout and stderr (each shortened to 300 characters: homr prints its whole recognized token list on one line), and the run directory.
- A successful result only means MusicXML was produced, not that the music was recognized correctly.

## Evidence

homr 0.7.1 (`homr[cpu]`, Python 3.12, Linux container, CPU only), 2026-11-12:

| Input | Result |
| --- | --- |
| `tests/fixtures/e2e/twinkle.png` (end-to-end sample) | succeeded in about 8 s; 42 of 42 notes matched in pitch and duration, first clef correct, the short last line (one measure) kept; 0 of 22 chord symbols and 0 of 42 lyric syllables (homr does not read them) |
| a blank white PNG | exit 1, "No noteheads or clefs found" |
| a text file named `.png` | exit 2, "The file format is not supported" |

The integration test `tests/integration/test_homr_integration.py` (marker `integration`) checks the version and runs the sample; it prints the correctness report without asserting it:

```bash
HOMR_EXE=~/.local/bin/homr uv run pytest -m integration -s tests/integration/test_homr_integration.py
```

A comparison with Audiveris and oemer on the synthetic corpus is issue #75 (OMR-012).
