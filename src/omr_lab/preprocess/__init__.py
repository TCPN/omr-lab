"""Optional image preprocessing before recognition (docs/preprocessing.md).

``preprocess_image`` reads the input, applies the named steps in order and writes the
result as a new PNG file; the input is never written. Every step's parameters, what
it measured, and whether it changed the image are returned for the run document.
Preprocessing does not always help recognition: compare runs with and without it.

The transforms need the optional ``preprocess`` extra (OpenCV, NumPy); naming and
checking steps does not.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STEP_NAMES: tuple[str, ...] = ("grayscale", "page", "deskew", "threshold")
"""Available steps, in the order they are usually combined."""

INSTALL_HINT = "Install the preprocess extra: `uv sync --extra preprocess`."

UNSUPPORTED_SUFFIXES = frozenset({".pdf"})
"""Inputs that preprocessing cannot read (it works on single images)."""


class PreprocessingError(Exception):
    """Preprocessing could not run on this input; the message says why."""


class PreprocessingUnavailableError(PreprocessingError):
    """OpenCV or NumPy is not installed."""


@dataclass(frozen=True, slots=True)
class StepRecord:
    """What one step did. ``result`` holds measurements, never guesses."""

    name: str
    params: dict[str, float | int]
    applied: bool
    result: dict[str, Any]
    message: str | None
    duration_seconds: float

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": dict(self.params),
            "applied": self.applied,
            "result": dict(self.result),
            "message": self.message,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class PreprocessResult:
    output_path: Path
    steps: tuple[StepRecord, ...]
    orientation_applied: bool
    """Whether the input's EXIF orientation tag turned or mirrored it before the steps."""
    width: int
    height: int


def parse_steps(steps: str | Iterable[str]) -> tuple[str, ...]:
    """Check step names (a comma-separated string or a sequence); raise ValueError.

    Order is kept; each step may appear once. An empty list is allowed and means no
    preprocessing.
    """
    names = [part.strip() for part in steps.split(",")] if isinstance(steps, str) else list(steps)
    if isinstance(steps, str) and names == [""]:
        names = []
    unknown = [name for name in names if name not in STEP_NAMES]
    if unknown:
        raise ValueError(
            f"Unknown preprocessing step(s): {', '.join(map(repr, unknown))}. "
            f"Available steps: {', '.join(STEP_NAMES)}."
        )
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(
            f"Preprocessing steps may appear only once; repeated: {', '.join(repeated)}."
        )
    return tuple(names)


def _steps_module() -> Any:
    try:
        from omr_lab.preprocess import steps
    except ImportError as exc:
        raise PreprocessingUnavailableError(
            f"Preprocessing needs {exc.name or 'a missing package'}. {INSTALL_HINT}"
        ) from exc
    return steps


def check_available() -> str:
    """Return the OpenCV version; raise PreprocessingUnavailableError when it is missing."""
    return _steps_module().opencv_version()


def preprocess_image(
    input_path: Path | str, output_path: Path | str, steps: Iterable[str]
) -> PreprocessResult:
    """Apply ``steps`` to the image at ``input_path`` and write a PNG to ``output_path``.

    ``output_path`` must not exist (it is created exclusively). Raises
    PreprocessingUnavailableError without the extra, PreprocessingError for an input
    that cannot be read or decoded, and ValueError for unknown step names.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    names = parse_steps(steps)
    module = _steps_module()
    if input_path.suffix.lower() in UNSUPPORTED_SUFFIXES:
        raise PreprocessingError(
            f"Preprocessing works on single images, not {input_path.suffix} files. Convert "
            "the pages to images first, or run without preprocessing."
        )
    try:
        data = input_path.read_bytes()
    except OSError as exc:
        raise PreprocessingError(
            f"Input file {input_path.name!r} cannot be read: {exc.strerror or exc}."
        ) from exc
    loaded = module.load_image(data)
    if loaded is None:
        raise PreprocessingError(
            f"OpenCV cannot decode {input_path.name!r} as an 8- or 16-bit image."
        )
    image, orientation_applied = loaded
    records = []
    for name in names:
        step = module.STEPS[name]
        params = dict(step.params)
        start = time.perf_counter()
        outcome = step.run(image, params)
        records.append(
            StepRecord(
                name=name,
                params=params,
                applied=outcome.applied,
                result=dict(outcome.result),
                message=outcome.message,
                duration_seconds=max(time.perf_counter() - start, 0.0),
            )
        )
        image = outcome.image
    encoded = module.encode_png(image)
    with output_path.open("xb") as stream:
        stream.write(encoded)
    height, width = image.shape[:2]
    return PreprocessResult(
        output_path=output_path,
        steps=tuple(records),
        orientation_applied=orientation_applied,
        width=int(width),
        height=int(height),
    )


__all__ = [
    "INSTALL_HINT",
    "STEP_NAMES",
    "PreprocessResult",
    "PreprocessingError",
    "PreprocessingUnavailableError",
    "StepRecord",
    "check_available",
    "parse_steps",
    "preprocess_image",
]
