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
   3. `audiveris` and `Audiveris` on `PATH` (on Windows `PATHEXT` also finds `Audiveris.exe`);
   4. the documented default install location for the platform (`/opt/audiveris/bin/Audiveris` on Linux, `C:\Program Files\Audiveris\Audiveris.exe` on Windows).

   If an explicit path or `AUDIVERIS_EXE` is set but wrong, the adapter reports that instead of falling back to `PATH`.
   Relative paths are made absolute against the current directory. Batch launchers (`.bat`/`.cmd`, such as the `bin\Audiveris.bat` of a Windows source build) are refused on every platform, for consistent results, because Windows runs them through `cmd.exe`, which re-parses the input and output paths; on `PATH` they are skipped. `run_command` also refuses to start a `.bat`/`.cmd` program on Windows (see [Process execution](../engine-interface.md#process-execution)). Use `Audiveris.exe` from the Windows installer.

   `find_executable()` returns a `LauncherLookup(path, message)`: `path` is the absolute launcher path, or `None` with an actionable `message`.
3. Check: `AudiverisEngine().check_available()` runs `<launcher> -version` (timeout 60 s by default, `version_timeout_seconds=` to change) and returns the parsed version, or `available=False` with an actionable message. Because Audiveris exit codes are undocumented, a version parsed from stdout or stderr counts as available even if `-version` exited non-zero or timed out; the message then names the exit code (or error) and, if applicable, that the version came from stderr. Only when no version is parsed **and** the launch failed, timed out, or exited non-zero is Audiveris reported unavailable. A zero exit without a parsable version is reported as available with a note. The launcher lookup and the result, including a failure, are cached together per `AudiverisEngine` instance, so Java starts at most once for it and `recognize()` uses the launcher that was checked; create a new instance to re-check after installing or changing Audiveris.

When Audiveris is missing or `-version` fails without printing a version, `check_available()` returns `available=False` and `recognize()` returns `EngineResult(success=False, error=...)` with the same message; neither raises. `recognize()` also returns a failure result, instead of raising, when the input or output directory is unusable. Invalid options raise `InvalidEngineOptionError`/`UnknownEngineOptionError` before anything runs, as documented in the engine interface.

## Invocation

```text
<launcher> -batch -transcribe -export -output <output_dir>/audiveris-XXXXXXXX -- <input_path>
```

- `-batch`: no graphical interface. `-transcribe`: transcribe each book. `-export`: export each book as MusicXML. `-output`: the folder that receives all output files; this is a new run directory created for each call (see [Output layout](#output-layout)). `--`: every following argument is an input file, even if it starts with `-`.
- Arguments are passed as a list through `run_command` (no shell), with the caller's `timeout_seconds`; on timeout the whole process tree is killed.
- `recognize()` reuses the cached `check_available()` result: `-version` runs once per `AudiverisEngine` instance (in `check_available()` or before the first `recognize()`), and its version is reported in `EngineResult.version`.

### Options

The adapter supports no options yet (`SUPPORTED_OPTIONS` is empty); any option name raises `UnknownEngineOptionError` ("Supported options: (none)"). `-sheets` is documented, but how it parses several ids/ranges has not been verified, so sheet selection is deferred until it has been checked against a real installation. Other Audiveris flags (`-constant`, `-step`, `-force`, `-save`, `-print`, `-swap`, `-playlist`) are not exposed either.

### Output layout

- `output_dir` is created if needed and may be shared between runs or already contain files. If it exists but is not a directory, or cannot be created, `recognize()` returns a failure result before starting Java.
- Each call then creates a new, empty run directory `<output_dir>/audiveris-XXXXXXXX` (`tempfile.mkdtemp`, random suffix) and passes it to `-output`. The handbook says `-output` stores the transcription of `foo.pdf` as `<output-folder>/foo.mxl` (compressed MusicXML), so a typical result is `<output_dir>/audiveris-XXXXXXXX/foo.mxl`.
- Only files in that run directory are reported, so existing files in `output_dir` (including an input file stored there) are neither overwritten nor attributed to the run, and concurrent runs sharing `output_dir` do not see each other's output.
- The run directory is kept after both successful and failed runs (for inspection); its path is listed in `diagnostics`. Nothing is deleted by the adapter.

### Results and artifacts

- Artifacts are the files in the run directory (searched recursively) whose suffix is `.mxl`/`.xml`/`.musicxml` **and** whose name starts with `<input stem>.` (for example `foo.mxl`, `foo.xml`, `foo.mvt1.mxl`). Other files, such as an unrelated `config.xml`, are ignored.
- Keys follow the [artifact key convention](../engine-interface.md#artifact-keys): `artifacts["musicxml"]` is the primary file, which is `<stem>.mxl`, `<stem>.xml` or `<stem>.musicxml` directly in the run directory if present (checked in that order), otherwise the first collected file in sorted path order. Every other file (for example further movements) is `artifacts["musicxml:<path relative to the run directory>"]`.
- `success` is `True` only if the process exited with code 0, did not time out, **and** at least one artifact was written. Audiveris exit codes are not documented, so a zero exit alone is not trusted. Artifacts from failed runs are still listed for inspection.
- `diagnostics` contain the argument list, the return code or launch/timeout error, the last 20 non-empty lines of stdout and stderr, the run directory, and the number of MusicXML files when there are several.
- Safety: the input file is never written. `.omr` project files are refused as input (Audiveris uses them as input *and* output and would modify them). Because Audiveris writes into a fresh, empty run directory, its output cannot replace the input file or any other existing file.
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
- **File names for multi-movement books** (e.g. `foo.mvt1.mxl`): not documented on the pages read; the adapter therefore collects every `.mxl`/`.xml`/`.musicxml` file whose name starts with `<stem>.`. A name that does not start with the input stem would be missed.
- **Whether Audiveris writes other `.xml` files under `-output`** (for example book internals or log configuration): unknown. Files not named `<stem>.*` are ignored; an unrelated `<stem>.*.xml` file would still be reported as MusicXML.
- **Exact `-sheets` parsing** of several ids/ranges: only the help line `-sheets int[] : Select sheet numbers and ranges (1 4-5)` was verified, so the adapter does not offer sheet selection yet.
- **macOS launcher path** inside `Audiveris.app`: not documented.
- **`-version` output of the Windows installer's `Audiveris.exe`** (a jpackage launcher): whether it writes to an inherited stdout/stderr pipe, as `run_command` captures it, or behaves as a GUI-subsystem program without console output. If it writes nothing, `check_available()` reports either unavailable (non-zero exit) or available without a version.
- **Real runs**: no Audiveris installation was available when the adapter was written; the integration tests have not yet been run against a real engine.

## Tests

- Unit tests (`tests/unit/test_audiveris_engine.py`) replace `run_command` and executable discovery; they need no Java or Audiveris.
- Integration tests (`tests/integration/test_audiveris_integration.py` and `tests/integration/test_e2e_audiveris.py`, marker `integration`) run with `uv run pytest -m integration` and skip when Audiveris is not found. They recognize the committed sample `tests/fixtures/e2e/twinkle.png` (CC0, see its README); `OMR_LAB_AUDIVERIS_SAMPLE` points the adapter test at another local image. The end-to-end test is described in the README's [End-to-end test](../../README.md#end-to-end-test) section.

## License obligations (for human review)

This section records facts behind the owner's decision in [ADR 0002](../decisions/0002-project-license-and-agpl-engines.md). It is not legal advice.

- Audiveris is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0), per its repository. Re-check the license of the exact version installed.
- omr-lab does **not** link, import, vendor, bundle, or modify Audiveris. It locates a separately installed, unmodified launcher and runs it as an external process with command-line arguments, reading the files it writes.
- Local research use of an unmodified Audiveris, as done here, is the low-obligation case. Obligations to review arise if omr-lab ever **distributes** Audiveris (for example in a Docker image or installer, see OMR-014) or offers it, especially a **modified** version, to users **over a network** (AGPL section 13), as in a hosted cloud service.
- Policy (owner decision #30, [ADR 0002](../decisions/0002-project-license-and-agpl-engines.md)): omr-lab is MIT-licensed and runs Audiveris only as a separately installed, unmodified external program. Do not package or redistribute Audiveris with omr-lab, and do not deploy it as a service, without the license review that ADR 0002 requires first.
