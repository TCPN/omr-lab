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
from omr_lab.engines.homr import HomrEngine
from omr_lab.engines.registry import DEFAULT_ENGINE, ENGINES

__all__ = [
    "DEFAULT_ENGINE",
    "ENGINES",
    "AudiverisEngine",
    "CommandResult",
    "Engine",
    "EngineAvailability",
    "EngineOptionValue",
    "EngineOptions",
    "EngineResult",
    "HomrEngine",
    "InvalidEngineOptionError",
    "LauncherLookup",
    "UnknownEngineOptionError",
    "resolve_options",
    "run_command",
]
