"""Image degradations for synthetic samples: resolve random parameters, then apply them.

Resolving draws every random choice from the sample's generator and returns plain
numbers; applying uses only those numbers. So the recorded parameters reproduce the
image without the seed, and the same seed always gives the same parameters.

Images are 8-bit grayscale (Pillow mode "L") with a white background; areas uncovered
by a geometric change are filled with white.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from omr_lab.synthetic.spec import DEGRADATION_PARAMETERS, DegradationSpec

ResolvedParams = dict[str, float | int | list[float]]

WHITE = 255


def resolve(degradation: DegradationSpec, rng: np.random.Generator) -> ResolvedParams:
    """Draw the parameters of one degradation; ranges are drawn uniformly."""
    rules = DEGRADATION_PARAMETERS[degradation.kind]
    resolved: ResolvedParams = {}
    for name, rule in rules.items():
        value = degradation.params[name]
        drawn = float(rng.uniform(*value)) if isinstance(value, tuple) else float(value)
        resolved[name] = int(round(drawn)) if rule.integer else drawn
    if degradation.kind == "perspective":
        # Inward offset of each corner (top-left x, y; top-right x, y; bottom-right x, y;
        # bottom-left x, y) as a fraction of the width or height.
        resolved["corners"] = [float(v) for v in rng.uniform(0, resolved["strength"], 8)]
    elif degradation.kind == "noise":
        resolved["noise_seed"] = int(rng.integers(0, 2**63 - 1))
    return resolved


def apply(image: Image.Image, kind: str, params: Mapping[str, Any]) -> Image.Image:
    """Apply one resolved degradation to a grayscale image and return a new image."""
    return _APPLY[kind](image, params)


def _rotate(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    return image.rotate(
        float(params["degrees"]), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=WHITE
    )


def _perspective(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    width, height = image.size
    c = [float(v) for v in params["corners"]]
    target = [
        (c[0] * width, c[1] * height),
        (width - c[2] * width, c[3] * height),
        (width - c[4] * width, height - c[5] * height),
        (c[6] * width, height - c[7] * height),
    ]
    page = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]
    # Pillow's coefficients map each output pixel back to the input: target -> page.
    coefficients = _perspective_coefficients(target, page)
    return image.transform(
        image.size,
        Image.Transform.PERSPECTIVE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor=WHITE,
    )


def _perspective_coefficients(
    source: list[tuple[float, float]], target: list[tuple[float, float]]
) -> tuple[float, ...]:
    rows = []
    values = []
    for (x, y), (u, v) in zip(source, target, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values += [u, v]
    solution = np.linalg.solve(np.array(rows, dtype=float), np.array(values, dtype=float))
    return tuple(float(v) for v in solution)


def _curvature(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64)
    height, width = pixels.shape
    # Column x moves down by amplitude * height * sin(pi * x / width): 0 at the edges.
    shift = float(params["amplitude"]) * height * np.sin(np.pi * np.arange(width) / width)
    source_y = np.arange(height)[:, None] - shift[None, :]
    lower = np.floor(source_y).astype(np.int64)
    fraction = source_y - lower
    columns = np.arange(width)[None, :]

    def sample(rows: np.ndarray) -> np.ndarray:
        inside = (rows >= 0) & (rows < height)
        return np.where(inside, pixels[np.clip(rows, 0, height - 1), columns], WHITE)

    bent = sample(lower) * (1 - fraction) + sample(lower + 1) * fraction
    return _to_image(bent)


def _blur(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(float(params["radius"])))


def _noise(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64)
    rng = np.random.default_rng(int(params["noise_seed"]))
    return _to_image(pixels + rng.normal(0.0, float(params["sigma"]), pixels.shape))


def _lighting(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64)
    height, width = pixels.shape
    angle = np.deg2rad(float(params["angle"]))
    # Position along the light direction, scaled to 0 (brightest) .. 1 (darkest).
    ys, xs = np.mgrid[0:height, 0:width]
    along = xs * np.cos(angle) + ys * np.sin(angle)
    span = along.max() - along.min()
    position = (along - along.min()) / span if span > 0 else np.zeros_like(along)
    return _to_image(pixels * (1 - float(params["strength"]) * position))


def _jpeg(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(params["quality"]))
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        return decoded.convert("L")


def _to_image(pixels: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.rint(pixels), 0, 255).astype(np.uint8))


_APPLY: dict[str, Callable[[Image.Image, Mapping[str, Any]], Image.Image]] = {
    "rotate": _rotate,
    "perspective": _perspective,
    "curvature": _curvature,
    "blur": _blur,
    "noise": _noise,
    "lighting": _lighting,
    "jpeg": _jpeg,
}
assert set(_APPLY) == set(DEGRADATION_PARAMETERS)
