# Audiveris Adapter

`omr_lab.engines.AudiverisEngine` (`src/omr_lab/engines/audiveris.py`) runs a separately installed Audiveris in batch mode and collects the MusicXML it writes. It implements the Engine protocol from [engine-interface.md](../engine-interface.md).

## Setup

1. Install Audiveris from the [releases page](https://github.com/Audiveris/audiveris/releases) following the [binary install guide](https://audiveris.github.io/audiveris/_pages/tutorials/install/binaries/). The installers bundle their own Java runtime.
   - Linux (Ubuntu 22.04/24.04): `sudo apt install /path/to/Audiveris-<version>-ubuntu<osversion>-x86_64.deb`; the launcher is `/opt/audiveris/bin/Audiveris`.
   - Windows: `.msi` installer (or `winget install Audiveris`); the default launcher is `C:\Program Files\Audiveris\Audiveris.exe`.
   - macOS: `.dmg` with `Audiveris.app`. The handbook does not document the command-line launcher path inside the app bundle; set `AUDIVERIS_EXE` yourself.
   - Flatpak (`flatpak run org.audiveris.audiveris`) is a command plus arguments, not a single executable, so it is not detected. Point `AUDIVERIS_EXE` at a small wrapper script if you use it.
2. Make the launcher discoverable. The adapter looks, in order, at:
   1. the `executable` argument of `AudiverisEngine(...)`;
   2. the `AUDIVERIS_EXE` environment variable, which must hold the **full path of the launcher file** (not the installation directory);
   3. `audiveris` and `Audiveris` on `PATH` (on Windows `PATHEXT` also finds `Audiveris.exe`/`Audiveris.bat`);
   4. the documented default install location for the platform (`/opt/audiveris/bin/Audiveris` on Linux, `C:\Program Files\Audiveris\Audiveris.exe` on Windows).

   If an explicit path or `AUDIVERIS_EXE` is set but wrong, the adapter reports that instead of falling back to `PATH`.
3. Check: `AudiverisEngine().check_available()` runs `<launcher> -version` (timeout 60 s by default, `version_timeout_seconds=` to change) and returns the parsed version, or `available=False` with an actionable message.

When Audiveris is missing, `check_available()` returns `available=False` and `recognize()` returns `EngineResult(success=False, error=...)` with the same guidance; neither raises. Invalid options raise `InvalidEngineOptionError`/`UnknownEngineOptionError` before anything runs, as documented in the engine interface.

## Invocation

```text
<launcher> -batch -transcribe -export [-sheets <ids>...] -output <output_dir> -- <input_path>
```

- `-batch`: no graphical interface. `-transcribe`: transcribe each book. `-export`: export each book as MusicXML. `-output`: the folder that receives all output files. `--`: every following argument is an input file, even if it starts with `-`.
- Arguments are passed as a list through `run_command` (no shell), with the caller's `timeout_seconds`; on timeout the whole process tree is killed.
- `-version` is run once per `AudiverisEngine` instance (in `check_available()` or before the first `recognize()`) and its result is reported in `EngineResult.version`.

### Options

| Option | Default | Maps to |
| --- | --- | --- |
| `sheets` | `None` (all sheets) | `-sheets` followed by the space-separated ids/ranges, e.g. `"1 3-5"` → `-sheets 1 3-5` |

`sheets` must match sheet ids starting at 1 and `X-Y` ranges separated by single spaces; anything else raises `InvalidEngineOptionError`. Other Audiveris flags (`-constant`, `-step`, `-force`, `-save`, `-print`, `-swap`, `-playlist`) are intentionally not exposed yet.

### Results and artifacts

- Outputs go to `output_dir` (created if needed). The handbook says `-output` stores the transcription of `foo.pdf` as `<output-folder>/foo.mxl` (compressed MusicXML).
- Artifacts are every `.mxl`/`.musicxml` file under `output_dir` (searched recursively) that is new or modified by this run; files left by earlier runs are ignored. One file is reported as `artifacts["musicxml"]`; several (for example a book with several movements) are reported as `artifacts["musicxml:<relative path>"]`.
- `success` is `True` only if the process exited with code 0, did not time out, **and** at least one artifact was written. Audiveris exit codes are not documented, so a zero exit alone is not trusted. Artifacts from failed runs are still listed for inspection.
- `diagnostics` contain the argument list, the return code or launch/timeout error, and the last 20 non-empty lines of stdout and stderr.
- Safety: the input file is never written. `.omr` project files are refused as input (Audiveris uses them as input *and* output and would modify them), and a run whose `<output_dir>/<stem>.mxl` would be the input file itself is refused.
- A successful result only means MusicXML was produced, not that the music was recognized correctly.

## Verified CLI facts

Verified on 2026-10-14 against the Audiveris handbook (web edition, which shows `-version` sample output for 5.10.0):

| Fact | Source |
| --- | --- |
| Syntax `audiveris [OPTIONS] [--] [INPUT_FILES]`; `-batch`, `-transcribe`, `-export`, `-output <output-folder>`, `-sheets int[]`, `-version`, `--` delimiter; `.omr` input is a book file (input/output), any other extension is an image input | [CLI reference](https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/) |
| `-version` prints a block including `- Version:      5.10.0` and exits | [CLI reference](https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/) |
| `-output` overrides preferences; `foo.pdf` is stored as `<output-folder>/foo.mxl` | [Standard folders](https://audiveris.github.io/audiveris/_pages/reference/folders/standard/) |
| `.mxl` is compressed MusicXML | [Quick tour: Export](https://audiveris.github.io/audiveris/_pages/tutorials/quick/export/) |
| Installers bundle a JRE; Linux launcher `/opt/audiveris/bin/Audiveris`; Windows `C:\Program Files\Audiveris\Audiveris.exe`; Scoop example `audiveris -batch -transcribe <image>` | [Install from binaries](https://audiveris.github.io/audiveris/_pages/tutorials/install/binaries/) |
| Source builds provide `bin/Audiveris` and `bin\Audiveris.bat` launchers | [Build from source](https://audiveris.github.io/audiveris/_pages/tutorials/install/sources/) |
| Repository license: AGPL-3.0 | [Audiveris/audiveris](https://github.com/Audiveris/audiveris) |

### Not verified

- **Process exit codes**: undocumented; whether a failed transcription returns non-zero is unknown. Hence the artifact requirement.
- **Whether `-export` alone triggers transcription** in batch mode: not stated, so `-transcribe` is passed explicitly.
- **File names for multi-movement books** (e.g. `foo.mvt1.mxl`): not documented on the pages read; the adapter therefore collects any new `.mxl`/`.musicxml` file.
- **Exact `-sheets` parsing** when followed by other options: only the help line `-sheets int[] : Select sheet numbers and ranges (1 4-5)` was verified; the adapter places `-output` after the ids and `--` before the input.
- **macOS launcher path** inside `Audiveris.app`: not documented.
- **Real runs**: no Audiveris installation was available when the adapter was written; the integration tests have not yet been run against a real engine.

## Tests

- Unit tests (`tests/unit/test_audiveris_engine.py`) replace `run_command` and executable discovery; they need no Java or Audiveris.
- Integration tests (`tests/integration/test_audiveris_integration.py`, marker `integration`) run with `uv run pytest -m integration` and skip when Audiveris is not found. The recognition test also needs a score image: no redistributable sample is committed yet (issue #10, decision #29), so set `OMR_LAB_AUDIVERIS_SAMPLE` to a local image path; otherwise it skips with that reason.

## License obligations (for human review)

This section records facts for the owner's decision; it does not set policy and is not legal advice.

- Audiveris is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0), per its repository. Re-check the license of the exact version installed.
- omr-lab does **not** link, import, vendor, bundle, or modify Audiveris. It locates a separately installed, unmodified launcher and runs it as an external process with command-line arguments, reading the files it writes.
- Local research use of an unmodified Audiveris, as done here, is the low-obligation case. Obligations to review arise if omr-lab ever **distributes** Audiveris (for example in a Docker image or installer, see OMR-014) or offers it, especially a **modified** version, to users **over a network** (AGPL section 13), as in a hosted cloud service.
- The project license and the policy for AGPL engines are pending owner decision **#30**. Until it is answered, do not package or redistribute Audiveris with omr-lab and do not deploy it as a service.
