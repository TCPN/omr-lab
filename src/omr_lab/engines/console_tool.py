"""Shared plumbing for engines that are Python console programs run as external processes.

homr and oemer are both installed with pip or uv into their own environment and
started as a console script (``homr <image>``, ``oemer <image> -o <dir>``). Neither
has a ``--version`` flag, and both write files whose names are derived from the
input name. :class:`ConsoleToolEngine` holds what such adapters share:

- executable lookup: the ``executable`` argument, an environment variable, then PATH;
- the version read from the package metadata with the interpreter of the tool's own
  environment, falling back to ``<command> --help`` as a start check;
- a cached availability result, reused by ``recognize()``;
- input checks, a new run directory per call with a plainly named input copy (the
  source file is never written), diagnostics and the mapping to EngineResult.

A subclass sets the class attributes and implements :meth:`build_command`,
:meth:`expected_musicxml` and :meth:`missing_executable_message`.

omr-lab never imports these tools; it only runs them (ADR 0002).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from omr_lab.engines.base import (
    EngineAvailability,
    EngineOptions,
    EngineOptionValue,
    EngineResult,
    LauncherLookup,
    command_diagnostics,
    last_output_line,
    resolve_options,
    run_command,
)

DEFAULT_VERSION_TIMEOUT_SECONDS = 60.0

WhichFunc = Callable[[str], str | None]


def interpreter_candidates(executable: Path) -> list[Path]:
    """Python interpreters that may belong to the environment a console script is in.

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


def _looks_like_version(text: str) -> bool:
    """A single non-empty token; rejects tracebacks or warnings printed on stdout."""
    return bool(text) and len(text.split()) == 1


class ConsoleToolEngine:
    """Base class for adapters around a separately installed Python console program."""

    ENGINE_NAME: ClassVar[str]
    """Engine name used in the registry, results and messages."""
    EXECUTABLE_ENV_VAR: ClassVar[str]
    """Environment variable holding the full path of the command."""
    PATH_COMMAND_NAME: ClassVar[str]
    """Name searched on PATH (on Windows, shutil.which also tries PATHEXT)."""
    PACKAGE_NAME: ClassVar[str]
    """Distribution name whose version is read from the package metadata."""
    PROJECT_URL: ClassVar[str]
    INPUT_SUFFIXES: ClassVar[tuple[str, ...]]
    """Accepted input suffixes, lower case (compared case-insensitively)."""
    SUPPORTED_OPTIONS: ClassVar[Mapping[str, EngineOptionValue]] = {}
    MAX_LOG_LINE_CHARS: ClassVar[int | None] = 300
    """Diagnostics shorten longer stdout/stderr lines to this many characters."""

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

    # -- subclass hooks -------------------------------------------------------

    def missing_executable_message(self) -> str:
        """Actionable explanation shown when no command can be found."""
        raise NotImplementedError

    def build_command(
        self,
        executable: Path,
        input_path: Path,
        run_dir: Path,
        options: Mapping[str, EngineOptionValue],
    ) -> list[str | Path]:
        """Argument list (no shell) for one run on the input copy ``input_path``.

        ``options`` is the effective option set, already checked by
        :meth:`validate_options`.
        """
        raise NotImplementedError

    def validate_options(self, options: Mapping[str, EngineOptionValue]) -> None:
        """Check option types and values; raise InvalidEngineOptionError if wrong."""

    def expected_musicxml(self, input_path: Path, run_dir: Path) -> Path:
        """Where the tool writes its MusicXML for the input copy ``input_path``."""
        raise NotImplementedError

    # -- shared behavior ------------------------------------------------------

    @property
    def name(self) -> str:
        return self.ENGINE_NAME

    def find_executable(self) -> LauncherLookup:
        """Locate the command; on failure, ``path`` is None and ``message`` says why."""
        tool = self.ENGINE_NAME
        if self._explicit_executable is not None:
            if self._explicit_executable.is_file():
                return LauncherLookup(self._explicit_executable.absolute())
            return LauncherLookup(
                None,
                f"{tool} executable {str(self._explicit_executable)!r} (passed explicitly) "
                "does not exist or is not a file. " + self.missing_executable_message(),
            )
        env_var = self.EXECUTABLE_ENV_VAR
        env_value = self._environ.get(env_var, "").strip()
        if env_value:
            env_path = Path(env_value)
            if env_path.is_file():
                return LauncherLookup(env_path.absolute())
            return LauncherLookup(
                None,
                f"{env_var} is set to {env_value!r}, which does not exist or is not a "
                f"file. Set it to the full path of the {self.PATH_COMMAND_NAME} command, "
                f"for example ~/.local/bin/{self.PATH_COMMAND_NAME}.",
            )
        found = self._which(self.PATH_COMMAND_NAME)
        if found:
            return LauncherLookup(Path(found).absolute())
        return LauncherLookup(None, self.missing_executable_message())

    def check_available(self) -> EngineAvailability:
        """Locate the command and read its version; the result is cached per instance.

        Create a new engine instance to re-check after installing or changing the tool.
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
        tool = self.ENGINE_NAME
        script = f"import importlib.metadata as m; print(m.version({self.PACKAGE_NAME!r}))"
        for interpreter in interpreter_candidates(executable):
            if not interpreter.is_file():
                continue
            command = run_command(
                [interpreter, "-I", "-c", script],
                timeout_seconds=self._version_timeout_seconds,
            )
            version = command.stdout.strip()
            if command.succeeded and _looks_like_version(version):
                return EngineAvailability(
                    engine_name=self.name,
                    available=True,
                    version=version,
                    message=f"{tool} {version} at {executable}",
                )
        # No interpreter of the tool's environment was found (for example a copied
        # Windows shim), or it could not report the version: check that the tool starts.
        command = run_command([executable, "--help"], timeout_seconds=self._version_timeout_seconds)
        if command.succeeded:
            return EngineAvailability(
                engine_name=self.name,
                available=True,
                message=(
                    f"Found {tool} at {executable}, but its version could not be read from "
                    "the Python environment it is installed in. Point "
                    f"{self.EXECUTABLE_ENV_VAR} at the {self.PATH_COMMAND_NAME} command "
                    "inside that environment to record the version."
                ),
            )
        details = command.error or f"exit code {command.returncode}"
        stderr = last_output_line(command.stderr, max_chars=self.MAX_LOG_LINE_CHARS)
        if stderr:
            details += f"; stderr: {stderr}"
        return EngineAvailability(
            engine_name=self.name,
            available=False,
            message=(
                f"Found {tool} at {executable}, but '{self.PATH_COMMAND_NAME} --help' "
                f"failed ({details}). Check the installation; reinstall it as described "
                f"at {self.PROJECT_URL}."
            ),
        )

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
        """Run the tool on a copy of ``input_path`` inside a new run directory.

        The run directory is ``<output_dir>/<engine>-XXXXXXXX`` (created with
        tempfile.mkdtemp), so ``output_dir`` may be shared between runs. It holds
        the input copy and everything the tool writes, and is kept after every run.
        """
        tool = self.ENGINE_NAME
        effective = resolve_options(self.name, options, self.SUPPORTED_OPTIONS)
        self.validate_options(effective)

        if not input_path.is_file():
            return self._failure_result(
                f"Input file {str(input_path)!r} does not exist or is not a file.",
                effective=effective,
            )
        if input_path.suffix.lower() not in self.INPUT_SUFFIXES:
            return self._failure_result(
                f"{tool} reads only {', '.join(self.INPUT_SUFFIXES)} files, not "
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
                availability.message or self.missing_executable_message(),
                effective=effective,
                version=availability.version,
            )

        try:
            run_dir = Path(tempfile.mkdtemp(prefix=f"{tool}-", dir=output_dir)).absolute()
            # A fixed, plain name: the tool derives its output names from the input name.
            work_input = run_dir / f"input{input_path.suffix.lower()}"
            shutil.copyfile(input_path, work_input)
        except OSError as exc:
            return self._failure_result(
                f"Cannot prepare a run directory in {output_dir}: {exc}",
                effective=effective,
                version=availability.version,
            )

        command = run_command(
            self.build_command(executable, work_input, run_dir, effective),
            timeout_seconds=timeout_seconds,
        )
        diagnostics = command_diagnostics(command, max_line_chars=self.MAX_LOG_LINE_CHARS)
        diagnostics.append(f"run directory: {run_dir}")

        musicxml = self.expected_musicxml(work_input, run_dir)
        artifacts = {"musicxml": musicxml} if musicxml.is_file() else {}

        if not command.succeeded:
            reason = command.error or f"{tool} exited with code {command.returncode}."
            last = last_output_line(command.stderr, max_chars=self.MAX_LOG_LINE_CHARS)
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
                f"{tool} exited with code 0 but wrote no {musicxml.name} to {run_dir}; see "
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
