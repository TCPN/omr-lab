"""Shared contracts and safe subprocess execution for OMR engines."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeAlias, runtime_checkable

EngineOptionValue: TypeAlias = str | int | float | bool | None
"""A single engine option value: a JSON scalar (finite floats only)."""

EngineOptions: TypeAlias = Mapping[str, EngineOptionValue]
"""Engine-specific settings passed to Engine.recognize(), keyed by option name."""


class InvalidEngineOptionError(ValueError):
    """An engine option has an unsupported key type or a non-scalar value."""


class UnknownEngineOptionError(InvalidEngineOptionError):
    """One or more option names are not supported by the selected engine."""

    def __init__(
        self,
        engine_name: str,
        unknown: Sequence[str],
        supported: Sequence[str],
    ) -> None:
        self.engine_name = engine_name
        self.unknown = tuple(sorted(unknown))
        self.supported = tuple(sorted(supported))
        supported_text = ", ".join(self.supported) if self.supported else "(none)"
        super().__init__(
            f"Unknown option(s) for engine {engine_name!r}: {', '.join(self.unknown)}. "
            f"Supported options: {supported_text}."
        )


def _check_option_value(engine_name: str, key: str, value: object) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise InvalidEngineOptionError(
            f"Option {key!r} for engine {engine_name!r} must be a finite number, got {value!r}."
        )
    raise InvalidEngineOptionError(
        f"Option {key!r} for engine {engine_name!r} must be a string, number, boolean, "
        f"or None, got {type(value).__name__}."
    )


def resolve_options(
    engine_name: str,
    options: EngineOptions | None,
    defaults: EngineOptions,
) -> dict[str, EngineOptionValue]:
    """Validate requested options against an adapter's defaults and merge them.

    ``defaults`` lists every option the adapter supports with its default value
    (``None`` when the engine's own default applies and nothing is passed). The
    returned dict is the effective option set to echo in EngineResult.options.

    Raises UnknownEngineOptionError for option names absent from ``defaults`` and
    InvalidEngineOptionError for non-string keys or non-scalar/non-finite values.
    Per-option type and range checks (for example ``device`` in {"cpu", "cuda"})
    remain the adapter's responsibility.
    """
    requested = dict(options or {})
    for key in requested:
        if not isinstance(key, str):
            raise InvalidEngineOptionError(
                f"Option names for engine {engine_name!r} must be strings, got {key!r}."
            )
    unknown = [key for key in requested if key not in defaults]
    if unknown:
        raise UnknownEngineOptionError(engine_name, unknown, list(defaults))
    effective = {**defaults, **requested}
    for key, value in effective.items():
        _check_option_value(engine_name, key, value)
    return effective


@dataclass(frozen=True, slots=True)
class EngineAvailability:
    """Result of checking whether an engine can run in the current environment."""

    engine_name: str
    available: bool
    version: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class EngineResult:
    """Outcome and raw artifacts from one recognition attempt.

    The success flag describes process/adapter completion only; it does not imply
    that the recognized musical content is correct.

    ``options`` holds the effective engine options (requested values merged with
    adapter defaults) so that the pipeline can record them in the run output.
    """

    engine_name: str
    success: bool
    artifacts: dict[str, Path] = field(default_factory=dict)
    version: str | None = None
    diagnostics: tuple[str, ...] = ()
    returncode: int | None = None
    error: str | None = None
    options: dict[str, EngineOptionValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Normalized result of invoking one external command."""

    args: tuple[str, ...]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the command exited successfully without a launch error or timeout."""
        return self.returncode == 0 and not self.timed_out and self.error is None


# Engines such as Audiveris emit UTF-8; decode it ourselves instead of relying on
# the locale encoding (for example cp950 on Traditional Chinese Windows).
OUTPUT_ENCODING = "utf-8"

# Upper bound for collecting remaining output after the process tree was killed.
# A process that escaped the kill (for example by starting its own session) can
# keep the pipes open; this keeps run_command from hanging on it.
_DRAIN_TIMEOUT_SECONDS = 5.0

# On Windows, CreateProcess runs .bat/.cmd files through cmd.exe even without
# shell=True, and cmd.exe re-parses the arguments, so they are not passed safely.
_WINDOWS_BATCH_SUFFIXES = frozenset({".bat", ".cmd"})


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode(OUTPUT_ENCODING, errors="replace")
    # Match the universal-newlines behavior of text-mode subprocess pipes.
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _process_group_kwargs() -> dict[str, Any]:
    """Popen options that put the child at the root of its own process group."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Forcefully terminate the child and every descendant still in its group.

    POSIX: the child was started in a new session, so its process group id equals
    its pid and SIGKILL is sent to the whole group. Descendants that moved to a
    different session or process group themselves are not reached.

    Windows: ``taskkill /T /F`` terminates the child and the descendants it can
    find by parent pid. Descendants whose parent already exited are not reached;
    a job object would close that gap and is not implemented yet.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_DRAIN_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass  # Fall back to killing the direct child below.
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # Group already gone; still make sure the direct child is killed.
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _collect_after_kill(
    process: subprocess.Popen[bytes],
    partial: subprocess.TimeoutExpired,
) -> tuple[bytes | str | None, bytes | str | None]:
    """Collect remaining output after the process tree was killed, without hanging."""
    try:
        return process.communicate(timeout=_DRAIN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        # Something outside the killed group still holds the pipes open.
        stdout = exc.stdout if exc.stdout is not None else partial.stdout
        stderr = exc.stderr if exc.stderr is not None else partial.stderr
        _close_pipes(process)
        process.wait()
        return stdout, stderr


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    """Close the output pipes unless that could block on a still-running reader.

    On Windows, communicate() reads through daemon threads that stay blocked in
    read() while an escaped descendant holds a pipe open; closing the same
    buffered stream would wait for that read and hang. The pipes are left to
    those threads there, which finish once the descendant exits. On POSIX,
    communicate() reads in the calling thread, so closing is safe.
    """
    if sys.platform == "win32":
        return
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def run_command(
    args: Sequence[str | Path],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> CommandResult:
    """Run a command without a shell and normalize launch and timeout failures.

    Arguments are passed as a sequence, so paths and other values are never
    concatenated into a shell command. stdout and stderr are decoded as UTF-8
    with replacement characters for invalid bytes, independent of the locale.
    The command runs in its own process group; on timeout the whole group is
    killed so that grandchildren (for example ``java`` started by a wrapper
    script) do not outlive it. The caller interprets non-zero exits and maps the
    outcome to an EngineResult.

    On Windows, a ``.bat``/``.cmd`` program is refused without being started,
    because cmd.exe would re-parse its arguments; the refusal is reported in
    CommandResult.error.
    """
    if not args:
        raise ValueError("args must contain an executable")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    command = tuple(str(arg) for arg in args)
    if sys.platform == "win32" and Path(command[0]).suffix.lower() in _WINDOWS_BATCH_SUFFIXES:
        return CommandResult(
            args=command,
            error=(
                f"Refusing to run batch file {command[0]!r}: Windows runs .bat/.cmd files "
                "through cmd.exe, which re-parses the arguments, so they would not be "
                "passed safely. Run the underlying executable instead."
            ),
        )
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_process_group_kwargs(),
        )
    except OSError as exc:
        return CommandResult(
            args=command,
            error=f"Unable to start command: {exc}",
        )

    # Not ``with process:``: its exit closes the pipes, which can hang on Windows
    # (see _close_pipes). communicate() closes them itself once it reaches EOF.
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        stdout, stderr = _collect_after_kill(process, exc)
        return CommandResult(
            args=command,
            stdout=_as_text(stdout),
            stderr=_as_text(stderr),
            timed_out=True,
            error=f"Command timed out after {timeout_seconds:g} seconds",
        )
    except BaseException:
        _kill_process_tree(process)
        _close_pipes(process)
        process.wait()
        raise

    return CommandResult(
        args=command,
        returncode=process.returncode,
        stdout=_as_text(stdout),
        stderr=_as_text(stderr),
    )


@runtime_checkable
class Engine(Protocol):
    """Structural interface implemented by each engine-specific adapter."""

    @property
    def name(self) -> str:
        """Stable, human-readable engine identifier."""
        ...

    def check_available(self) -> EngineAvailability:
        """Check local prerequisites without running recognition."""
        ...

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        """Recognize a score and return raw artifacts plus diagnostics.

        ``options`` holds engine-specific settings; ``None`` means no options and
        is treated like an empty mapping. Adapters raise UnknownEngineOptionError
        (see resolve_options) for names they do not support, before running the
        engine, and echo the effective options in EngineResult.options.
        """
        ...
