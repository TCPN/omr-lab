"""Adapter for the oemer OMR engine, invoked as an external program.

oemer (https://github.com/BreezeWhite/oemer) is licensed under MIT. omr-lab still
runs a separately installed ``oemer`` command instead of importing it: oemer pins
old versions of numpy, OpenCV and ONNX Runtime that do not fit the omr-lab
environment, and its models are large downloads. See docs/engines/oemer.md.

oemer is a deep-learning engine: two UNet models segment staff lines, noteheads
and other symbols, and rule-based code then builds the score. Its ONNX models are
downloaded into its own installation (site-packages) on the first run.

Command-line behavior was checked against oemer 0.1.8 (``oemer -h`` and
``oemer/ete.py``), not assumed:

- ``oemer <image> -o <path>`` processes one image. If ``<path>`` ends with
  ``.musicxml`` the MusicXML is written exactly there; otherwise ``<path>`` is
  taken as an existing directory. A ``<stem>_teaser.png`` is written next to it.
- ``-d`` / ``--without-deskew`` skips the deskewing step.
- A failure raises a Python exception, so the exit code is 1.
- There is no ``--version`` flag; the version is read from the package metadata
  with the Python interpreter of oemer's own environment.

Each run copies the input into a new run directory under ``output_dir`` and asks
oemer to write ``input.musicxml`` there; the source file is never written. A zero
exit is not trusted on its own: recognition only counts as successful when the
MusicXML file was actually written.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from omr_lab.engines.base import EngineOptionValue, InvalidEngineOptionError
from omr_lab.engines.console_tool import DEFAULT_VERSION_TIMEOUT_SECONDS, ConsoleToolEngine

__all__ = ["DEFAULT_VERSION_TIMEOUT_SECONDS", "OemerEngine"]

ENGINE_NAME = "oemer"

EXECUTABLE_ENV_VAR = "OEMER_EXE"
"""Environment variable holding the full path of the ``oemer`` command."""

PROJECT_URL = "https://github.com/BreezeWhite/oemer"

# Image types oemer 0.1.8 reads (PIL/OpenCV); PDF is not supported.
INPUT_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg")

MUSICXML_NAME = "input.musicxml"

DESKEW_OPTION = "deskew"
"""Bool option; False passes ``--without-deskew`` (for scans known to be straight)."""

SUPPORTED_OPTIONS: Mapping[str, EngineOptionValue] = {DESKEW_OPTION: True}


class OemerEngine(ConsoleToolEngine):
    """Run a locally installed oemer on one image and collect its MusicXML.

    Executable lookup order: the ``executable`` argument, the ``OEMER_EXE``
    environment variable, then ``oemer`` on PATH. Each run directory is
    ``<output_dir>/oemer-XXXXXXXX``.
    """

    ENGINE_NAME: ClassVar[str] = ENGINE_NAME
    EXECUTABLE_ENV_VAR: ClassVar[str] = EXECUTABLE_ENV_VAR
    PATH_COMMAND_NAME: ClassVar[str] = "oemer"
    PACKAGE_NAME: ClassVar[str] = "oemer"
    PROJECT_URL: ClassVar[str] = PROJECT_URL
    INPUT_SUFFIXES: ClassVar[tuple[str, ...]] = INPUT_SUFFIXES
    SUPPORTED_OPTIONS: ClassVar[Mapping[str, EngineOptionValue]] = SUPPORTED_OPTIONS

    def missing_executable_message(self) -> str:
        return (
            "oemer was not found. Install it in its own Python 3.11 environment with the "
            "versions it works with, for example `uv venv -p 3.11 <dir>` and "
            "`uv pip install --python <dir> oemer 'onnxruntime-gpu<1.20' "
            "'opencv-python-headless<5' 'numpy<2'` (see docs/engines/oemer.md and "
            f"{PROJECT_URL}), then either put `oemer` on PATH or set the "
            f"{EXECUTABLE_ENV_VAR} environment variable to the full path of the oemer "
            "command."
        )

    def validate_options(self, options: Mapping[str, EngineOptionValue]) -> None:
        deskew = options[DESKEW_OPTION]
        if not isinstance(deskew, bool):
            raise InvalidEngineOptionError(
                f"Option {DESKEW_OPTION!r} for engine {self.name!r} must be true or false, "
                f"got {deskew!r}."
            )

    def build_command(
        self,
        executable: Path,
        input_path: Path,
        run_dir: Path,
        options: Mapping[str, EngineOptionValue],
    ) -> list[str | Path]:
        """Build the argument list (no shell involved).

        Both paths are absolute, so neither can be mistaken for an option. The
        output is an explicit ``.musicxml`` file, so its name does not depend on
        how oemer derives a name from the input.
        """
        command: list[str | Path] = [
            executable,
            input_path,
            "-o",
            self.expected_musicxml(input_path, run_dir),
        ]
        if options[DESKEW_OPTION] is False:
            command.append("--without-deskew")
        return command

    def expected_musicxml(self, input_path: Path, run_dir: Path) -> Path:
        return run_dir / MUSICXML_NAME
