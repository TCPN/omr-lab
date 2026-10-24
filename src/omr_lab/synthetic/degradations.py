"""Image degradations for synthetic samples: resolve random parameters, then apply them.

Resolving draws every random choice from the sample's generator and returns plain
numbers; applying uses only those numbers. So the recorded parameters reproduce the
image without the seed, and the same seed always gives the same parameters.

Images are 8-bit grayscale (Pillow mode "L") with a white background; areas uncovered
by a geometric change are filled with white.
"""

from __future__ import annotations

import io
import math
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
        resolved["corner_insets"] = [float(v) for v in rng.uniform(0, resolved["strength"], 8)]
    elif degradation.kind == "noise":
        # 53 bits, so the recorded value survives JSON readers that use doubles.
        resolved["noise_seed"] = int(rng.integers(0, 2**53))
    return resolved


def apply(image: Image.Image, kind: str, params: Mapping[str, Any]) -> Image.Image:
    """Apply one resolved degradation to a grayscale image and return a new image.

    Raises ValueError for an unknown kind or a missing parameter, for example in a
    manifest edited by hand.
    """
    if kind not in _APPLY:
        raise ValueError(f"Unknown degradation kind {kind!r}; known: {', '.join(_APPLY)}")
    missing = [name for name in _PARAMETERS_NEEDED_BY_APPLY[kind] if name not in params]
    if missing:
        raise ValueError(f"Degradation {kind!r} is missing parameters: {', '.join(missing)}")
    return _APPLY[kind](image, params)


def _rotate(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    return image.rotate(
        float(params["degrees"]), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=WHITE
    )


def _perspective(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    width, height = image.size
    insets = [float(v) for v in params["corner_insets"]]
    target = [
        (insets[0] * width, insets[1] * height),
        (width - insets[2] * width, insets[3] * height),
        (width - insets[4] * width, height - insets[5] * height),
        (insets[6] * width, height - insets[7] * height),
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
    from_points: list[tuple[float, float]], to_points: list[tuple[float, float]]
) -> tuple[float, ...]:
    rows = []
    values = []
    for (x, y), (u, v) in zip(from_points, to_points, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values += [u, v]
    solution = np.linalg.solve(np.array(rows, dtype=float), np.array(values, dtype=float))
    return tuple(float(v) for v in solution)


def _curvature(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64)
    height, width = pixels.shape
    amplitude = float(params["amplitude"])
    # Column x moves down by amplitude * height * sin(pi * x / width): 0 at the edges.
    shift = amplitude * height * np.sin(np.pi * np.arange(width) / width)
    # The canvas grows on the side the music moves towards, as rotate's does, so no
    # part of the page is cut off.
    pad = math.ceil(abs(amplitude) * height)
    top = pad if amplitude < 0 else 0
    source_y = np.arange(height + pad)[:, None] - top - shift[None, :]
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
    # Each pixel's projection onto the light direction, scaled to a darkness fraction
    # from 0 (brightest) to 1 (darkest).
    ys, xs = np.mgrid[0:height, 0:width]
    projection = xs * np.cos(angle) + ys * np.sin(angle)
    span = projection.max() - projection.min()
    darkness_fraction = (
        (projection - projection.min()) / span if span > 0 else np.zeros_like(projection)
    )
    return _to_image(pixels * (1 - float(params["strength"]) * darkness_fraction))


def _jpeg(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(params["quality"]))
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        return decoded.convert("L")


def _to_image(pixels: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.rint(pixels), 0, 255).astype(np.uint8))


# Parameters each kind needs when applied: its own, plus the values resolve() draws.
_EXTRA_DRAWN_PARAMETERS: dict[str, tuple[str, ...]] = {
    "perspective": ("corner_insets",),
    "noise": ("noise_seed",),
}
_PARAMETERS_NEEDED_BY_APPLY: dict[str, tuple[str, ...]] = {
    kind: (*rules, *_EXTRA_DRAWN_PARAMETERS.get(kind, ()))
    for kind, rules in DEGRADATION_PARAMETERS.items()
}

# Every kind in spec.DEGRADATION_PARAMETERS has an entry; a unit test checks this.
_APPLY: dict[str, Callable[[Image.Image, Mapping[str, Any]], Image.Image]] = {
    "rotate": _rotate,
    "perspective": _perspective,
    "curvature": _curvature,
    "blur": _blur,
    "noise": _noise,
    "lighting": _lighting,
    "jpeg": _jpeg,
}
