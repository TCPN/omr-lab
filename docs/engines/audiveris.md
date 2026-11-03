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

| Option | Default | Meaning |
| --- | --- | --- |
| `min_staff_length` | `15` | Shortest staff Audiveris keeps, in interlines (the distance between two staff lines). A number greater than 0 and at most 1000, passed as `-constant org.audiveris.omr.sheet.grid.LinesRetriever.minStaffLength=<value>`. `null` passes no constant and keeps the Audiveris default of 30. The default of 15 keeps a short last line; see [Short staves are dropped](#short-staves-are-dropped). |

Any other option name raises `UnknownEngineOptionError`, and an invalid `min_staff_length` raises `InvalidEngineOptionError`, before Audiveris runs. Example: `omr-lab run score.png -o runs/0001 --option min_staff_length=null` restores the Audiveris default.

`-sheets` is documented, but how it parses several ids/ranges has not been verified, so sheet selection is deferred until it has been checked against a real installation. Other Audiveris constants and flags (`-step`, `-force`, `-save`, `-print`, `-swap`, `-playlist`) are not exposed.

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

## Short staves are dropped

Audiveris silently drops every staff that is shorter than its `LinesRetriever.minStaffLength` constant: 30 interlines in 5.11.0. The staff and all its measures are missing from the MusicXML, the log has no warning, and the run still succeeds. A short last line of a song, such as a single closing measure, is the typical case (issue #55).

Evidence (Audiveris 5.11.0, the end-to-end sample `tests/fixtures/e2e/twinkle.png`, interline 21 px):

| Layout of the sample | Last staff | Audiveris result (limit 30) |
| --- | --- | --- |
| 5 + 6 + 1 measures (the committed sample) | 430 px, about 20 interlines | 2 systems, 11 measures: measure 12 is lost |
| 5 + 5 + 2 measures | 778 px, about 37 interlines | 3 systems, 12 measures |
| 4 + 4 + 4 measures | 1471 px | 3 systems, 12 measures |
| 5 + 6 + 1, last line stretched to full width | full width | 3 systems, 12 measures |
| 5 + 6 + 1 with `min_staff_length=15` | 430 px | 3 systems, 12 measures; 42 of 42 notes matched (one extra note) |

These runs used the Linux `.deb` of 5.11.0 extracted without installing, in a container without Tesseract languages, so chord symbols and lyrics were not recognized there; staff detection does not depend on OCR. The other layouts were rendered with the same Verovio settings as the sample and system breaks set in the MusicXML. The staff detection log line `Systems: #1[1] #2[2]` already lacks the third system, while `Retrieved raw line clusters: 3` shows that the lines were found and then discarded by length.

Lowering `min_staff_length` keeps such staves, so omr-lab passes 15 by default (since 2026-11-01; before that it passed nothing). If a run shows extra or empty staves, retry with `--option min_staff_length=null`. omr-lab does not add measures that the engine did not report, and a run alone cannot tell that a staff was dropped: compare the measure count with the ground truth (the benchmark and end-to-end correctness reports do this).

### Corpus check of the default (issue #68)

Audiveris 5.11.0 on the synthetic corpus (`benchmarks/synthetic/corpus.json`, seed 20261023, 7 sources × 5 conditions = 35 samples, no preprocessing, timeout 600 s), once with `min_staff_length=null` (limit 30) and once with `15`; generated 2026-11-03 in a Linux container without OCR languages, so only notes and measures are compared. Four sources end with a one-measure line: `twinkle` (about 20 interlines long) and the three `-short-end` sources (14 to 26 interlines; [benchmarks/synthetic/README.md](../../benchmarks/synthetic/README.md)). Notes are pitch-and-duration recall and precision over the condition's 7 samples ([metrics.md](../metrics.md)); measures are counted over the condition's samples.

| Condition | Notes R / P, limit 30 | Notes R / P, `15` | Measures predicted, 30 / 15 | Measures exactly right, 30 / 15 | Failed runs, 30 / 15 |
| --- | --- | --- | --- | --- | --- |
| clean | 0.89 / 0.92 | 0.90 / 0.93 | 90 / 91 | 78 / 79 | 0 / 0 |
| scan | 0.95 / 0.98 | 0.96 / 0.98 | 90 / 91 | 79 / 80 | 0 / 0 |
| photo | 0.92 / 0.99 | 0.93 / 0.99 | 92 / 93 | 77 / 77 | 0 / 0 |
| phone | 0.76 / 0.90 | 0.76 / 0.90 | 88 / 88 | 53 / 53 | 0 / 0 |
| phone-tilted | 0.15 / 0.44 | 0.15 / 0.44 | 36 / 36 | 8 / 8 | 1 / 1 |

Each condition has 94 ground-truth measures over its 7 samples. The failed run is `xiao-yu-di-da-phone-tilted` with both settings (no score); the low measure count of `phone-tilted` otherwise comes from runs that succeed with fewer measures, the same with both settings.

- **What changed:** only `twinkle` clean, scan and photo: with 15 they keep the last measure (11 → 12 measures, notes recall 0.93 → 1.00). All other 32 samples gave the same measures and notes with both settings.
- **No extra or empty staves from the lower limit:** no run found a second part, the measures without any note or rest (`joy-short-end-photo` measure 9, `xiao-yu-di-da-phone` measures 1, 3 and 5) are the same with both settings, and the samples with more measures than the ground truth (`xiao-yu-di-da-phone`, `joy-short-end-photo`, `joy-short-end-phone`) have the same extra measures with limit 30, so they are not caused by 15. The corpus has no text underlines, frames or tables, so those risks are not covered.
- **Decision:** keep the default at 15.
- **A second limit drops the `-short-end` last lines too.** With 15 they are still lost: `ClustersRetriever.minClusterLengthRatio` (0.2 in 5.11.0) discards a staff shorter than that fraction of the median staff length, and the log shows `Destroying spurious Cluster` instead of the last system. Lowering it to 0.1 by hand (`-constant org.audiveris.omr.sheet.grid.ClustersRetriever.minClusterLengthRatio=0.1`) brought back the last line of `joy-short-end` and `xiaoyu-short-end`, but some runs then exported several movements, so it needs its own check before omr-lab passes it. `grace-short-end`'s last line, about 14 interlines, is also under 15.

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
| `-constant key=value` defines an application constant; `-constant org.audiveris.omr.sheet.grid.LinesRetriever.minStaffLength=15` changes the result as expected (2026-10-23, 5.11.0 on Linux). The value applies to that run only: no `run.properties` is written, and a following run without it drops the short staff again. An unknown key is ignored without a message. | `-help` output and runs for issue #55 |

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
