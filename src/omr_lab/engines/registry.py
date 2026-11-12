"""The engines the command line can run, by name."""

from __future__ import annotations

from collections.abc import Callable

from omr_lab.engines.audiveris import AudiverisEngine
from omr_lab.engines.base import Engine
from omr_lab.engines.homr import HomrEngine
from omr_lab.engines.mock import MockEngine

# Engine name -> factory. A factory, not an instance, so each command gets a fresh
# engine (AudiverisEngine caches its availability check per instance).
ENGINES: dict[str, Callable[[], Engine]] = {
    "audiveris": AudiverisEngine,
    "homr": HomrEngine,
    "mock": MockEngine,
}
DEFAULT_ENGINE = "audiveris"

__all__ = ["DEFAULT_ENGINE", "ENGINES"]
