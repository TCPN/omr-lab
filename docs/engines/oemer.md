# oemer Adapter

`omr_lab.engines.OemerEngine` (`src/omr_lab/engines/oemer.py`) runs a separately installed [oemer](https://github.com/BreezeWhite/oemer) on one image and collects the MusicXML it writes. It implements the Engine protocol from [engine-interface.md](../engine-interface.md). Engine name: `oemer` (`omr-lab run ... --engine oemer`).

## What oemer is

oemer is a deep-learning OMR engine for photos and scans of printed sheet music:

1. Two UNet segmentation models separate staff lines and symbols, then noteheads, clefs, accidentals, stems and rests.
2. Rule-based code finds staffs, groups noteheads into notes and chords, reads rhythm from stems, beams and flags, and builds the MusicXML.

The models are ONNX files run with ONNX Runtime (oemer can also use TensorFlow with `--use-tf`; the adapter does not). oemer does not recognize chord symbols, lyrics or text. Its segmentation models are also the base of homr's first stage ([homr.md](homr.md)).

## License

oemer is licensed under **MIT** (its `LICENSE`; PyPI `oemer` 0.1.8). MIT allows importing it, but omr-lab still runs it as an external program, like Audiveris and homr: oemer requires old versions of numpy, OpenCV and ONNX Runtime (see [Setup](#setup)) that conflict with the omr-lab environment, and its models are about 210 MB. The models are downloaded by oemer itself from the oemer GitHub releases (`checkpoints` release); their license is not stated separately from the repository's.

## Setup

oemer 0.1.8 (released in 2023) declares its dependencies without upper bounds, so a plain `pip install oemer` today installs versions it does not work with. Install it in its own **Python 3.11** environment with these pins (checked on Linux, 2026-11-24):

```bash
uv venv -p 3.11 ~/.local/opt/oemer
uv pip install --python ~/.local/opt/oemer/bin/python oemer \
  'onnxruntime-gpu<1.20' 'opencv-python-headless<5' 'numpy<2'
```

| Pin | Without it |
| --- | --- |
| `onnxruntime-gpu<1.20` (1.19.2 worked) | newer ONNX Runtime refuses oemer's model: a `ConvTranspose` node fails with a pads error |
| `opencv-python-headless<5` (4.11 worked) | OpenCV 5 returns `HoughLinesP` lines in another shape, and oemer stops with `IndexError` in `bbox.find_lines` |
| `numpy<2` (1.26.4 worked) | the pinned ONNX Runtime and OpenCV builds need numpy 1 |

Notes:

- oemer depends on `onnxruntime-gpu`, not `onnxruntime`. Without a matching CUDA 12 and cuDNN 9 runtime it prints `Failed to load library libonnxruntime_providers_cuda.so` and similar errors at start-up and then runs on the CPU; the errors are harmless. GPU use was not tested (the RTX 5060 on the owner's PC is newer than ONNX Runtime 1.19).
- `onnxruntime-gpu` has no macOS wheels, so this recipe works on Linux (including WSL) and Windows only.

Then:

1. Run oemer once on any image to download its models (about 210 MB into its own `site-packages/oemer/checkpoints`). Without this step, the first recognition downloads them and needs network access.
2. Make the command discoverable. The adapter looks, in order, at:
   1. the `executable` argument of `OemerEngine(...)`;
   2. the `OEMER_EXE` environment variable, the **full path of the `oemer` command** (for example `~/.local/opt/oemer/bin/oemer`);
   3. `oemer` on `PATH`.

   If an explicit path or `OEMER_EXE` is set but wrong, the adapter reports that instead of falling back to `PATH`. Relative paths are made absolute against the current directory.
3. Check: `omr-lab doctor` or `OemerEngine().check_available()`.

The version check works as for homr ([homr.md](homr.md#version-check)): oemer has no `--version`, so the adapter reads the `oemer` package version with the Python interpreter next to the command, and falls back to `oemer --help` when it cannot. The result is cached per `OemerEngine` instance.

When oemer is missing or unusable, `check_available()` returns `available=False` and `recognize()` returns `EngineResult(success=False, error=...)` with the same message; neither raises.

## Invocation

```text
<oemer> <output_dir>/oemer-XXXXXXXX/input.<suffix> -o <output_dir>/oemer-XXXXXXXX/input.musicxml [--without-deskew]
```

- Each `recognize()` call creates a new run directory `<output_dir>/oemer-XXXXXXXX` (with `tempfile.mkdtemp`), copies the input there as `input.png`, `input.jpg` or `input.jpeg` (the suffix in lower case), and runs oemer on the copy. The source file and its directory are never written, and concurrent runs sharing `output_dir` do not see each other's files.
- The output is given as an explicit `.musicxml` file, which oemer writes exactly there (a directory would make oemer derive the name from the input name, and it strips only `.png` and `.jpg`).
- The argument list goes through `run_command` (no shell) with the caller's `timeout_seconds`; on timeout the whole process tree is killed and the result has `timed_out=True` (recorded as `engine_timeout`). oemer is slow on a CPU (minutes per page, see [Evidence](#evidence)), so give it a long timeout, for example `--timeout 1800`.
- Accepted inputs: `.png`, `.jpg`, `.jpeg` (any case). oemer cannot read PDF; anything else fails before oemer starts, with a message to convert the input to PNG.
- Options:

  | Option | Default | Effect |
  | --- | --- | --- |
  | `deskew` (bool) | `true` | `false` passes `--without-deskew`, skipping oemer's deskewing step for images known to be straight (for example rendered or flatbed-scanned pages). |

  Any other option raises `UnknownEngineOptionError`, and a non-bool `deskew` raises `InvalidEngineOptionError`, before oemer runs.

## Results and artifacts

- `artifacts["musicxml"]` is `input.musicxml` in the run directory (uncompressed MusicXML). The run directory also keeps the input copy and oemer's `input_teaser.png` (a picture of the recognized symbols); they are not reported as artifacts.
- `success` is `True` only if oemer exited with code 0, did not time out, **and** wrote `input.musicxml`. oemer reports a failure as an uncaught Python exception (exit code 1); the error message then includes the last stderr line, normally the exception.
- `diagnostics` contain the argument list, the return code or launch/timeout error, the last 20 non-empty lines of stdout and stderr (each shortened to 300 characters). `run_command` turns carriage returns into line breaks, so each update of oemer's progress counter counts as a line; on a failure the tail still ends with the Python traceback, and the run directory.
- A successful result only means MusicXML was produced, not that the music was recognized correctly.

## Evidence

oemer 0.1.8 with the pins above (Python 3.11, Linux container, 4 CPU cores, no GPU), 2026-11-24:

| Input | Result |
| --- | --- |
| `tests/fixtures/e2e/twinkle.png` (end-to-end sample) | succeeded in about 4.5 minutes (about 14 CPU-minutes); 22 of 42 notes matched in pitch and duration (32 notes recognized: recall 0.52, precision 0.69); first clef correct, time signature wrong; 0 of 22 chord symbols and 0 of 42 lyric syllables (oemer does not read them) |

For comparison, homr 0.7.1 matched 42 of 42 notes on the same sample in about 8 seconds ([homr.md](homr.md#evidence)).

The integration test `tests/integration/test_oemer_integration.py` (marker `integration`) checks the version and runs the sample; it prints the correctness report without asserting it:

```bash
OEMER_EXE=~/.local/opt/oemer/bin/oemer uv run pytest -m integration -s tests/integration/test_oemer_integration.py
```

A comparison with Audiveris and homr on the synthetic corpus is issue #75 (OMR-012).
