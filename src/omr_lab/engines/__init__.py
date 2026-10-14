"""Interfaces and shared utilities for OMR engine adapters."""

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
    "CommandResult",
    "Engine",
    "EngineAvailability",
    "EngineOptionValue",
    "EngineOptions",
    "EngineResult",
    "InvalidEngineOptionError",
    "UnknownEngineOptionError",
    "resolve_options",
    "run_command",
]
