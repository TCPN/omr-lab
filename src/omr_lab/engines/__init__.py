"""Interfaces, shared utilities, and adapters for OMR engines."""

from omr_lab.engines.audiveris import AudiverisEngine, LauncherLookup
from omr_lab.engines.base import (
    CommandResult,
    Engine,
    EngineAvailability,
    EngineOptions,
    EngineOptionValue,
    EngineResult,
    InvalidEngineOptionError,
    UnknownEngineOptionError,
    resolve_options,
    run_command,
)

__all__ = [
    "AudiverisEngine",
    "CommandResult",
    "Engine",
    "EngineAvailability",
    "EngineOptionValue",
    "EngineOptions",
    "EngineResult",
    "InvalidEngineOptionError",
    "LauncherLookup",
    "UnknownEngineOptionError",
    "resolve_options",
    "run_command",
]
