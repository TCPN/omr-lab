"""Shared contracts and safe subprocess execution for OMR engines."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


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


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(
    args: Sequence[str | Path],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> CommandResult:
    """Run a command without a shell and normalize launch and timeout failures.

    Arguments are passed as a sequence, so paths and other values are never
    concatenated into a shell command. The caller interprets non-zero exits and
    maps the outcome to an EngineResult.
    """
    if not args:
        raise ValueError("args must contain an executable")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    command = tuple(str(arg) for arg in args)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            args=command,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            timed_out=True,
            error=f"Command timed out after {timeout_seconds:g} seconds",
        )
    except OSError as exc:
        return CommandResult(
            args=command,
            error=f"Unable to start command: {exc}",
        )

    return CommandResult(
        args=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
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
