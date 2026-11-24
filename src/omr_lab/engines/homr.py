"""Adapter for the homr OMR engine, invoked as an external program.

homr (https://github.com/liebharc/homr) is licensed under AGPL-3.0. Following
ADR 0002, this adapter only locates and runs a separately installed, unmodified
``homr`` command; omr-lab never imports homr or bundles it. See docs/engines/homr.md.

homr is a deep-learning engine: UNet segmentation models find staffs and symbols,
and a transformer model reads each straightened staff. Its models are ONNX files
that homr downloads into its own installation on first use (``homr --init``).

Command-line behavior was checked against homr 0.7.1 (``homr --help`` and
``homr/main.py``), not assumed:

- ``homr <image>`` processes one PNG/JPG/PDF file and writes ``<stem>.musicxml``
  and ``<stem>_teaser.png`` next to the input (a PDF is first rendered to
  ``<stem>.png``, ``<stem>_1.png``, ... next to it).
- A failure on a single file exits non-zero (1 for a processing error, 2 for an
  unsupported file or a missing path).
- There is no ``--version`` flag; the version is read from the package metadata
  with the Python interpreter of homr's own environment.

Because homr writes next to its input, each run copies the input into a new run
directory under ``output_dir`` and runs homr on the copy; the source file is never
written. A zero exit is not trusted on its own: recognition only counts as
successful when ``<stem>.musicxml`` was actually written.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from omr_lab.engines.audiveris import LauncherLookup
from omr_lab.engines.base import (
    CommandResult,
    EngineAvailability,
    EngineOptions,
    EngineOptionValue,
    EngineResult,
    resolve_options,
    run_command,
)

ENGINE_NAME = "homr"

EXECUTABLE_ENV_VAR = "HOMR_EXE"
"""Environment variable holding the full path of the ``homr`` command."""

PROJECT_URL = "https://github.com/liebharc/homr"

# Name searched on PATH (on Windows, shutil.which also tries PATHEXT, e.g. homr.exe).
PATH_COMMAND_NAME = "homr"

# File types homr 0.7.1 accepts for a single input file (compared case-insensitively).
INPUT_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".pdf")

MUSICXML_SUFFIX = ".musicxml"

# Each recognize() call writes into a new ``<output_dir>/homr-XXXXXXXX`` directory.
RUN_DIR_PREFIX = "homr-"

DEFAULT_VERSION_TIMEOUT_SECONDS = 60.0

# homr prints its whole recognized token list on one line; keep diagnostics short.
_MAX_LOG_LINES = 20
_MAX_LOG_LINE_CHARS = 300

_VERSION_SCRIPT = "import importlib.metadata as m; print(m.version('homr'))"

# homr has no run-time options that change recognition yet (CPU or GPU is chosen
# when homr is installed), so the adapter accepts none.
SUPPORTED_OPTIONS: Mapping[str, EngineOptionValue] = {}

WhichFunc = Callable[[str], str | None]


def _missing_executable_message() -> str:
    """Actionable explanation shown when no homr command can be found."""
    return (
        "homr was not found. Install it in its own environment, for example "
        "`uv tool install 'homr[cpu]'` (or 'homr[cuda]' for an NVIDIA GPU; see "
        f"{PROJECT_URL}), run `homr --init` once to download its models, then either "
        f"put `homr` on PATH or set the {EXECUTABLE_ENV_VAR} environment variable to "
        "the full path of the homr command."
    )


def _interpreter_candidates(executable: Path) -> list[Path]:
    """Python interpreters that may belong to the environment homr is installed in.

    A console script installed by pip or uv sits next to its environment's
    interpreter (``bin/python`` or ``Scripts\\python.exe``). A ``uv tool`` shim on
    PATH is a symlink to that script, so the symlink is resolved as well.
    """
    names = ("python.exe",) if sys.platform == "win32" else ("python", "python3")
    directories = [executable.parent]
    try:
        # Compare resolved paths: a symlinked parent directory (for example
        # /home -> /var/home) must not make the same directory appear twice.
        target = executable.resolve().parent
        if target != executable.parent.resolve():
            directories.append(target)
    except (OSError, RuntimeError):
        pass
    return [directory / name for directory in directories for name in names]


def _shorten(line: str) -> str:
    if len(line) <= _MAX_LOG_LINE_CHARS:
        return line
    return f"{line[:_MAX_LOG_LINE_CHARS]}... ({len(line)} characters)"


def _tail(text: str, label: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    shown = lines[-_MAX_LOG_LINES:]
    header = f"{label} (last {len(shown)} of {len(lines)} lines):"
    return [header, *(_shorten(line) for line in shown)]


def _command_diagnostics(command: CommandResult) -> list[str]:
    diagnostics = [f"command: {list(command.args)!r}"]
    if command.error:
        diagnostics.append(f"error: {command.error}")
    if command.returncode is not None:
        diagnostics.append(f"returncode: {command.returncode}")
    diagnostics.extend(_tail(command.stdout, "stdout"))
    diagnostics.extend(_tail(command.stderr, "stderr"))
    return diagnostics


def _looks_like_version(text: str) -> bool:
    """A single non-empty token; rejects tracebacks or warnings printed on stdout."""
    return bool(text) and len(text.split()) == 1


def _last_line(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return _shorten(lines[-1]) if lines else None


class HomrEngine:
    """Run a locally installed homr on one image or PDF and collect its MusicXML.

    Executable lookup order: the ``executable`` argument, the ``HOMR_EXE``
    environment variable, then ``homr`` on PATH.
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
        # The lookup and the version check are cached together per instance,
        # including a failure, so recognize() uses the command that was checked.
        self._probe_result: tuple[Path | None, EngineAvailability] | None = None

    @property
    def name(self) -> str:
        return ENGINE_NAME

    def find_executable(self) -> LauncherLookup:
        """Locate homr; on failure, ``path`` is None and ``message`` says why."""
        if self._explicit_executable is not None:
            if self._explicit_executable.is_file():
                return LauncherLookup(self._explicit_executable.absolute())
            return LauncherLookup(
                None,
                f"homr executable {str(self._explicit_executable)!r} (passed explicitly) "
                "does not exist or is not a file. " + _missing_executable_message(),
            )
        env_value = self._environ.get(EXECUTABLE_ENV_VAR, "").strip()
        if env_value:
            env_path = Path(env_value)
            if env_path.is_file():
                return LauncherLookup(env_path.absolute())
            return LauncherLookup(
                None,
                f"{EXECUTABLE_ENV_VAR} is set to {env_value!r}, which does not exist or is "
                "not a file. Set it to the full path of the homr command, for example "
                "~/.local/bin/homr.",
            )
        found = self._which(PATH_COMMAND_NAME)
        if found:
            return LauncherLookup(Path(found).absolute())
        return LauncherLookup(None, _missing_executable_message())

    def check_available(self) -> EngineAvailability:
        """Locate homr and read its version; the result is cached per instance.

        Create a new HomrEngine to re-check after installing or changing homr.
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
        return lookup.path, self._availability(lookup.path)

    def _availability(self, executable: Path) -> EngineAvailability:
        for interpreter in _interpreter_candidates(executable):
            if not interpreter.is_file():
                continue
            command = run_command(
                [interpreter, "-I", "-c", _VERSION_SCRIPT],
                timeout_seconds=self._version_timeout_seconds,
            )
            version = command.stdout.strip()
            if command.succeeded and _looks_like_version(version):
                return EngineAvailability(
                    engine_name=self.name,
                    available=True,
                    version=version,
                    message=f"homr {version} at {executable}",
                )
        # No interpreter of homr's environment was found (for example a copied
        # Windows shim), or it could not report the version: check that homr starts.
        command = run_command([executable, "--help"], timeout_seconds=self._version_timeout_seconds)
        if command.succeeded:
            return EngineAvailability(
                engine_name=self.name,
                available=True,
                message=(
                    f"Found homr at {executable}, but its version could not be read from "
                    "the Python environment it is installed in. Point "
                    f"{EXECUTABLE_ENV_VAR} at the homr command inside that environment "
                    "to record the version."
                ),
            )
        details = command.error or f"exit code {command.returncode}"
        stderr = _last_line(command.stderr)
        if stderr:
            details += f"; stderr: {stderr}"
        return EngineAvailability(
            engine_name=self.name,
            available=False,
            message=(
                f"Found homr at {executable}, but 'homr --help' failed ({details}). "
                f"Check the installation; reinstall it as described at {PROJECT_URL}."
            ),
        )

    def build_command(self, executable: Path, input_path: Path) -> list[str | Path]:
        """Build the argument list (no shell involved).

        ``input_path`` is the copy inside the run directory and is absolute, so it
        cannot be mistaken for an option even if its name starts with ``-``.
        """
        return [executable, input_path]

    def _failure_result(
        self,
        error: str,
        *,
        effective: dict[str, EngineOptionValue],
        version: str | None = None,
        diagnostics: Sequence[str] = (),
        artifacts: dict[str, Path] | None = None,
        returncode: int | None = None,
        timed_out: bool = False,
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
            timed_out=timed_out,
        )

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        """Run homr on a copy of ``input_path`` inside a new run directory.

        The run directory is ``<output_dir>/homr-XXXXXXXX`` (created with
        tempfile.mkdtemp), so ``output_dir`` may be shared between runs. It holds
        the input copy and everything homr writes, and is kept after every run.
        """
        effective = resolve_options(self.name, options, SUPPORTED_OPTIONS)

        if not input_path.is_file():
            return self._failure_result(
                f"Input file {str(input_path)!r} does not exist or is not a file.",
                effective=effective,
            )
        if input_path.suffix.lower() not in INPUT_SUFFIXES:
            return self._failure_result(
                f"homr reads only {', '.join(INPUT_SUFFIXES)} files, not "
                f"{input_path.suffix or 'files without an extension'!r}. Convert the "
                "input to PNG first.",
                effective=effective,
            )
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
            run_dir = Path(tempfile.mkdtemp(prefix=RUN_DIR_PREFIX, dir=output_dir)).absolute()
            # A fixed, plain name: homr derives its output names from the input name.
            work_input = run_dir / f"input{input_path.suffix.lower()}"
            shutil.copyfile(input_path, work_input)
        except OSError as exc:
            return self._failure_result(
                f"Cannot prepare a run directory in {output_dir}: {exc}",
                effective=effective,
                version=availability.version,
            )

        command = run_command(
            self.build_command(executable, work_input), timeout_seconds=timeout_seconds
        )
        diagnostics = _command_diagnostics(command)
        diagnostics.append(f"run directory: {run_dir}")

        musicxml = work_input.with_suffix(MUSICXML_SUFFIX)
        artifacts = {"musicxml": musicxml} if musicxml.is_file() else {}

        if not command.succeeded:
            reason = command.error or f"homr exited with code {command.returncode}."
            last = _last_line(command.stderr)
            if command.error is None and last:
                reason += f" Last stderr line: {last}"
            return self._failure_result(
                reason,
                effective=effective,
                version=availability.version,
                diagnostics=diagnostics,
                artifacts=artifacts,
                returncode=command.returncode,
                timed_out=command.timed_out,
            )
        if not artifacts:
            return self._failure_result(
                f"homr exited with code 0 but wrote no {musicxml.name} to {run_dir}; see "
                "stdout/stderr in the diagnostics.",
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
