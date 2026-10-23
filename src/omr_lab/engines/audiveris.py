"""Adapter for the Audiveris OMR engine, invoked as an external program.

Audiveris is licensed under AGPL-3.0. This adapter only locates and runs a
separately installed, unmodified Audiveris launcher; nothing is linked or bundled.
Distribution and deployment obligations are pending owner decision #30; see
docs/engines/audiveris.md.

Command-line behavior was taken from the Audiveris handbook, not assumed:

- CLI reference (options, ``--`` delimiter, ``-version`` output):
  https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/
- Output folder policy (``-output`` writes ``<output-folder>/<stem>.mxl``):
  https://audiveris.github.io/audiveris/_pages/reference/folders/standard/
- Installation and launcher locations:
  https://audiveris.github.io/audiveris/_pages/tutorials/install/binaries/
  https://audiveris.github.io/audiveris/_pages/tutorials/install/sources/

The handbook does not document process exit codes, so a zero exit is not trusted
on its own: recognition only counts as successful when a MusicXML artifact was
actually written. Each run writes into its own new subdirectory of ``output_dir``
so that only files from that run are reported.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from omr_lab.engines.base import (
    CommandResult,
    EngineAvailability,
    EngineOptions,
    EngineOptionValue,
    EngineResult,
    InvalidEngineOptionError,
    resolve_options,
    run_command,
)

ENGINE_NAME = "audiveris"

EXECUTABLE_ENV_VAR = "AUDIVERIS_EXE"
"""Environment variable holding the full path of the Audiveris launcher."""

DOWNLOAD_URL = "https://github.com/Audiveris/audiveris/releases"
INSTALL_DOC_URL = "https://audiveris.github.io/audiveris/_pages/tutorials/install/binaries/"

# Launcher names searched on PATH. ``audiveris`` is the Scoop command on Windows;
# ``Audiveris`` is the launcher of the Linux .deb package and of source builds
# (on Windows, shutil.which also tries PATHEXT suffixes such as Audiveris.exe).
PATH_LAUNCHER_NAMES: tuple[str, ...] = ("audiveris", "Audiveris")

# Default install locations documented in the handbook's binary install page.
LINUX_DEFAULT_LAUNCHER = Path("/opt/audiveris/bin/Audiveris")
WINDOWS_DEFAULT_LAUNCHER = Path(r"C:\Program Files\Audiveris\Audiveris.exe")

# Batch launchers (such as bin\Audiveris.bat of a Windows source build) are refused
# on every platform, for consistent discovery results: on Windows they run through
# cmd.exe, which re-parses the arguments, so input/output paths would no longer be
# passed safely. run_command refuses to start them on Windows as well.
BATCH_LAUNCHER_SUFFIXES: frozenset[str] = frozenset({".bat", ".cmd"})

# Audiveris writes compressed ``.mxl`` by default and uncompressed ``.xml`` when its
# compression preference is disabled; ``.musicxml`` is accepted as well. The order
# also decides which ``<stem><suffix>`` file is the primary artifact.
MUSICXML_SUFFIXES: tuple[str, ...] = (".mxl", ".xml", ".musicxml")

_MUSICXML_SUFFIXES_TEXT = "/".join(MUSICXML_SUFFIXES)

# Each recognize() call writes into a new ``<output_dir>/audiveris-XXXXXXXX`` directory.
RUN_DIR_PREFIX = "audiveris-"

DEFAULT_VERSION_TIMEOUT_SECONDS = 60.0

# ``-version`` prints a block such as ``- Version:      5.10.0``.
_VERSION_PATTERN = re.compile(r"^\s*-?\s*Version:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

_MAX_LOG_LINES = 20

MIN_STAFF_LENGTH_OPTION = "min_staff_length"
"""Option: shortest staff Audiveris keeps, in interlines (issue #55).

Audiveris drops every staff shorter than its ``LinesRetriever.minStaffLength``
constant (30 interlines in 5.11), without a warning; a short last line with one
measure is lost that way. ``None`` keeps the Audiveris default.
"""

_MIN_STAFF_LENGTH_CONSTANT = "org.audiveris.omr.sheet.grid.LinesRetriever.minStaffLength"
# A sanity cap against typos, not an Audiveris limit.
_MAX_MIN_STAFF_LENGTH = 1000

# The options this adapter supports, with their default values; resolve_options()
# uses it to reject unknown names and to fill in defaults. ``-sheets`` is
# documented, but how it parses several ids/ranges has not been verified against
# a real installation, so it is deferred.
SUPPORTED_OPTIONS: Mapping[str, EngineOptionValue] = {MIN_STAFF_LENGTH_OPTION: None}

WhichFunc = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class LauncherLookup:
    """Outcome of AudiverisEngine.find_executable().

    ``path`` is the absolute launcher path when one was found; otherwise it is
    None and ``message`` explains, actionably, why no launcher can be used.
    """

    path: Path | None
    message: str | None = None


def _default_launchers() -> tuple[Path, ...]:
    if sys.platform == "win32":
        return (WINDOWS_DEFAULT_LAUNCHER,)
    if sys.platform.startswith("linux"):
        return (LINUX_DEFAULT_LAUNCHER,)
    return ()


def _is_batch_launcher(path: Path) -> bool:
    return path.suffix.lower() in BATCH_LAUNCHER_SUFFIXES


def _batch_launcher_message(path: Path) -> str:
    """Explain why a .bat/.cmd launcher is refused."""
    return (
        f"Audiveris launcher {str(path)!r} is a batch file. Windows runs batch files "
        "through cmd.exe, which re-parses file paths, so it is not used. Point "
        f"{EXECUTABLE_ENV_VAR} at Audiveris.exe from the Windows installer instead, for "
        f"example {WINDOWS_DEFAULT_LAUNCHER}."
    )


def _missing_executable_message() -> str:
    """Actionable explanation shown when no Audiveris launcher can be found."""
    return (
        "Audiveris was not found. Install it from "
        f"{DOWNLOAD_URL} (instructions: {INSTALL_DOC_URL}), then either put the "
        "launcher on PATH or set the "
        f"{EXECUTABLE_ENV_VAR} environment variable to its full path, for example "
        f"{LINUX_DEFAULT_LAUNCHER} (Linux .deb) or {WINDOWS_DEFAULT_LAUNCHER} "
        "(Windows installer). The Flatpak package is not supported directly; point "
        f"{EXECUTABLE_ENV_VAR} at a wrapper script if you use it."
    )


def parse_version(output: str) -> str | None:
    """Extract the version number from ``audiveris -version`` output."""
    match = _VERSION_PATTERN.search(output)
    return match.group(1) if match else None


def _tail(text: str, label: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    shown = lines[-_MAX_LOG_LINES:]
    header = f"{label} (last {len(shown)} of {len(lines)} lines):"
    return [header, *shown]


def _validate_min_staff_length(engine_name: str, value: EngineOptionValue) -> None:
    # bool is an int subclass; resolve_options() has already rejected NaN and infinity.
    if value is None or (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and 0 < value <= _MAX_MIN_STAFF_LENGTH
    ):
        return
    raise InvalidEngineOptionError(
        f"Option {MIN_STAFF_LENGTH_OPTION!r} for engine {engine_name!r} must be a number of "
        f"interlines greater than 0 and at most {_MAX_MIN_STAFF_LENGTH}, or null for the "
        f"Audiveris default; got {value!r}."
    )


def _command_diagnostics(command: CommandResult) -> list[str]:
    diagnostics = [f"command: {list(command.args)!r}"]
    if command.error:
        diagnostics.append(f"error: {command.error}")
    if command.returncode is not None:
        diagnostics.append(f"returncode: {command.returncode}")
    diagnostics.extend(_tail(command.stdout, "stdout"))
    diagnostics.extend(_tail(command.stderr, "stderr"))
    return diagnostics


def _find_musicxml_files(directory: Path, stem: str) -> list[Path]:
    """List MusicXML files named after ``stem`` under ``directory``, recursively.

    A file counts when its suffix is a MusicXML suffix and its name starts with
    ``<stem>.`` (``<stem>.mxl``, ``<stem>.mvt1.mxl``, ...), so other XML files that
    Audiveris or anything else may write there are not reported.
    """
    if not directory.is_dir():
        return []
    prefix = f"{stem}."
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in MUSICXML_SUFFIXES
        and path.name.startswith(prefix)
    )


def _musicxml_artifacts(produced: Sequence[Path], run_dir: Path, stem: str) -> dict[str, Path]:
    """Map produced files to artifact keys (see docs/engine-interface.md).

    ``"musicxml"`` is the primary file: ``<stem>.mxl``, ``<stem>.xml`` or
    ``<stem>.musicxml`` directly in ``run_dir`` (first match in that order), or else
    the first file in sorted order. Every other file is ``"musicxml:<relative path>"``.
    """
    if not produced:
        return {}
    primary = next(
        (
            run_dir / f"{stem}{suffix}"
            for suffix in MUSICXML_SUFFIXES
            if run_dir / f"{stem}{suffix}" in produced
        ),
        produced[0],
    )
    artifacts = {"musicxml": primary}
    for path in produced:
        if path != primary:
            artifacts[f"musicxml:{path.relative_to(run_dir).as_posix()}"] = path
    return artifacts


class AudiverisEngine:
    """Run a locally installed Audiveris in batch mode and collect MusicXML output.

    Executable lookup order: the ``executable`` argument, the ``AUDIVERIS_EXE``
    environment variable, launcher names on PATH, then the default install
    location documented for the current platform.
    """

    def __init__(
        self,
        executable: Path | str | None = None,
        *,
        version_timeout_seconds: float = DEFAULT_VERSION_TIMEOUT_SECONDS,
        environ: Mapping[str, str] | None = None,
        which: WhichFunc = shutil.which,
    ) -> None:
        self._explicit_executable = Path(executable) if executable is not None else None
        self._version_timeout_seconds = version_timeout_seconds
        self._environ = os.environ if environ is None else environ
        self._which = which
        # The launcher lookup and the ``-version`` check (which starts Java) are
        # cached together per instance, including a failure, so that Java starts at
        # most once for it and recognize() uses the launcher that was checked.
        self._probe_result: tuple[Path | None, EngineAvailability] | None = None

    @property
    def name(self) -> str:
        return ENGINE_NAME

    def find_executable(self) -> LauncherLookup:
        """Locate the launcher; on failure, ``path`` is None and ``message`` says why."""
        if self._explicit_executable is not None:
            if self._explicit_executable.is_file():
                return self._normalize_launcher(self._explicit_executable)
            return LauncherLookup(
                None,
                f"Audiveris executable {str(self._explicit_executable)!r} (passed "
                "explicitly) does not exist or is not a file. " + _missing_executable_message(),
            )
        env_value = self._environ.get(EXECUTABLE_ENV_VAR, "").strip()
        if env_value:
            env_path = Path(env_value)
            if env_path.is_file():
                return self._normalize_launcher(env_path)
            return LauncherLookup(
                None,
                f"{EXECUTABLE_ENV_VAR} is set to {env_value!r}, which does not exist or is "
                "not a file. Set it to the full path of the Audiveris launcher, for example "
                f"{LINUX_DEFAULT_LAUNCHER} or {WINDOWS_DEFAULT_LAUNCHER}.",
            )
        skipped: list[Path] = []
        for name in PATH_LAUNCHER_NAMES:
            found = self._which(name)
            if found:
                path = Path(found)
                if _is_batch_launcher(path):
                    skipped.append(path)
                    continue
                return LauncherLookup(path.absolute())
        for candidate in _default_launchers():
            if candidate.is_file():
                return LauncherLookup(candidate)
        if skipped:
            return LauncherLookup(None, _batch_launcher_message(skipped[0]))
        return LauncherLookup(None, _missing_executable_message())

    @staticmethod
    def _normalize_launcher(path: Path) -> LauncherLookup:
        """Refuse batch launchers and make the launcher path absolute."""
        if _is_batch_launcher(path):
            return LauncherLookup(None, _batch_launcher_message(path))
        # A bare relative name such as ``Audiveris`` would be looked up on PATH by the
        # OS rather than run from the current directory, so make it absolute. The path
        # is not resolved: a symlinked launcher keeps its own name and location.
        return LauncherLookup(path.absolute())

    def check_available(self) -> EngineAvailability:
        """Locate Audiveris and read its version; the result is cached per instance.

        Create a new AudiverisEngine to re-check after installing or changing
        Audiveris.
        """
        return self._cached_probe()[1]

    def _cached_probe(self) -> tuple[Path | None, EngineAvailability]:
        if self._probe_result is None:
            self._probe_result = self._probe_availability()
        return self._probe_result

    def _probe_availability(self) -> tuple[Path | None, EngineAvailability]:
        lookup = self.find_executable()
        if lookup.path is None:
            return None, EngineAvailability(
                engine_name=self.name, available=False, message=lookup.message
            )
        return lookup.path, self._availability_from_version(lookup.path)

    def _availability_from_version(self, executable: Path) -> EngineAvailability:
        command = run_command(
            [executable, "-version"], timeout_seconds=self._version_timeout_seconds
        )
        stdout_version = parse_version(command.stdout)
        version = stdout_version or parse_version(command.stderr)
        details = command.error or f"exit code {command.returncode}"
        if version is not None:
            # Exit codes are undocumented, so a parsed version is trusted over them.
            notes: list[str] = []
            if not command.succeeded:
                notes.append(f"'-version' did not succeed: {details}")
            if stdout_version is None:
                notes.append("version read from stderr")
            suffix = f" ({'; '.join(notes)})" if notes else ""
            return EngineAvailability(
                engine_name=self.name,
                available=True,
                version=version,
                message=f"Audiveris {version} at {executable}{suffix}",
            )
        if not command.succeeded:
            stderr = command.stderr.strip()
            if stderr:
                details += f"; stderr: {stderr.splitlines()[-1]}"
            return EngineAvailability(
                engine_name=self.name,
                available=False,
                message=(
                    f"Found Audiveris at {executable}, but '-version' failed ({details}). "
                    "Check that the installation is complete; reinstall from "
                    f"{DOWNLOAD_URL} if needed."
                ),
            )
        return EngineAvailability(
            engine_name=self.name,
            available=True,
            message=(
                f"Found Audiveris at {executable}, but its version could not be parsed "
                "from the '-version' output."
            ),
        )

    def build_command(
        self,
        executable: Path,
        input_path: Path,
        output_dir: Path,
        options: Mapping[str, EngineOptionValue],
    ) -> list[str | Path]:
        """Build the batch argument list (no shell involved).

        ``-transcribe`` runs the full recognition and ``-export`` writes MusicXML;
        ``--`` makes the input path an input file even if it starts with ``-``.
        ``options`` must already be validated, as ``recognize`` does.
        """
        # Audiveris application constants, as -constant key=value pairs.
        constant_args: list[str | Path] = []
        min_staff_length = options.get(MIN_STAFF_LENGTH_OPTION)
        if min_staff_length is not None:
            constant_args += ["-constant", f"{_MIN_STAFF_LENGTH_CONSTANT}={min_staff_length!r}"]
        return [
            executable,
            "-batch",
            "-transcribe",
            "-export",
            *constant_args,
            "-output",
            output_dir,
            "--",
            input_path,
        ]

    def _failure_result(
        self,
        error: str,
        *,
        effective: dict[str, EngineOptionValue],
        version: str | None = None,
        diagnostics: Sequence[str] = (),
        artifacts: dict[str, Path] | None = None,
        returncode: int | None = None,
    ) -> EngineResult:
        return EngineResult(
            engine_name=self.name,
            success=False,
            artifacts=artifacts or {},
            version=version,
            diagnostics=tuple(diagnostics),
            returncode=returncode,
            error=error,
            options=effective,
        )

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        """Run Audiveris on ``input_path``, writing into a new run directory.

        The run directory is ``<output_dir>/audiveris-XXXXXXXX`` (created with
        tempfile.mkdtemp), so ``output_dir`` may be shared between runs. It is kept
        after a failed run for inspection.
        """
        effective = resolve_options(self.name, options, SUPPORTED_OPTIONS)
        _validate_min_staff_length(self.name, effective[MIN_STAFF_LENGTH_OPTION])

        if not input_path.is_file():
            return self._failure_result(
                f"Input file {str(input_path)!r} does not exist or is not a file.",
                effective=effective,
            )
        if input_path.suffix.lower() == ".omr":
            # Audiveris uses a .omr book as both input and output and would modify it.
            return self._failure_result(
                "Audiveris .omr project files are not accepted as input because Audiveris "
                "writes back to them; pass the source image or PDF instead.",
                effective=effective,
            )
        # Checked before check_available() so that Java is not started for a call
        # that cannot write its output anyway.
        if output_dir.exists() and not output_dir.is_dir():
            return self._failure_result(
                f"Output directory {output_dir} exists but is not a directory.",
                effective=effective,
            )
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._failure_result(
                f"Cannot create output directory {output_dir}: {exc}", effective=effective
            )

        executable, availability = self._cached_probe()
        if not availability.available or executable is None:
            return self._failure_result(
                availability.message or _missing_executable_message(),
                effective=effective,
                version=availability.version,
            )

        try:
            run_dir = Path(tempfile.mkdtemp(prefix=RUN_DIR_PREFIX, dir=output_dir))
        except OSError as exc:
            return self._failure_result(
                f"Cannot create output directory in {output_dir}: {exc}",
                effective=effective,
                version=availability.version,
            )

        args = self.build_command(executable, input_path, run_dir, effective)
        command = run_command(args, timeout_seconds=timeout_seconds)
        diagnostics = _command_diagnostics(command)
        diagnostics.append(f"run directory: {run_dir}")

        produced = _find_musicxml_files(run_dir, input_path.stem)
        artifacts = _musicxml_artifacts(produced, run_dir, input_path.stem)
        if len(produced) > 1:
            diagnostics.append(f"Audiveris wrote {len(produced)} MusicXML files.")

        if not command.succeeded:
            return self._failure_result(
                command.error or f"Audiveris exited with code {command.returncode}.",
                effective=effective,
                version=availability.version,
                diagnostics=diagnostics,
                artifacts=artifacts,
                returncode=command.returncode,
            )
        if not produced:
            return self._failure_result(
                f"Audiveris exited with code 0 but wrote no {_MUSICXML_SUFFIXES_TEXT} file "
                f"named {input_path.stem}.* to {run_dir}. The input may not contain "
                "recognizable music; see stdout/stderr in the diagnostics.",
                effective=effective,
                version=availability.version,
                diagnostics=diagnostics,
                returncode=command.returncode,
            )
        return EngineResult(
            engine_name=self.name,
            success=True,
            artifacts=artifacts,
            version=availability.version,
            diagnostics=tuple(diagnostics),
            returncode=command.returncode,
            options=effective,
        )
