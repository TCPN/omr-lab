"""Shared contracts and safe subprocess execution for OMR engines."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


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
    """

    engine_name: str
    success: bool
    artifacts: dict[str, Path] = field(default_factory=dict)
    version: str | None = None
    diagnostics: tuple[str, ...] = ()
    returncode: int | None = None
    error: str | None = None


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
    """
    if not args:
        raise ValueError("args must contain an executable")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    command = tuple(str(arg) for arg in args)
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
    ) -> EngineResult:
        """Recognize a score and return raw artifacts plus diagnostics."""
        ...
