"""Image transforms of the optional preprocessing (docs/preprocessing.md).

Each step takes an 8-bit image (grayscale or BGR) and returns a ``StepOutcome``: the
transformed image, whether the step changed anything, and what it measured. A step
that cannot find what it looks for (no page boundary, no clear skew) leaves the image
unchanged and says why; it never guesses. Detection always runs on a grayscale copy;
the geometric steps warp the image as it is, so colour survives unless ``grayscale``
ran.

This module needs OpenCV and NumPy (the ``preprocess`` extra).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

WHITE = 255
"""Fill value for pixels a geometric step brings in from outside the source image."""


@dataclass(frozen=True, slots=True)
class StepOutcome:
    image: np.ndarray
    applied: bool
    result: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


@dataclass(frozen=True, slots=True)
class Step:
    """One named transform and its fixed parameters (recorded with every run)."""

    name: str
    params: dict[str, float | int]
    run: Callable[[np.ndarray, dict[str, Any]], StepOutcome]


def to_gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _border_value(image: np.ndarray) -> int | tuple[int, int, int]:
    return WHITE if image.ndim == 2 else (WHITE, WHITE, WHITE)


def _downscaled(gray: np.ndarray, longest_side: int) -> tuple[np.ndarray, float]:
    """The image with its longer side at most ``longest_side`` pixels, and the scale used."""
    height, width = gray.shape
    scale = min(1.0, longest_side / max(height, width))
    if scale == 1.0:
        return gray, 1.0
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA), scale


# --- grayscale -------------------------------------------------------------------------


def grayscale(image: np.ndarray, params: dict[str, Any]) -> StepOutcome:
    if image.ndim == 2:
        return StepOutcome(image, applied=False, message="The image is already grayscale.")
    return StepOutcome(to_gray(image), applied=True)


# --- page: find the sheet on a background, correct perspective, crop ----------------------


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left (the page is less than 45 degrees off)."""
    points = points.reshape(4, 2).astype(np.float64)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).ravel()
    return np.array(
        [
            points[np.argmin(sums)],
            points[np.argmin(differences)],
            points[np.argmax(sums)],
            points[np.argmax(differences)],
        ]
    )


def _page_region(small: np.ndarray) -> np.ndarray:
    """Mask of what is enclosed by edges, i.e. not reachable from the image border.

    The page edge is a strong edge even where the page is shaded (a fold, a shadow),
    while a textured background has only weak ones, so flooding the background from
    the border stops at the page outline.
    """
    blurred = cv2.GaussianBlur(small, (0, 0), 2)
    edges = cv2.Canny(blurred, 20, 60)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    height, width = small.shape
    flooded = edges.copy()
    mask = np.zeros((height + 2, width + 2), np.uint8)
    background = 128
    seeds = [(x, y) for x in range(0, width, 5) for y in (0, height - 1)]
    seeds += [(x, y) for y in range(0, height, 5) for x in (0, width - 1)]
    for x, y in seeds:
        if flooded[y, x] == 0:
            cv2.floodFill(flooded, mask, (x, y), background)
    region = np.where(flooded == background, 0, 255).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    return cv2.morphologyEx(region, cv2.MORPH_OPEN, kernel)


def _quadrilateral(region: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    perimeter = cv2.arcLength(hull, True)
    for epsilon in (0.01, 0.02, 0.03, 0.05):
        approximation = cv2.approxPolyDP(hull, epsilon * perimeter, True)
        if len(approximation) == 4:
            return _order_corners(approximation)
    # A page with a cut corner (a fold) has more than four hull corners.
    return _order_corners(cv2.boxPoints(cv2.minAreaRect(hull)))


def page(image: np.ndarray, params: dict[str, Any]) -> StepOutcome:
    gray = to_gray(image)
    small, scale = _downscaled(gray, int(params["detection_size"]))
    quad = _quadrilateral(_page_region(small))
    if quad is None:
        return StepOutcome(image, applied=False, message="No page outline was found.")
    inside_mask = np.zeros(small.shape, np.uint8)
    cv2.fillConvexPoly(inside_mask, quad.round().astype(np.int32), 1)
    inside = small[inside_mask == 1]
    outside = small[inside_mask == 0]
    if inside.size < params["min_page_fraction"] * small.size:
        return StepOutcome(
            image,
            applied=False,
            message="No page outline was found (the largest enclosed region is too small).",
        )
    if outside.size < params["min_background_fraction"] * small.size:
        return StepOutcome(
            image, applied=False, message="The page fills the image; there is no background."
        )
    contrast = float(np.median(inside)) - float(np.median(outside))
    if contrast < params["min_contrast"]:
        return StepOutcome(
            image,
            applied=False,
            result={"contrast": round(contrast, 1)},
            message=(
                f"The candidate page is only {contrast:.0f} grey levels brighter than its "
                f"surroundings (at least {params['min_contrast']:g} needed); no clear page "
                "boundary."
            ),
        )
    corners = quad / scale
    top_left, top_right, bottom_right, bottom_left = corners
    width = round(
        max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left))
    )
    height = round(
        max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right))
    )
    target = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    transform = cv2.getPerspectiveTransform(corners.astype(np.float32), target)
    warped = cv2.warpPerspective(
        image,
        transform,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=_border_value(image),
    )
    return StepOutcome(
        warped,
        applied=True,
        result={
            # In pixels of this step's input image, as detected; not a ground truth.
            "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in corners],
            "contrast": round(contrast, 1),
            "output_size": [width, height],
        },
    )


# --- deskew: rotate so the staff lines are horizontal -----------------------------------


def _ink(small: np.ndarray) -> np.ndarray:
    return 255 - cv2.adaptiveThreshold(
        small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 15
    )


def _profile_sharpness(ink: np.ndarray, degrees: float) -> float:
    """How sharply the row sums change after rotating by ``degrees``.

    Long horizontal lines (staff lines) give tall narrow peaks in the row-sum
    profile, which is largest when they are exactly horizontal.
    """
    height, width = ink.shape
    rotation = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    rotated = cv2.warpAffine(ink, rotation, (width, height))
    profile = rotated.sum(axis=1, dtype=np.float64)
    return float((np.diff(profile) ** 2).sum())


def estimate_skew(gray: np.ndarray, max_degrees: float, detection_size: int) -> float:
    """Counter-clockwise rotation in degrees that makes the image's lines horizontal."""
    small, _ = _downscaled(gray, detection_size)
    ink = _ink(small)
    best = 0.0
    for step, span in ((1.0, max_degrees), (0.1, 1.0), (0.02, 0.1)):
        candidates = np.arange(best - span, best + span + step / 2, step)
        candidates = candidates[np.abs(candidates) <= max_degrees + 1e-9]
        best = float(max(candidates, key=lambda degrees: _profile_sharpness(ink, degrees)))
    return round(best, 2) + 0.0  # + 0.0 turns -0.0 into 0.0


def rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate counter-clockwise on a canvas grown to keep the whole image."""
    height, width = image.shape[:2]
    rotation = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    cos, sin = abs(rotation[0, 0]), abs(rotation[0, 1])
    new_width = int(height * sin + width * cos + 0.5)
    new_height = int(height * cos + width * sin + 0.5)
    rotation[0, 2] += new_width / 2 - width / 2
    rotation[1, 2] += new_height / 2 - height / 2
    return cv2.warpAffine(
        image,
        rotation,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=_border_value(image),
    )


def deskew(image: np.ndarray, params: dict[str, Any]) -> StepOutcome:
    max_degrees = float(params["max_degrees"])
    degrees = estimate_skew(to_gray(image), max_degrees, int(params["detection_size"]))
    result = {"angle_degrees": degrees}
    if abs(degrees) >= max_degrees - 1e-9:
        return StepOutcome(
            image,
            applied=False,
            result=result,
            message=(
                f"The best angle is at the search limit ({max_degrees:g} degrees), so it is "
                "not a reliable estimate; the image was left unchanged."
            ),
        )
    if abs(degrees) < params["min_degrees"]:
        return StepOutcome(
            image,
            applied=False,
            result=result,
            message=f"The skew is below {params['min_degrees']:g} degrees.",
        )
    return StepOutcome(rotate(image, degrees), applied=True, result=result)


# --- threshold: binarize with a local (adaptive) threshold ------------------------------


def threshold(image: np.ndarray, params: dict[str, Any]) -> StepOutcome:
    gray = to_gray(image)
    # Odd and at least 3 pixels, as OpenCV requires; scaled with the image so the
    # window spans several staff spaces.
    block_size = max(3, int(min(gray.shape) * params["block_fraction"]) | 1)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size,
        float(params["offset"]),
    )
    return StepOutcome(binary, applied=True, result={"block_size": block_size})


STEPS: dict[str, Step] = {
    step.name: step
    for step in (
        Step("grayscale", {}, grayscale),
        Step(
            "page",
            {
                "detection_size": 1000,
                "min_contrast": 60,
                "min_page_fraction": 0.2,
                "min_background_fraction": 0.01,
            },
            page,
        ),
        Step(
            "deskew",
            {"detection_size": 1200, "max_degrees": 15, "min_degrees": 0.1},
            deskew,
        ),
        Step("threshold", {"block_fraction": 1 / 30, "offset": 15}, threshold),
    )
}
"""The steps by name, in the order they are usually combined."""


def _dihedral(image: np.ndarray) -> list[np.ndarray]:
    """The eight rotations and mirror images of ``image`` (the EXIF orientations)."""
    variants = []
    for flipped in (image, image[:, ::-1]):
        for turns in range(4):
            variants.append(np.rot90(flipped, turns))
    return variants


def load_image(data: bytes) -> tuple[np.ndarray, bool] | None:
    """Decode an image file into 8-bit grayscale or BGR; None when OpenCV cannot read it.

    Also returns whether an EXIF orientation tag turned or mirrored the image. The tag
    is applied, as photo viewers do: a phone photo taken upright is often stored
    sideways with a tag saying so, and the steps (and the PNG written afterwards, which
    has no tag) must see it upright. Transparent pixels are composited onto white and
    16-bit images are scaled to 8 bits.
    """
    buffer = np.frombuffer(data, np.uint8)
    # ANYDEPTH | ANYCOLOR applies the orientation but drops an alpha channel;
    # IMREAD_UNCHANGED keeps alpha but ignores the orientation.
    oriented = cv2.imdecode(buffer, cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)
    if oriented is None:
        return None
    stored = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
    if stored is None:
        return None
    if stored.ndim == 3 and stored.shape[2] == 1:
        stored = stored[:, :, 0]
    colour = stored[:, :, :-1] if stored.ndim == 3 and stored.shape[2] in (2, 4) else stored
    if colour.ndim == 3 and colour.shape[2] == 1:
        colour = colour[:, :, 0]
    image = oriented
    orientation_applied = colour.shape != oriented.shape or not np.array_equal(colour, oriented)
    if stored is not colour:
        # Keep the alpha channel: apply to the stored image the turn or mirror that
        # makes its colour match the oriented decode.
        matches = [
            index
            for index, variant in enumerate(_dihedral(colour))
            if variant.shape == oriented.shape and np.array_equal(variant, oriented)
        ]
        if not matches:
            return None
        image = _dihedral(stored)[matches[0]]
        orientation_applied = matches[0] != 0
    if image.dtype == np.uint16:
        image = (image // 257).astype(np.uint8)
    elif image.dtype != np.uint8:
        return None
    if image.ndim == 3 and image.shape[2] in (2, 4):
        alpha = image[:, :, -1:].astype(np.float64) / 255
        colour = image[:, :, :-1].astype(np.float64)
        image = np.round(colour * alpha + WHITE * (1 - alpha)).astype(np.uint8)
        if image.shape[2] == 1:
            image = image[:, :, 0]
    return np.ascontiguousarray(image), bool(orientation_applied)


def encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("OpenCV could not encode the preprocessed image as PNG.")
    return encoded.tobytes()


def opencv_version() -> str:
    return cv2.__version__
