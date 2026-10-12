"""Interfaces and shared utilities for OMR engine adapters."""

from omr_lab.engines.base import (
    CommandResult,
    Engine,
    EngineAvailability,
    EngineResult,
    run_command,
)

__all__ = [
    "CommandResult",
    "Engine",
    "EngineAvailability",
    "EngineResult",
    "run_command",
]
