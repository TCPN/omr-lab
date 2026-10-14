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
actually written.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from omr_lab.engines.base import (
    CommandResult,
    EngineAvailability,
    EngineOptions,
    EngineOptionValue,
    EngineResult,
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
PATH_NAMES: tuple[str, ...] = ("audiveris", "Audiveris")

# Default install locations documented in the handbook's binary install page.
LINUX_DEFAULT_LAUNCHER = Path("/opt/audiveris/bin/Audiveris")
WINDOWS_DEFAULT_LAUNCHER = Path(r"C:\Program Files\Audiveris\Audiveris.exe")

# Windows runs .bat/.cmd files through cmd.exe even without shell=True, and cmd.exe
# re-parses the arguments, so input/output paths would no longer be passed safely.
BATCH_LAUNCHER_SUFFIXES: frozenset[str] = frozenset({".bat", ".cmd"})

# Audiveris writes compressed ``.mxl`` by default and uncompressed ``.xml`` when its
# compression preference is disabled; ``.musicxml`` is accepted as well.
MUSICXML_SUFFIXES: tuple[str, ...] = (".mxl", ".xml", ".musicxml")

_SUFFIX_LIST = "/".join(MUSICXML_SUFFIXES)

DEFAULT_VERSION_TIMEOUT_SECONDS = 60.0

# ``-version`` prints a block such as ``- Version:      5.10.0``.
_VERSION_PATTERN = re.compile(r"^\s*-?\s*Version:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

_MAX_LOG_LINES = 20

# No options are supported yet. ``-sheets`` is documented, but how it parses several
# ids/ranges has not been verified against a real installation, so it is deferred.
DEFAULT_OPTIONS: Mapping[str, EngineOptionValue] = {}

Which = Callable[[str], str | None]


def _default_launchers() -> tuple[Path, ...]:
    if sys.platform == "win32":
        return (WINDOWS_DEFAULT_LAUNCHER,)
    if sys.platform.startswith("linux"):
        return (LINUX_DEFAULT_LAUNCHER,)
    return ()


def _is_batch_launcher(path: Path) -> bool:
    return path.suffix.lower() in BATCH_LAUNCHER_SUFFIXES


def batch_launcher_message(path: Path) -> str:
    """Explain why a .bat/.cmd launcher is refused."""
    return (
        f"Audiveris launcher {str(path)!r} is a batch file. Windows runs batch files "
        "through cmd.exe, which re-parses file paths, so it is not used. Point "
        f"{EXECUTABLE_ENV_VAR} at Audiveris.exe from the Windows installer instead, for "
        f"example {WINDOWS_DEFAULT_LAUNCHER}."
    )


def missing_executable_message() -> str:
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


def _command_diagnostics(command: CommandResult) -> list[str]:
    diagnostics = [f"command: {list(command.args)!r}"]
    if command.error:
        diagnostics.append(f"error: {command.error}")
    if command.returncode is not None:
        diagnostics.append(f"returncode: {command.returncode}")
    diagnostics.extend(_tail(command.stdout, "stdout"))
    diagnostics.extend(_tail(command.stderr, "stderr"))
    return diagnostics


def _musicxml_files(directory: Path) -> list[Path]:
    """List MusicXML files under ``directory``, recursively, in sorted order."""
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in MUSICXML_SUFFIXES
    )


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
        which: Which = shutil.which,
    ) -> None:
        self._explicit = Path(executable) if executable is not None else None
        self._version_timeout_seconds = version_timeout_seconds
        self._environ = os.environ if environ is None else environ
        self._which = which
        # check_available() starts Java via ``-version``; its outcome (including a
        # failure) is cached per instance so that Java starts at most once for it.
        self._availability: EngineAvailability | None = None
        self._executable: Path | None = None

    @property
    def name(self) -> str:
        return ENGINE_NAME

    def find_executable(self) -> tuple[Path | None, str | None]:
        """Return the launcher path, or None plus an actionable message."""
        if self._explicit is not None:
            if self._explicit.is_file():
                return self._accept(self._explicit)
            return None, (
                f"Audiveris executable {str(self._explicit)!r} (passed explicitly) does not "
                "exist or is not a file. " + missing_executable_message()
            )
        env_value = self._environ.get(EXECUTABLE_ENV_VAR, "").strip()
        if env_value:
            env_path = Path(env_value)
            if env_path.is_file():
                return self._accept(env_path)
            return None, (
                f"{EXECUTABLE_ENV_VAR} is set to {env_value!r}, which does not exist or is "
                "not a file. Set it to the full path of the Audiveris launcher, for example "
                f"{LINUX_DEFAULT_LAUNCHER} or {WINDOWS_DEFAULT_LAUNCHER}."
            )
        skipped: list[Path] = []
        for name in PATH_NAMES:
            found = self._which(name)
            if found:
                path = Path(found)
                if _is_batch_launcher(path):
                    skipped.append(path)
                    continue
                return path.absolute(), None
        for candidate in _default_launchers():
            if candidate.is_file():
                return candidate, None
        if skipped:
            return None, batch_launcher_message(skipped[0])
        return None, missing_executable_message()

    @staticmethod
    def _accept(path: Path) -> tuple[Path | None, str | None]:
        if _is_batch_launcher(path):
            return None, batch_launcher_message(path)
        # A bare relative name such as ``Audiveris`` would be looked up on PATH by the
        # OS rather than run from the current directory, so make it absolute. The path
        # is not resolved: a symlinked launcher keeps its own name and location.
        return path.absolute(), None

    def check_available(self) -> EngineAvailability:
        """Locate Audiveris and read its version; the result is cached per instance.

        Create a new AudiverisEngine to re-check after installing or changing
        Audiveris.
        """
        if self._availability is None:
            self._executable, self._availability = self._probe()
        return self._availability

    def _probe(self) -> tuple[Path | None, EngineAvailability]:
        executable, message = self.find_executable()
        if executable is None:
            return None, EngineAvailability(engine_name=self.name, available=False, message=message)
        return executable, self._availability_from_version(executable)

    def _availability_from_version(self, executable: Path) -> EngineAvailability:
        command = run_command(
            [executable, "-version"], timeout_seconds=self._version_timeout_seconds
        )
        version = parse_version(command.stdout) or parse_version(command.stderr)
        if not command.succeeded:
            details = command.error or f"exit code {command.returncode}"
            stderr = command.stderr.strip()
            if stderr:
                details += f"; stderr: {stderr.splitlines()[-1]}"
            return EngineAvailability(
                engine_name=self.name,
                available=False,
                version=version,
                message=(
                    f"Found Audiveris at {executable}, but '-version' failed ({details}). "
                    "Check that the installation is complete; reinstall from "
                    f"{DOWNLOAD_URL} if needed."
                ),
            )
        if version is None:
            return EngineAvailability(
                engine_name=self.name,
                available=True,
                message=(
                    f"Found Audiveris at {executable}, but its version could not be parsed "
                    "from the '-version' output."
                ),
            )
        return EngineAvailability(
            engine_name=self.name,
            available=True,
            version=version,
            message=f"Audiveris {version} at {executable}",
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
        """
        del options  # No options are mapped to arguments yet.
        return [
            executable,
            "-batch",
            "-transcribe",
            "-export",
            "-output",
            output_dir,
            "--",
            input_path,
        ]

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        effective = resolve_options(self.name, options, DEFAULT_OPTIONS)
        availability: EngineAvailability | None = None

        def failure(
            error: str,
            diagnostics: list[str] | None = None,
            *,
            artifacts: dict[str, Path] | None = None,
            returncode: int | None = None,
        ) -> EngineResult:
            return EngineResult(
                engine_name=self.name,
                success=False,
                artifacts=artifacts or {},
                version=availability.version if availability else None,
                diagnostics=tuple(diagnostics or ()),
                returncode=returncode,
                error=error,
                options=effective,
            )

        if not input_path.is_file():
            return failure(f"Input file {str(input_path)!r} does not exist or is not a file.")
        if input_path.suffix.lower() == ".omr":
            # Audiveris uses a .omr book as both input and output and would modify it.
            return failure(
                "Audiveris .omr project files are not accepted as input because Audiveris "
                "writes back to them; pass the source image or PDF instead."
            )

        for suffix in MUSICXML_SUFFIXES:
            expected_output = output_dir / f"{input_path.stem}{suffix}"
            if expected_output.resolve() == input_path.resolve() or (
                expected_output.exists() and expected_output.samefile(input_path)
            ):
                # samefile() also catches hard links, which resolve() does not.
                return failure(
                    f"Audiveris could write {expected_output} over the input file; choose "
                    "a different output directory."
                )

        # Every MusicXML file found after the run is attributed to it, so the output
        # directory must not already hold any; timestamps cannot tell a rewrite from
        # a stale file reliably.
        existing = _musicxml_files(output_dir)
        if existing:
            return failure(
                f"Output directory {output_dir} already contains MusicXML "
                f"({existing[0].relative_to(output_dir).as_posix()}"
                f"{', ...' if len(existing) > 1 else ''}); use a fresh directory for each run."
            )

        availability = self.check_available()
        executable = self._executable
        if not availability.available or executable is None:
            return failure(availability.message or missing_executable_message())

        output_dir.mkdir(parents=True, exist_ok=True)
        args = self.build_command(executable, input_path, output_dir, effective)
        command = run_command(args, timeout_seconds=timeout_seconds)
        diagnostics = _command_diagnostics(command)

        produced = _musicxml_files(output_dir)
        artifacts: dict[str, Path] = {}
        if len(produced) == 1:
            artifacts["musicxml"] = produced[0]
        else:
            for path in produced:
                artifacts[f"musicxml:{path.relative_to(output_dir).as_posix()}"] = path
        if len(produced) > 1:
            diagnostics.append(f"Audiveris wrote {len(produced)} MusicXML files.")

        if not command.succeeded:
            error = command.error or f"Audiveris exited with code {command.returncode}."
            return failure(
                error,
                diagnostics,
                artifacts=artifacts,
                returncode=command.returncode,
            )
        if not produced:
            return failure(
                f"Audiveris exited with code 0 but wrote no {_SUFFIX_LIST} file to "
                f"{output_dir}. The input may not contain recognizable music; see stdout/"
                "stderr in the diagnostics.",
                diagnostics,
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
