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

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from omr_lab.engines.base import EngineOptionValue
from omr_lab.engines.console_tool import DEFAULT_VERSION_TIMEOUT_SECONDS, ConsoleToolEngine

__all__ = ["DEFAULT_VERSION_TIMEOUT_SECONDS", "HomrEngine"]

ENGINE_NAME = "homr"

EXECUTABLE_ENV_VAR = "HOMR_EXE"
"""Environment variable holding the full path of the ``homr`` command."""

PROJECT_URL = "https://github.com/liebharc/homr"

# File types homr 0.7.1 accepts for a single input file (compared case-insensitively).
INPUT_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".pdf")

MUSICXML_SUFFIX = ".musicxml"

# homr has no run-time options that change recognition yet (CPU or GPU is chosen
# when homr is installed), so the adapter accepts none.
SUPPORTED_OPTIONS: Mapping[str, EngineOptionValue] = {}


class HomrEngine(ConsoleToolEngine):
    """Run a locally installed homr on one image or PDF and collect its MusicXML.

    Executable lookup order: the ``executable`` argument, the ``HOMR_EXE``
    environment variable, then ``homr`` on PATH. Each run directory is
    ``<output_dir>/homr-XXXXXXXX``.
    """

    ENGINE_NAME: ClassVar[str] = ENGINE_NAME
    EXECUTABLE_ENV_VAR: ClassVar[str] = EXECUTABLE_ENV_VAR
    PATH_COMMAND_NAME: ClassVar[str] = "homr"
    PACKAGE_NAME: ClassVar[str] = "homr"
    PROJECT_URL: ClassVar[str] = PROJECT_URL
    INPUT_SUFFIXES: ClassVar[tuple[str, ...]] = INPUT_SUFFIXES
    SUPPORTED_OPTIONS: ClassVar[Mapping[str, EngineOptionValue]] = SUPPORTED_OPTIONS

    def missing_executable_message(self) -> str:
        return (
            "homr was not found. Install it in its own environment, for example "
            "`uv tool install 'homr[cpu]'` (or 'homr[cuda]' for an NVIDIA GPU; see "
            f"{PROJECT_URL}), run `homr --init` once to download its models, then either "
            f"put `homr` on PATH or set the {EXECUTABLE_ENV_VAR} environment variable to "
            "the full path of the homr command."
        )

    def build_command(
        self,
        executable: Path,
        input_path: Path,
        run_dir: Path,
        options: Mapping[str, EngineOptionValue],
    ) -> list[str | Path]:
        """Build the argument list (no shell involved).

        ``input_path`` is the copy inside the run directory and is absolute, so it
        cannot be mistaken for an option even if its name starts with ``-``. homr
        writes its output next to it.
        """
        return [executable, input_path]

    def expected_musicxml(self, input_path: Path, run_dir: Path) -> Path:
        return input_path.with_suffix(MUSICXML_SUFFIX)
