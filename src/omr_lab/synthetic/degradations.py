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
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

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
    draw = KINDS[degradation.kind].draw
    if draw is not None:
        resolved.update(draw(resolved, rng))
    return resolved


def apply(image: Image.Image, kind: str, params: Mapping[str, Any]) -> Image.Image:
    """Apply one resolved degradation to a grayscale image and return a new image.

    Raises ValueError for an unknown kind or a missing parameter, for example in a
    manifest edited by hand.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown degradation kind {kind!r}; known: {', '.join(KINDS)}")
    needed = (*DEGRADATION_PARAMETERS[kind], *KINDS[kind].drawn)
    missing = [name for name in needed if name not in params]
    if missing:
        raise ValueError(f"Degradation {kind!r} is missing parameters: {', '.join(missing)}")
    return KINDS[kind].apply(image, params)


def _seed(rng: np.random.Generator) -> int:
    # 53 bits, so the recorded value survives JSON readers that use doubles.
    return int(rng.integers(0, 2**53))


def _floats(values: Any) -> list[float]:
    return [float(v) for v in np.ravel(values)]


def _draw_corner_insets(params: ResolvedParams, rng: np.random.Generator) -> ResolvedParams:
    # Inward offset of each corner (top-left x, y; top-right x, y; bottom-right x, y;
    # bottom-left x, y) as a fraction of the width or height.
    return {"corner_insets": _floats(rng.uniform(0, params["strength"], 8))}


def _draw_noise_seed(params: ResolvedParams, rng: np.random.Generator) -> ResolvedParams:
    return {"noise_seed": _seed(rng)}


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
    darkness_fraction = _ramp(pixels.shape, float(params["angle"]))
    return _to_image(pixels * (1 - float(params["strength"]) * darkness_fraction))


def _ramp(shape: tuple[int, ...], degrees: float) -> np.ndarray:
    """0 to 1 across an image of this shape, rising towards degrees (0 = right, 90 = down)."""
    height, width = shape
    angle = np.deg2rad(degrees)
    # Each pixel's projection onto the direction (row x column, broadcast).
    ys, xs = np.arange(height)[:, None], np.arange(width)[None, :]
    projection = xs * np.cos(angle) + ys * np.sin(angle)
    span = projection.max() - projection.min()
    return (projection - projection.min()) / span if span > 0 else np.zeros_like(projection)


def _jpeg(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=int(params["quality"]))
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        return decoded.convert("L")


def _to_image(pixels: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(np.rint(pixels), 0, 255).astype(np.uint8))


# --- place: the page small on a background -------------------------------------------

# Cell sizes in pixels of the two octaves of the background texture.
_TEXTURE_CELLS = (64, 8)


def _draw_place(params: ResolvedParams, rng: np.random.Generator) -> ResolvedParams:
    return {
        # Where the page sits in the free space: 0 = left or top edge, 1 = right or bottom.
        "offset_x": float(rng.uniform(0, 1)),
        "offset_y": float(rng.uniform(0, 1)),
        "gradient_angle": float(rng.uniform(0, 360)),
        "texture_seed": _seed(rng),
    }


def _place(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    """Shrink the page and lay it on a background of the same size as the input.

    The page keeps its aspect ratio and loses resolution, as in a photo where the page
    fills only part of the frame; nothing is cut off.
    """
    width, height = image.size
    scale = float(params["scale"])
    page = image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        resample=Image.Resampling.LANCZOS,
    )
    ramp = _ramp((height, width), float(params["gradient_angle"]))
    background = float(params["background"]) * (1 - float(params["gradient"]) * ramp)
    background += float(params["texture"]) * _texture((height, width), int(params["texture_seed"]))
    canvas = _to_image(background)
    left = round(float(params["offset_x"]) * (width - page.width))
    top = round(float(params["offset_y"]) * (height - page.height))
    canvas.paste(page, (left, top))
    return canvas


def _texture(shape: tuple[int, int], seed: int) -> np.ndarray:
    """Smooth random texture with standard deviation 1: coarse and fine value noise."""
    height, width = shape
    rng = np.random.default_rng(seed)
    total = np.zeros(shape)
    for cell in _TEXTURE_CELLS:
        grid = rng.normal(size=(math.ceil(height / cell) + 1, math.ceil(width / cell) + 1))
        coarse = Image.fromarray(grid.astype(np.float32), mode="F")
        total += np.asarray(coarse.resize((width, height), Image.Resampling.BICUBIC))
    deviation = total.std()
    return total / deviation if deviation > 0 else total


# --- shadow and vignette --------------------------------------------------------------

_SHADOW_VERTICES = 6


def _draw_shadow(params: ResolvedParams, rng: np.random.Generator) -> ResolvedParams:
    # A star-shaped polygon: vertices at increasing angles around the centre, each at
    # half to the full size from it.
    angles = np.sort(rng.uniform(0, 2 * np.pi, _SHADOW_VERTICES))
    radii = rng.uniform(0.5, 1, _SHADOW_VERTICES) * float(params["size"])
    return {
        # Fractions of the width and height.
        "centre": _floats(rng.uniform(0, 1, 2)),
        # x, y offsets of each vertex from the centre, as fractions of the shorter side.
        "vertices": _floats(np.column_stack([radii * np.cos(angles), radii * np.sin(angles)])),
    }


def _shadow(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    width, height = image.size
    shorter = min(width, height)
    centre_x, centre_y = (float(v) for v in params["centre"])
    offsets = [float(v) for v in params["vertices"]]
    if len(offsets) < 6 or len(offsets) % 2:
        raise ValueError("Degradation 'shadow' needs an even number of vertices values, >= 6")
    points = [
        (centre_x * width + dx * shorter, centre_y * height + dy * shorter)
        for dx, dy in zip(offsets[::2], offsets[1::2], strict=True)
    ]
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    radius = float(params["softness"]) * shorter
    if radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius))
    darkness = float(params["strength"]) * np.asarray(mask, dtype=np.float64) / 255
    return _to_image(np.asarray(image, dtype=np.float64) * (1 - darkness))


def _vignette(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    pixels = np.asarray(image, dtype=np.float64)
    height, width = pixels.shape
    # Distance from the centre, 0 in the middle and 1 in the corners.
    ys = (np.arange(height)[:, None] - (height - 1) / 2) / max(height / 2, 1)
    xs = (np.arange(width)[None, :] - (width - 1) / 2) / max(width / 2, 1)
    distance_squared = (xs**2 + ys**2) / 2
    return _to_image(pixels * (1 - float(params["strength"]) * distance_squared))


# --- warp: folds, bends and undulation ------------------------------------------------

_FOLD_VALUES = 6  # x, y, angle, slope, direction, shade
_BEND_VALUES = 5  # x, y, radius, amplitude, direction
# Width of a fold's shading (the Gaussian's sigma), as a fraction of the shorter side.
_FOLD_BAND = 0.005
# Undulation grid cell, as a fraction of the shorter side.
_UNDULATION_CELL = 0.5
# A field this steep or steeper could fold the page over itself and hide music.
_MAX_SLOPE = 0.95
# Rows per tile when shading and warping.
_TILE_ROWS = 256


def _draw_warp(params: ResolvedParams, rng: np.random.Generator) -> ResolvedParams:
    slope, shade = float(params["fold_slope"]), float(params["fold_shade"])
    folds = [
        [
            *rng.uniform(0, 1, 2),  # a point on the line, fractions of width and height
            rng.uniform(0, 180),  # direction of the line in degrees
            slope,  # shift per pixel of distance from the line
            rng.uniform(0, 360),  # direction of the shift in degrees
            shade * rng.choice((-1.0, 1.0)),  # crease shading; the sign picks the lighter side
        ]
        for _ in range(int(params["folds"]))
    ]
    amplitude = float(params["bend_amplitude"])
    bends = [
        [
            *rng.uniform(0, 1, 2),  # centre, fractions of width and height
            rng.uniform(0.2, 0.5),  # Gaussian radius as a fraction of the diagonal
            amplitude,  # peak shift as a fraction of the shorter side
            rng.uniform(0, 360),  # direction of the shift in degrees
        ]
        for _ in range(int(params["bends"]))
    ]
    return {
        "fold_lines": _floats(folds),
        "bend_shapes": _floats(bends),
        "undulation_seed": _seed(rng),
    }


def _warp(image: Image.Image, params: Mapping[str, Any]) -> Image.Image:
    """Move each pixel along a smooth displacement field with kinks at fold lines.

    The canvas grows to hold the whole moved page, then is trimmed to it, so no part of
    the page is cut off. Rows are processed in tiles, so memory stays bounded however
    far the field moves the page.
    """
    pixels = np.array(image, dtype=np.float64)
    height, width = pixels.shape
    field = _WarpField(params, width, height)
    slope = field.slope_bound()
    if slope >= _MAX_SLOPE:
        raise ValueError(
            f"Degradation 'warp' is too steep (slope up to {slope:.2f}); use smaller values"
        )
    field.shade(pixels)

    left, top, right, bottom = field.canvas()
    warped = np.empty((bottom - top, right - left), dtype=np.uint8)
    covered_rows = np.zeros(bottom - top, dtype=bool)
    covered_columns = np.zeros(right - left, dtype=bool)
    for start in range(top, bottom, _TILE_ROWS):
        stop = min(start + _TILE_ROWS, bottom)
        xs, ys = _grid(left, right, start, stop)
        dx, dy = field.shift(xs, ys)
        source_x, source_y = xs - dx, ys - dy
        tile = _sample(pixels, source_x, source_y, WHITE)
        warped[start - top : stop - top] = np.clip(np.rint(tile), 0, 255)
        # Where a bilinear sample has some weight on the page.
        covered = (source_x > -1) & (source_x < width) & (source_y > -1) & (source_y < height)
        covered_rows[start - top : stop - top] = covered.any(axis=1)
        covered_columns |= covered.any(axis=0)
    rows, columns = np.flatnonzero(covered_rows), np.flatnonzero(covered_columns)
    return Image.fromarray(warped[rows[0] : rows[-1] + 1, columns[0] : columns[-1] + 1])


def _grid(left: int, right: int, top: int, bottom: int) -> tuple[np.ndarray, np.ndarray]:
    """x and y coordinates of every pixel in a rectangle, as two arrays of its shape."""
    ys, xs = np.mgrid[top:bottom, left:right]
    return xs.astype(np.float64), ys.astype(np.float64)


class _WarpField:
    """The displacement field of one warp, in pixels of the input image."""

    def __init__(self, params: Mapping[str, Any], width: int, height: int) -> None:
        self.width, self.height = width, height
        self.shorter = min(width, height)
        self.folds = _groups(params["fold_lines"], _FOLD_VALUES, "fold_lines")
        self.bends = _groups(params["bend_shapes"], _BEND_VALUES, "bend_shapes")
        if any(not bend[2] > 0 for bend in self.bends):
            raise ValueError("Degradation 'warp' needs a positive radius for every bend")
        self.undulation = float(params["undulation"]) * self.shorter
        cell = max(_UNDULATION_CELL * self.shorter, 1.0)
        self.cell = cell
        rng = np.random.default_rng(int(params["undulation_seed"]))
        shape = (2, math.ceil(height / cell) + 1, math.ceil(width / cell) + 1)
        self.grid = rng.uniform(-1, 1, shape)

    def slope_bound(self) -> float:
        """An upper bound of the field's slope (Frobenius norm of its Jacobian)."""
        bound = sum(abs(fold[3]) for fold in self.folds)
        diagonal = math.hypot(self.width, self.height)
        # A Gaussian's steepest slope is its peak / sigma * exp(-1/2).
        bound += sum(
            abs(amplitude) * self.shorter / (radius * diagonal) * math.exp(-0.5)
            for _, _, radius, amplitude, _ in self.bends
        )
        # Smoothstep between grid values from -1 to 1: at most 1.5 * 2 / cell per pixel,
        # for each of the four partial derivatives.
        return bound + 2 * 3 * self.undulation / self.cell

    def largest_shift(self) -> float:
        """An upper bound of the shift's length anywhere on the page, in pixels."""
        corners = [(0, 0), (self.width, 0), (0, self.height), (self.width, self.height)]
        largest = sum(
            abs(fold[3]) * max(abs(self._line(fold, x, y)) for x, y in corners)
            for fold in self.folds
        )
        largest += sum(abs(bend[3]) * self.shorter for bend in self.bends)
        # Each undulation component is a weighted mean of grid values from -1 to 1.
        return largest + math.sqrt(2) * self.undulation

    def canvas(self) -> tuple[int, int, int, int]:
        """A rectangle (left, top, right, bottom) of output positions that holds every
        moved page pixel, in input pixel coordinates.

        Page pixel p ends up at the q with q - shift(q) = p. Since the shift's slope L is
        below 1, |shift(q) - shift(p)| <= L |shift(q)| <= L M / (1 - L), where M is the
        largest shift on the page; so q lies within that margin of p + shift(p). Along a
        row, x + shift_x grows with x (its slope is at least 1 - L), so its extremes lie
        on the page's left and right edges; likewise y on the top and bottom edges.
        """
        slope = self.slope_bound()
        margin = slope * self.largest_shift() / (1 - slope)
        last_x, last_y = self.width - 1, self.height - 1
        xs = np.arange(self.width, dtype=np.float64)
        ys = np.arange(self.height, dtype=np.float64)
        left_x, _ = self.shift(np.zeros_like(ys), ys)
        right_x, _ = self.shift(np.full_like(ys, last_x), ys)
        _, top_y = self.shift(xs, np.zeros_like(xs))
        _, bottom_y = self.shift(xs, np.full_like(xs, last_y))
        return (
            math.floor(float(left_x.min()) - margin) - 1,
            math.floor(float(top_y.min()) - margin) - 1,
            math.ceil(last_x + float(right_x.max()) + margin) + 2,
            math.ceil(last_y + float(bottom_y.max()) + margin) + 2,
        )

    def _line(self, fold: list[float], xs: Any, ys: Any) -> Any:
        """Signed distance in pixels from the fold line."""
        x, y, angle = fold[0] * self.width, fold[1] * self.height, np.deg2rad(fold[2])
        return (xs - x) * -np.sin(angle) + (ys - y) * np.cos(angle)

    def shade(self, pixels: np.ndarray) -> None:
        """Shade each crease in place: lighter on one side of the line, darker on the other.

        The profile is a Gaussian's derivative scaled to peak at the fold's shade; a
        positive shade lightens the side the line's normal points to.
        """
        sigma = max(_FOLD_BAND * self.shorter, 0.5)
        for start in range(0, self.height if self.folds else 0, _TILE_ROWS):
            stop = min(start + _TILE_ROWS, self.height)
            xs, ys = _grid(0, self.width, start, stop)
            tile = pixels[start:stop]
            for fold in self.folds:
                distance = self._line(fold, xs, ys) / sigma
                amount = fold[5] * distance * np.exp(0.5 - 0.5 * distance**2)
                lighter, darker = np.maximum(amount, 0), np.maximum(-amount, 0)
                tile[:] = tile + lighter * (WHITE - tile) - darker * tile

    def shift(self, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """How far the content at each output position came from: (x, y) in pixels."""
        dx, dy = np.zeros_like(xs), np.zeros_like(ys)
        for fold in self.folds:
            # A kink: the shift grows with the distance from the line on both sides.
            size = fold[3] * np.abs(self._line(fold, xs, ys))
            direction = np.deg2rad(fold[4])
            dx += size * np.cos(direction)
            dy += size * np.sin(direction)
        diagonal = math.hypot(self.width, self.height)
        for x, y, radius, amplitude, direction in self.bends:
            sigma = radius * diagonal
            distance_squared = (xs - x * self.width) ** 2 + (ys - y * self.height) ** 2
            size = amplitude * self.shorter * np.exp(-0.5 * distance_squared / sigma**2)
            dx += size * np.cos(np.deg2rad(direction))
            dy += size * np.sin(np.deg2rad(direction))
        if self.undulation > 0:
            dx += self.undulation * self._smooth(self.grid[0], xs, ys)
            dy += self.undulation * self._smooth(self.grid[1], xs, ys)
        return dx, dy

    def _smooth(self, grid: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Value noise: smoothstep interpolation of the grid, constant beyond the page."""

        def axis(values: np.ndarray, points: int) -> tuple[np.ndarray, np.ndarray]:
            position = np.clip(values / self.cell, 0, points - 1)
            index = np.minimum(np.floor(position).astype(np.int64), points - 2)
            t = position - index
            return index, t * t * (3 - 2 * t)

        rows, columns = grid.shape
        column, u = axis(xs, columns)
        row, v = axis(ys, rows)
        top = grid[row, column] * (1 - u) + grid[row, column + 1] * u
        bottom = grid[row + 1, column] * (1 - u) + grid[row + 1, column + 1] * u
        return top * (1 - v) + bottom * v


def _groups(values: Any, size: int, name: str) -> list[list[float]]:
    flat = [float(v) for v in values]
    if not all(math.isfinite(v) for v in flat):
        raise ValueError(f"Degradation 'warp' needs finite numbers in {name}")
    if len(flat) % size:
        raise ValueError(f"Degradation 'warp' needs {name} in groups of {size} values")
    return [flat[i : i + size] for i in range(0, len(flat), size)]


def _sample(pixels: np.ndarray, xs: np.ndarray, ys: np.ndarray, fill: float) -> np.ndarray:
    """Bilinear sample of pixels at fractional positions; fill outside the image."""
    height, width = pixels.shape
    left, top = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    fx, fy = xs - left, ys - top

    def at(row: np.ndarray, column: np.ndarray) -> np.ndarray:
        inside = (row >= 0) & (row < height) & (column >= 0) & (column < width)
        return np.where(
            inside, pixels[np.clip(row, 0, height - 1), np.clip(column, 0, width - 1)], fill
        )

    upper = at(top, left) * (1 - fx) + at(top, left + 1) * fx
    lower = at(top + 1, left) * (1 - fx) + at(top + 1, left + 1) * fx
    return upper * (1 - fy) + lower * fy


# --- registry -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DegradationKind:
    """How one kind is drawn and applied; its own parameters are in spec.DEGRADATION_PARAMETERS.

    Adding a kind takes a rule there, an entry here, and the name in the specification
    schema's kind enum; a unit test checks that the three agree.
    """

    apply: Callable[[Image.Image, Mapping[str, Any]], Image.Image]
    draw: Callable[[ResolvedParams, np.random.Generator], ResolvedParams] | None = None
    """Draws the kind's extra random values, after its own parameters are resolved."""
    drawn: tuple[str, ...] = ()
    """Names of the values draw returns; apply needs them too."""


KINDS: dict[str, DegradationKind] = {
    "rotate": DegradationKind(_rotate),
    "perspective": DegradationKind(_perspective, _draw_corner_insets, ("corner_insets",)),
    "curvature": DegradationKind(_curvature),
    "blur": DegradationKind(_blur),
    "noise": DegradationKind(_noise, _draw_noise_seed, ("noise_seed",)),
    "lighting": DegradationKind(_lighting),
    "jpeg": DegradationKind(_jpeg),
    "place": DegradationKind(
        _place, _draw_place, ("offset_x", "offset_y", "gradient_angle", "texture_seed")
    ),
    "shadow": DegradationKind(_shadow, _draw_shadow, ("centre", "vertices")),
    "vignette": DegradationKind(_vignette),
    "warp": DegradationKind(_warp, _draw_warp, ("fold_lines", "bend_shapes", "undulation_seed")),
}
