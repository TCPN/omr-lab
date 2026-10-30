import copy
import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from omr_lab import synthetic
from omr_lab.benchmark import load_manifest, run_benchmark
from omr_lab.cli import (
    EXIT_ENGINE_UNAVAILABLE,
    EXIT_INTERRUPTED,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_OUTPUT_DIR_UNUSABLE,
    EXIT_USAGE,
    main,
)
from omr_lab.engines.mock import MockEngine
from omr_lab.musicxml import load_musicxml
from omr_lab.schemas.benchmark import validate_benchmark_manifest
from omr_lab.synthetic import CorpusSpecError, load_corpus_spec, sample_seed
from omr_lab.synthetic.spec import DEGRADATION_PARAMETERS, DegradationSpec

REPO = Path(__file__).resolve().parents[2]
TWINKLE = REPO / "tests" / "fixtures" / "e2e" / "twinkle.musicxml"
SCORE_FEATURES = REPO / "tests" / "fixtures" / "musicxml" / "score-features.musicxml"
COMMITTED_SPEC = REPO / "benchmarks" / "synthetic" / "corpus.json"

# One value inside each parameter's range, for tests that apply every kind.
EVERY_KIND = [
    {"kind": "rotate", "degrees": {"min": -3, "max": 3}},
    {"kind": "perspective", "strength": 0.05},
    {"kind": "curvature", "amplitude": 0.02},
    {"kind": "blur", "radius": 1.5},
    {"kind": "noise", "sigma": 12},
    {"kind": "lighting", "strength": 0.4, "angle": {"min": 0, "max": 360}},
    {"kind": "jpeg", "quality": {"min": 40, "max": 70}},
    {
        "kind": "warp",
        "folds": 1,
        "fold_slope": 0.02,
        "fold_shade": 0.3,
        "bends": 1,
        "bend_amplitude": 0.01,
        "undulation": 0.003,
    },
    {
        "kind": "place",
        "scale": {"min": 0.5, "max": 0.8},
        "degrees": {"min": -5, "max": 5},
        "tilt": 0.05,
        "background": 100,
        "gradient": 0.3,
        "texture": 8,
    },
    {"kind": "shadow", "strength": 0.4, "size": 0.4, "softness": 0.03},
    {"kind": "vignette", "strength": 0.3},
]


def _spec_document(**changes: object) -> dict:
    document = {
        "schema_version": "0.1.0",
        "name": "unit",
        "seed": 7,
        "sources": [
            {
                "id": "twinkle",
                "musicxml": "twinkle.musicxml",
                "work": "Twinkle",
                "source": "unit test",
                "license": "CC0-1.0",
            }
        ],
        "variants": [
            {"id": "clean", "condition": "clean", "degradations": []},
            {"id": "worn", "condition": "every degradation", "degradations": EVERY_KIND},
        ],
    }
    document.update(changes)
    return document


def _write_spec(directory: Path, document: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TWINKLE, directory / "twinkle.musicxml")
    path = directory / "corpus.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- Specification -----------------------------------------------------------------


def test_committed_specification_loads_and_its_sources_are_valid() -> None:
    spec = load_corpus_spec(COMMITTED_SPEC)

    assert {variant.id for variant in spec.variants} >= {"clean"}
    for source in spec.sources:
        assert load_musicxml(source.musicxml).container == "musicxml"


def test_specification_is_parsed(tmp_path: Path) -> None:
    spec = load_corpus_spec(_write_spec(tmp_path, _spec_document()))

    assert spec.seed == 7
    assert spec.sources[0].musicxml == tmp_path / "twinkle.musicxml"
    assert spec.sources[0].text_fonts == ("Liberation Serif", "Times New Roman")
    worn = spec.variants[1].degradations
    assert worn[0] == DegradationSpec(kind="rotate", params={"degrees": (-3, 3)})
    assert worn[1].params == {"strength": 0.05}


def _change(document: dict, path: list, value: object) -> dict:
    document = copy.deepcopy(document)
    target = document
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return document


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (["seed"], -1, "seed"),
        (["sources", 0, "id"], "Bad_Id", "sources[0].id"),
        (["sources", 0, "id"], "aux", "sources[0].id"),
        (["variants", 1, "degradations", 0, "kind"], "smudge", "kind"),
        (["variants", 1, "degradations", 0, "degrees"], "3", "degrees"),
        (["variants", 1, "degradations", 0, "angle"], 3, "unknown parameter 'angle'"),
        (["variants", 1, "degradations", 0, "degrees"], _DELETE, "needs parameter 'degrees'"),
        (["variants", 1, "degradations", 0, "degrees"], 90, "90 is outside -45 to 45"),
        (
            ["variants", 1, "degradations", 0, "degrees"],
            {"min": 2, "max": 1},
            "min 2 is greater than max 1",
        ),
        (["sources", 0, "text_fonts"], ["Bad,Font"], "text_fonts[0]"),
        (["variants", 1, "id"], "clean", "repeated: clean"),
    ],
)
def test_invalid_specifications_are_rejected(
    tmp_path: Path, path: list, value: object, message: str
) -> None:
    spec_path = _write_spec(tmp_path, _change(_spec_document(), path, value))

    with pytest.raises(CorpusSpecError, match=re.escape(message)):
        load_corpus_spec(spec_path)


def test_sample_ids_must_not_clash(tmp_path: Path) -> None:
    document = _spec_document()
    document["sources"].append({**document["sources"][0], "id": "twinkle-worn"})
    document["variants"] = [
        {"id": "x", "condition": "a", "degradations": []},
        {"id": "worn-x", "condition": "b", "degradations": []},
    ]

    with pytest.raises(CorpusSpecError, match="repeated: twinkle-worn-x"):
        load_corpus_spec(_write_spec(tmp_path, document))


def test_integral_float_seed_is_read_as_an_integer(tmp_path: Path) -> None:
    whole = load_corpus_spec(_write_spec(tmp_path / "a", _spec_document(seed=7)))
    floating = load_corpus_spec(_write_spec(tmp_path / "b", _spec_document(seed=7.0)))

    assert floating.seed == whole.seed == 7
    assert type(floating.seed) is int


def test_unreadable_and_non_json_specifications_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusSpecError, match="cannot be read"):
        load_corpus_spec(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(CorpusSpecError, match="not UTF-8 JSON"):
        load_corpus_spec(bad)


def test_sample_seed_is_stable_and_independent_of_other_samples() -> None:
    assert sample_seed(7, "twinkle", "worn") == sample_seed(7, "twinkle", "worn")
    assert sample_seed(7, "twinkle", "worn") != sample_seed(8, "twinkle", "worn")
    assert sample_seed(7, "twinkle", "worn") != sample_seed(7, "twinkle", "clean")
    assert 0 <= sample_seed(7, "twinkle", "worn") < 2**53
    # A fixed value: changing the derivation would silently change every corpus.
    assert sample_seed(20261023, "twinkle", "clean") == 7874391410349747


# --- Degradations (Pillow and NumPy) -----------------------------------------------


@pytest.fixture(scope="module")
def degradations():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from omr_lab.synthetic import degradations

    return degradations


@pytest.fixture(scope="module")
def page():
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    image = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(image)
    for y in range(100, 160, 12):
        draw.line([(20, y), (380, y)], fill=0, width=2)
    draw.ellipse([(150, 110), (175, 130)], fill=0)
    return image


def _specs() -> list[DegradationSpec]:
    document = _spec_document()
    from omr_lab.synthetic.spec import _degradation_params

    specs = []
    for item in document["variants"][1]["degradations"]:
        params, problems = _degradation_params("$", item)
        assert problems == []
        specs.append(DegradationSpec(kind=item["kind"], params=params))
    return specs


def test_every_kind_has_a_test_case() -> None:
    assert {item["kind"] for item in EVERY_KIND} == set(DEGRADATION_PARAMETERS)


def test_degradation_kinds_agree_across_schema_rules_and_implementations(degradations) -> None:
    from omr_lab.schemas.synthetic import load_synthetic_corpus_schema

    schema_kinds = load_synthetic_corpus_schema()["$defs"]["degradation"]["properties"]["kind"]
    assert set(schema_kinds["enum"]) == set(DEGRADATION_PARAMETERS) == set(degradations.KINDS)


def test_resolved_parameters_repeat_for_a_seed_and_stay_in_range(degradations) -> None:
    import numpy as np

    first = [degradations.resolve(s, np.random.default_rng(1)) for s in _specs()]
    again = [degradations.resolve(s, np.random.default_rng(1)) for s in _specs()]
    other = [degradations.resolve(s, np.random.default_rng(2)) for s in _specs()]

    assert first == again
    assert first != other
    by_kind = {spec.kind: params for spec, params in zip(_specs(), first, strict=True)}
    assert -3 <= by_kind["rotate"]["degrees"] <= 3
    assert isinstance(by_kind["jpeg"]["quality"], int)
    assert 40 <= by_kind["jpeg"]["quality"] <= 70
    corners = by_kind["perspective"]["corner_insets"]
    assert len(corners) == 8 and all(0 <= c <= 0.05 for c in corners)
    assert isinstance(by_kind["noise"]["noise_seed"], int)
    assert 0 <= by_kind["noise"]["noise_seed"] < 2**53
    warp = by_kind["warp"]
    assert len(warp["fold_lines"]) == 6 and len(warp["bend_shapes"]) == 5
    assert warp["fold_lines"][3] == 0.02 and abs(warp["fold_lines"][5]) == 0.3
    assert 0.2 <= warp["bend_shapes"][2] <= 0.5 and warp["bend_shapes"][3] == 0.01
    assert 0 <= warp["undulation_seed"] < 2**53
    place = by_kind["place"]
    assert 0.5 <= place["scale"] <= 0.8
    assert 0 <= place["offset_x"] <= 1 and 0 <= place["offset_y"] <= 1
    assert 0 <= place["texture_seed"] < 2**53
    assert -5 <= place["degrees"] <= 5
    assert len(place["corner_insets"]) == 8 and all(0 <= c <= 0.05 for c in place["corner_insets"])
    shadow = by_kind["shadow"]
    assert len(shadow["centre"]) == 2 and len(shadow["vertices"]) == 12
    radii = np.hypot(shadow["vertices"][::2], shadow["vertices"][1::2])
    assert all(0.2 - 1e-9 <= r <= 0.4 + 1e-9 for r in radii)
    # Recorded values must survive JSON exactly.
    assert json.loads(json.dumps(first)) == first


@pytest.mark.parametrize("kind", sorted(DEGRADATION_PARAMETERS))
def test_each_degradation_changes_the_image_reproducibly(degradations, page, kind) -> None:
    import numpy as np

    spec = next(s for s in _specs() if s.kind == kind)
    params = degradations.resolve(spec, np.random.default_rng(3))

    result = degradations.apply(page, kind, params)
    again = degradations.apply(page, kind, json.loads(json.dumps(params)))

    assert result.mode == "L"
    assert result.tobytes() != page.tobytes()
    assert result.tobytes() == again.tobytes()
    assert page.getpixel((0, 0)) == 255  # the input is not modified


def test_apply_names_a_missing_or_unknown_parameter_set(degradations, page) -> None:
    with pytest.raises(ValueError, match="missing parameters: corner_insets"):
        degradations.apply(page, "perspective", {"strength": 0.05})
    with pytest.raises(ValueError, match="Unknown degradation kind 'smudge'"):
        degradations.apply(page, "smudge", {})
    with pytest.raises(ValueError, match="'degrees' is 90.0; expected a number from -45 to 45"):
        degradations.apply(page, "rotate", {"degrees": 90.0})
    with pytest.raises(ValueError, match="'quality' is 50.5; expected an integer"):
        degradations.apply(page, "jpeg", {"quality": 50.5})
    with pytest.raises(ValueError, match="'quality' is '50'"):
        degradations.apply(page, "jpeg", {"quality": "50"})


def test_rotation_keeps_the_whole_page_and_fills_with_white(degradations, page) -> None:
    rotated = degradations.apply(page, "rotate", {"degrees": 10.0})

    assert rotated.width > page.width and rotated.height > page.height
    assert rotated.getpixel((0, 0)) == 255


@pytest.mark.parametrize("amplitude", [0.1, -0.1])
def test_curvature_keeps_music_near_the_edges(degradations, amplitude) -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    image = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle([(190, 0), (210, 3)], fill=0)
    draw.rectangle([(190, 296), (210, 299)], fill=0)

    bent = degradations.apply(image, "curvature", {"amplitude": amplitude})

    assert bent.size == (400, 330)
    # Both marks sit in the middle column, where the shift is largest; none is lost.
    assert int((np.asarray(bent.crop((190, 0, 211, 330))) < 128).sum()) == 2 * 21 * 4


def _dark(image) -> int:
    import numpy as np

    return int((np.asarray(image) < 128).sum())


def test_place_shrinks_the_page_onto_the_background(degradations, page) -> None:
    params = {
        "scale": 0.5,
        "background": 80,
        "gradient": 0.0,
        "texture": 0.0,
        "offset_x": 1.0,
        "offset_y": 0.0,
        "gradient_angle": 0.0,
        "texture_seed": 1,
        "degrees": 0.0,
        "tilt": 0.0,
        "corner_insets": [0.0] * 8,
    }
    placed = degradations.apply(page, "place", params)
    for offset in (2.0, -0.5, float("nan")):
        with pytest.raises(ValueError, match="offset_x from 0 to 1"):
            degradations.apply(page, "place", {**params, "offset_x": offset})

    assert placed.size == page.size
    assert placed.getpixel((0, 299)) == 80  # background
    assert placed.getpixel((201, 1)) == 255  # the page's white corner, top right
    assert placed.getpixel((199, 1)) == 80


def test_place_rotates_and_tilts_the_paper_outline(degradations) -> None:
    from PIL import Image

    white = Image.new("L", (400, 300), 255)
    params = {
        "scale": 1.0,
        "background": 50,
        "gradient": 0.0,
        "texture": 0.0,
        "offset_x": 0.5,
        "offset_y": 0.5,
        "gradient_angle": 0.0,
        "texture_seed": 1,
        "degrees": 30.0,
        "tilt": 0.0,
        "corner_insets": [0.0] * 8,
    }
    rotated = degradations.apply(white, "place", params)
    # The rotated paper's bounding box fills the canvas; its corners show the background.
    assert rotated.getpixel((200, 150)) == 255
    assert rotated.getpixel((2, 2)) == 50 and rotated.getpixel((397, 297)) == 50

    insets = [0.2, 0.0, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0]  # the top edge narrower: a tilt
    tilted = degradations.apply(
        white, "place", {**params, "degrees": 0.0, "tilt": 0.2, "corner_insets": insets}
    )
    assert tilted.getpixel((20, 5)) == 50 and tilted.getpixel((20, 295)) == 255
    for insets in ([0.1], [], [0.3] + [0.0] * 7, [-0.1] + [0.0] * 7):
        with pytest.raises(ValueError, match="8 corner_insets from 0 to tilt"):
            degradations.apply(white, "place", {**params, "tilt": 0.2, "corner_insets": insets})


def test_place_texture_and_gradient_vary_the_background(degradations, page) -> None:
    import numpy as np

    params = {
        "scale": 0.2,
        "background": 120,
        "gradient": 0.5,
        "texture": 10.0,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "gradient_angle": 0.0,
        "texture_seed": 9,
        "degrees": 0.0,
        "tilt": 0.0,
        "corner_insets": [0.0] * 8,
    }
    pixels = np.asarray(degradations.apply(page, "place", params), dtype=float)

    assert pixels[150:, :40].mean() > pixels[150:, 360:].mean() + 30  # darker to the right
    assert pixels[150:, 360:].std() > 3


def test_shadow_darkens_inside_its_polygon_only(degradations, page) -> None:
    params = {
        "strength": 0.5,
        "size": 0.2,
        "softness": 0.0,
        "centre": [0.25, 0.25],
        "vertices": [-0.1, -0.1, 0.1, -0.1, 0.1, 0.1, -0.1, 0.1],
    }
    shaded = degradations.apply(page, "shadow", params)

    assert shaded.getpixel((100, 75)) == 128
    assert shaded.getpixel((300, 250)) == 255
    with pytest.raises(ValueError, match="even number"):
        degradations.apply(page, "shadow", {**params, "vertices": [0.1, 0.1, 0.2]})


def test_vignette_darkens_the_corners_not_the_centre(degradations) -> None:
    from PIL import Image

    white = Image.new("L", (101, 81), 255)
    vignetted = degradations.apply(white, "vignette", {"strength": 0.4})

    assert vignetted.getpixel((50, 40)) == 255
    assert vignetted.getpixel((0, 0)) < 160


_FLAT_WARP = {
    "folds": 0,
    "fold_slope": 0,
    "fold_shade": 0,
    "bends": 0,
    "bend_amplitude": 0,
    "undulation": 0,
    "fold_lines": [],
    "bend_shapes": [],
    "undulation_seed": 1,
}


def test_warp_without_components_changes_nothing(degradations, page) -> None:
    assert degradations.apply(page, "warp", _FLAT_WARP).tobytes() == page.tobytes()


def test_warp_at_its_limits_keeps_every_mark(degradations) -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    from omr_lab.synthetic.spec import DEGRADATION_PARAMETERS

    image = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(image)
    for x, y in [(0, 0), (392, 0), (0, 292), (392, 292), (196, 146)]:
        draw.rectangle([(x, y), (x + 7, y + 7)], fill=0)
    limits = {name: rule.maximum for name, rule in DEGRADATION_PARAMETERS["warp"].items()}
    spec = DegradationSpec(kind="warp", params={**limits, "fold_shade": 0})

    for seed in range(5):
        params = degradations.resolve(spec, np.random.default_rng(seed))
        warped = degradations.apply(image, "warp", params)
        # Each 8 x 8 mark moves and stretches but none is cut off.
        assert _dark(warped) > 0.6 * _dark(image)
        assert _count_marks(np.asarray(warped) < 128) == 5, (seed, params)


def _count_marks(dark) -> int:
    import numpy as np
    from PIL import Image, ImageFilter

    # Grow each mark a little so a split pixel still counts as one, then count blobs.
    grown = Image.fromarray((dark * 255).astype("uint8")).filter(ImageFilter.MaxFilter(5))
    seen = np.asarray(grown) > 0
    labels = np.zeros(seen.shape, dtype=int)
    count = 0
    for start in zip(*np.nonzero(seen), strict=True):
        if labels[start]:
            continue
        count += 1
        stack = [start]
        labels[start] = count
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < seen.shape[0] and 0 <= nx < seen.shape[1]:
                    if seen[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = count
                        stack.append((ny, nx))
    return count


def test_warp_canvas_stays_bounded_on_a_full_page_at_the_limits(degradations) -> None:
    import numpy as np

    from omr_lab.synthetic.spec import DEGRADATION_PARAMETERS

    # An A4 page at 300 dpi; the canvas is computed without warping anything.
    width, height = 2480, 3508
    limits = {name: rule.maximum for name, rule in DEGRADATION_PARAMETERS["warp"].items()}
    spec = DegradationSpec(kind="warp", params=limits)
    for seed in range(20):
        params = degradations.resolve(spec, np.random.default_rng(seed))
        field = degradations._WarpField(params, width, height)
        left, top, right, bottom = field.canvas()
        assert field.slope_bound() < 0.95
        assert (right - left) * (bottom - top) < 8 * width * height, seed
    # Tiles keep the memory per step at a few rows of the canvas, whatever its size.
    assert degradations._TILE_ROWS * (right - left) < 5_000_000


def test_warp_fold_bends_a_line_and_shades_the_crease(degradations) -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    image = Image.new("L", (400, 300), 255)
    ImageDraw.Draw(image).line([(0, 150), (399, 150)], fill=0, width=3)
    # A vertical fold through the middle that shifts content down, and a dark crease.
    fold = [0.5, 0.5, 90.0, 0.05, 90.0, -0.5]
    warped = degradations.apply(image, "warp", {**_FLAT_WARP, "folds": 1, "fold_lines": fold})
    pixels = np.asarray(warped)

    def line_row(column: int) -> float:
        return float(np.nonzero(pixels[:, column] < 128)[0].mean())

    middle = warped.width // 2
    # The line is straight on each side and bends at the fold: a V shape.
    assert line_row(10) - line_row(middle) > 5
    assert line_row(warped.width - 10) - line_row(middle) > 5
    # The crease is shaded: darker than the paper on one side of the line.
    assert pixels[20, middle - 15 : middle + 16].min() < 200


def test_warp_crease_shading_stays_on_the_kink_when_bends_move_it(degradations) -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    image = Image.new("L", (400, 300), 255)
    ImageDraw.Draw(image).line([(0, 150), (399, 150)], fill=0, width=3)
    # A vertical fold and a bend centred on it that shifts content sideways, across it.
    fold = [0.5, 0.5, 90.0, 0.2, 90.0, -0.5]
    bend = [0.5, 0.2, 0.5, 0.05, 0.0]
    params = {**_FLAT_WARP, "folds": 1, "fold_lines": fold, "bends": 1, "bend_shapes": bend}
    pixels = np.asarray(degradations.apply(image, "warp", params), dtype=float)

    # The V's tip: the column where the line's topmost dark pixel is highest.
    dark = pixels < 128
    tops = np.where(dark.any(axis=0), dark.argmax(axis=0), pixels.shape[0])
    kink = float(np.flatnonzero(tops == tops.min()).mean())
    crease = int(np.argmin(pixels[20]))  # the darkest pixel near the top, away from the line
    assert abs(crease - kink) <= 4


def test_warp_rejects_bad_groups_and_too_steep_fields(degradations, page) -> None:
    with pytest.raises(ValueError, match="fold_lines in groups of 6"):
        degradations.apply(page, "warp", {**_FLAT_WARP, "fold_lines": [0.5, 0.5]})
    with pytest.raises(ValueError, match=r"1 group\(s\) in fold_lines; expected 0"):
        fold = [0.5, 0.5, 0.0, 0.01, 0.0, 0.0]
        degradations.apply(page, "warp", {**_FLAT_WARP, "fold_lines": fold})
    for radius in (0.0, -0.3):
        with pytest.raises(ValueError, match="positive radius"):
            bend = [0.5, 0.5, radius, 0.01, 0.0]
            degradations.apply(page, "warp", {**_FLAT_WARP, "bends": 1, "bend_shapes": bend})
    with pytest.raises(ValueError, match="finite"):
        fold = [0.5, float("nan"), 0.0, 0.01, 0.0, 0.0]
        degradations.apply(page, "warp", {**_FLAT_WARP, "fold_lines": fold})
    for undulation in (-0.05, float("nan")):
        flattened = {**_FLAT_WARP, "undulation": undulation}
        with pytest.raises(ValueError, match="'undulation' is"):
            degradations.apply(page, "warp", flattened)
    steep = [0.5, 0.5, 0.0, 0.6, 90.0, 0.0] * 2
    with pytest.raises(ValueError, match="too steep"):
        degradations.apply(page, "warp", {**_FLAT_WARP, "folds": 2, "fold_lines": steep})


def test_lighting_darkens_towards_the_angle(degradations, page) -> None:
    lit = degradations.apply(page, "lighting", {"strength": 0.5, "angle": 0.0})

    assert lit.getpixel((0, 0)) == 255
    assert lit.getpixel((page.width - 1, 0)) == round(255 * 0.5)


# --- Generation (Verovio and cairosvg) ---------------------------------------------


def _renderer_unavailable(reason: str) -> None:
    # CI sets OMR_LAB_REQUIRE_SYNTHETIC so that a broken renderer fails, not skips.
    if os.environ.get("OMR_LAB_REQUIRE_SYNTHETIC"):
        pytest.fail(f"OMR_LAB_REQUIRE_SYNTHETIC is set, but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def renderer():
    try:
        import cairosvg  # noqa: F401
        import verovio  # noqa: F401
    except (ImportError, OSError) as exc:
        _renderer_unavailable(f"the synthetic extra cannot be imported: {exc}")
    from omr_lab.synthetic import render

    try:
        font = render.choose_text_font(("Liberation Serif", "Times New Roman", "DejaVu Serif"))
    except render.FontUnavailableError as exc:
        _renderer_unavailable(str(exc))
    return font


@pytest.fixture(scope="module")
def corpus(renderer, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from omr_lab.synthetic import generate_corpus

    root = tmp_path_factory.mktemp("corpus")
    document = _spec_document(text_fonts=[renderer.family])
    spec = load_corpus_spec(_write_spec(root / "spec", document))
    generate_corpus(spec, root / "out")
    return root


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def test_generated_manifest_records_sources_parameters_and_checksums(corpus: Path) -> None:
    out = corpus / "out"
    manifest = _manifest(out)
    validate_benchmark_manifest(manifest)
    assert manifest["schema_version"] == "0.2.0"
    assert "with seed 7" in manifest["description"]

    clean, worn = manifest["samples"]
    assert [clean["id"], worn["id"]] == ["twinkle-clean", "twinkle-worn"]
    # Clean and degraded variants share one ground truth, a copy of the source.
    assert clean["ground_truth"] == worn["ground_truth"] == "ground-truth/twinkle.musicxml"
    assert (out / clean["ground_truth"]).read_bytes() == TWINKLE.read_bytes()
    for sample in manifest["samples"]:
        assert (
            hashlib.sha256((out / sample["image"]).read_bytes()).hexdigest()
            == (sample["image_sha256"])
        )
        assert sample["license"] == "CC0-1.0"
        assert sample["renderer"].startswith("Verovio ")
    assert clean["synthesis"]["degradations"] == []
    assert worn["synthesis"]["seed"] == sample_seed(7, "twinkle", "worn")
    assert worn["synthesis"]["corpus_seed"] == 7
    assert [d["kind"] for d in worn["synthesis"]["degradations"]] == [
        item["kind"] for item in EVERY_KIND
    ]


def test_recorded_parameters_reproduce_the_degraded_image(corpus: Path) -> None:
    from PIL import Image

    from omr_lab.synthetic import degradations

    out = corpus / "out"
    clean, worn = _manifest(out)["samples"]
    with Image.open(out / clean["image"]) as image:
        rebuilt = image.convert("L")
    for step in worn["synthesis"]["degradations"]:
        rebuilt = degradations.apply(rebuilt, step["kind"], step["params"])
    with Image.open(out / worn["image"]) as image:
        assert image.tobytes() == rebuilt.tobytes()


def test_generation_is_deterministic_and_the_seed_matters(corpus: Path) -> None:
    from omr_lab.synthetic import generate_corpus

    spec = load_corpus_spec(corpus / "spec" / "corpus.json")
    generate_corpus(spec, corpus / "again")
    generate_corpus(spec, corpus / "reseeded", seed=8)

    first, again, reseeded = (_manifest(corpus / d) for d in ("out", "again", "reseeded"))
    assert again == first
    assert reseeded["samples"][0]["image_sha256"] == first["samples"][0]["image_sha256"]
    assert reseeded["samples"][1]["image_sha256"] != first["samples"][1]["image_sha256"]


def test_generated_manifest_runs_as_a_benchmark(corpus: Path) -> None:
    manifest = load_manifest(corpus / "out" / "manifest.json")

    result = run_benchmark(manifest, corpus / "bench", MockEngine(), timeout_seconds=5)

    assert result.report["manifest"]["schema_version"] == "0.2.0"
    assert result.report["summary"]["succeeded"] == 2


def test_source_that_is_not_musicxml_is_rejected_before_writing(renderer, tmp_path: Path) -> None:
    from omr_lab.synthetic import SourceError, generate_corpus

    spec_path = _write_spec(tmp_path / "spec", _spec_document(text_fonts=[renderer.family]))
    (tmp_path / "spec" / "twinkle.musicxml").write_text("<html/>", encoding="utf-8")

    with pytest.raises(SourceError, match="twinkle"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_compressed_source_is_rejected_before_writing(renderer, tmp_path: Path) -> None:
    import zipfile

    from omr_lab.synthetic import SourceError, generate_corpus

    spec_path = _write_spec(tmp_path / "spec", _spec_document(text_fonts=[renderer.family]))
    with zipfile.ZipFile(tmp_path / "spec" / "twinkle.musicxml", "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>',
        )
        archive.write(TWINKLE, "score.musicxml")

    with pytest.raises(SourceError, match="compressed"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_source_longer_than_one_page_is_rejected_before_writing(
    renderer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omr_lab.synthetic import SourceError, generate_corpus, render

    # A short page instead of a long score: twinkle then needs two pages.
    monkeypatch.setitem(render.VEROVIO_OPTIONS, "adjustPageHeight", False)
    monkeypatch.setitem(render.VEROVIO_OPTIONS, "pageHeight", 700)
    spec_path = _write_spec(tmp_path / "spec", _spec_document(text_fonts=[renderer.family]))

    with pytest.raises(SourceError, match="renders to [0-9]+ pages"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_source_that_is_not_utf8_is_rejected_before_writing(renderer, tmp_path: Path) -> None:
    from omr_lab.synthetic import SourceError, generate_corpus

    spec_path = _write_spec(tmp_path / "spec", _spec_document(text_fonts=[renderer.family]))
    source = tmp_path / "spec" / "twinkle.musicxml"
    text = TWINKLE.read_text(encoding="utf-8").replace("Twinkle, Twinkle", "Schöner Stern")
    text = text.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"', 1)
    source.write_bytes(text.encode("latin-1"))
    assert load_musicxml(source).container == "musicxml"  # valid XML, just not UTF-8

    with pytest.raises(SourceError, match="is not UTF-8"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_utf8_source_with_bom_is_rendered(renderer, tmp_path: Path) -> None:
    from omr_lab.synthetic import generate_corpus

    document = _spec_document(text_fonts=[renderer.family])
    document["variants"] = document["variants"][:1]
    spec_path = _write_spec(tmp_path / "spec", document)
    source = tmp_path / "spec" / "twinkle.musicxml"
    source.write_bytes(b"\xef\xbb\xbf" + TWINKLE.read_bytes())

    result = generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")

    # The ground truth is the source file unchanged, BOM included.
    sample = result.manifest["samples"][0]
    assert (tmp_path / "out" / sample["ground_truth"]).read_bytes() == source.read_bytes()


def test_font_names_reach_fc_match_as_literals(renderer, monkeypatch: pytest.MonkeyPatch) -> None:
    from omr_lab.engines.base import CommandResult
    from omr_lab.synthetic import render

    calls = []

    def fake_run_command(args, *, timeout_seconds):
        calls.append(list(args))
        return CommandResult(args=tuple(args), returncode=0, stdout="Foo-Bold:Italic")

    monkeypatch.setattr(render, "run_command", fake_run_command)

    assert render.choose_text_font(("Foo-Bold:Italic",)).family == "Foo-Bold:Italic"
    assert calls[0][-1] == "Foo\\-Bold\\:Italic"


def test_fonts_are_never_used_unchecked(renderer, monkeypatch: pytest.MonkeyPatch) -> None:
    from omr_lab.synthetic import render

    monkeypatch.setattr(render.shutil, "which", lambda name: None)

    with pytest.raises(render.FontUnavailableError, match="fontconfig"):
        render.choose_text_font((renderer.family,))


def test_missing_text_font_is_a_dependency_error(renderer, tmp_path: Path) -> None:
    from omr_lab.synthetic import SyntheticDependencyError, generate_corpus

    spec_path = _write_spec(tmp_path, _spec_document(text_fonts=["No Such Font Omr Lab"]))

    with pytest.raises(SyntheticDependencyError, match="No Such Font Omr Lab"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")


def test_text_font_without_the_lyric_characters_is_a_dependency_error(
    renderer, tmp_path: Path
) -> None:
    from omr_lab.synthetic import SyntheticDependencyError, generate_corpus

    # The renderer fixture picks a Latin serif font, which has no CJK characters.
    spec_path = _write_spec(tmp_path, _spec_document(text_fonts=[renderer.family]))
    source = tmp_path / "twinkle.musicxml"
    source.write_text(
        source.read_text(encoding="utf-8").replace("<text>Twin</text>", "<text>雨</text>", 1),
        encoding="utf-8",
    )

    with pytest.raises(SyntheticDependencyError, match=r"lacks 雨 \(U\+96E8\)"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_music_symbols_in_text_are_drawn_in_the_music_font(renderer) -> None:
    import cairosvg
    import numpy as np
    from PIL import Image

    from omr_lab.synthetic import render

    # The fixture's D/F# chord symbol and metronome mark put SMuFL characters in text.
    svg = render.render_svg(SCORE_FEATURES.read_text(encoding="utf-8"), SCORE_FEATURES.name)
    assert 'font-family="Leipzig"' in svg
    characters = render.text_characters(svg)
    assert {"D", "F", "M"} <= characters
    assert not [c for c in characters if 0xE000 <= ord(c) <= 0xF8FF]

    render.register_music_font()

    def draw(family: str) -> np.ndarray:
        sharp = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">'
            f'<text x="20" y="60" font-family="{family}" font-size="48">&#xE262;</text></svg>'
        )
        png = cairosvg.svg2png(bytestring=sharp.encode(), background_color="white")
        with Image.open(io.BytesIO(png)) as image:
            return np.asarray(image.convert("L"))

    # Without the font, cairosvg draws the same placeholder box for any family.
    assert (draw(render.MUSIC_FONT) != draw("No Such Font Omr Lab")).any()


def test_rasterize_sets_the_text_font_on_every_default_attribute(
    renderer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omr_lab.synthetic import render

    drawn = []
    svg2png = render.cairosvg.svg2png

    def fake_svg2png(*, bytestring, **kwargs):
        drawn.append(bytestring.decode("utf-8"))
        return svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>')

    monkeypatch.setattr(render.cairosvg, "svg2png", fake_svg2png)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" font-family="Times, serif">'
        '<g font-family="Times, serif"><text>a</text></g></svg>'
    )

    render.rasterize(svg, render.TextFont("Foo Serif"))

    assert "Times, serif" not in drawn[0]
    assert drawn[0].count('font-family="Foo Serif"') == 2


class _StubToolkit:
    def __init__(self, svg: str) -> None:
        self.svg = svg

    def setOptions(self, options) -> None:  # noqa: N802 - Verovio's API
        pass

    def loadData(self, data: str) -> bool:  # noqa: N802
        return True

    def getPageCount(self) -> int:  # noqa: N802
        return 1

    def getVersion(self) -> str:  # noqa: N802
        return "stub"

    def renderToSVG(self, page: int) -> str:  # noqa: N802
        return self.svg


@pytest.mark.parametrize(
    ("svg", "message"),
    [
        (
            '<svg font-family="Times, serif"><tspan font-family="Arial">a</tspan></svg>',
            "uses fonts Arial",
        ),
        ('<svg font-family="Helvetica"><text>a</text></svg>', "no longer writes"),
    ],
)
def test_render_svg_rejects_fonts_it_cannot_set_or_draw(
    renderer, monkeypatch: pytest.MonkeyPatch, svg: str, message: str
) -> None:
    from omr_lab.synthetic import render

    monkeypatch.setattr(render.verovio, "toolkit", lambda: _StubToolkit(svg))

    with pytest.raises(render.RenderError, match=message):
        render.render_svg("<score-partwise/>", "stub.musicxml")


# --- CLI ---------------------------------------------------------------------------


def test_cli_rejects_an_invalid_specification(tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path, _spec_document(seed=-1))

    assert main(["synthesize", str(spec_path), "-o", str(tmp_path / "out")]) == (EXIT_INVALID_INPUT)
    assert "seed" in capsys.readouterr().err


def test_cli_rejects_a_bad_seed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["synthesize", "spec.json", "-o", str(tmp_path), "--seed", "-1"])
    assert exit_info.value.code == EXIT_USAGE


def test_cli_reports_a_missing_font(renderer, tmp_path: Path, capsys) -> None:
    spec_path = _write_spec(tmp_path, _spec_document(text_fonts=["No Such Font Omr Lab"]))

    code = main(["synthesize", str(spec_path), "-o", str(tmp_path / "out")])

    assert code == EXIT_ENGINE_UNAVAILABLE
    assert "No Such Font Omr Lab" in capsys.readouterr().err


def test_cli_writes_a_corpus_and_refuses_a_used_directory(renderer, tmp_path: Path, capsys) -> None:
    document = _spec_document(text_fonts=[renderer.family])
    document["variants"] = document["variants"][:1]
    spec_path = _write_spec(tmp_path / "spec", document)
    out = tmp_path / "out"

    assert main(["synthesize", str(spec_path), "-o", str(out)]) == EXIT_OK
    assert "Samples: 1" in capsys.readouterr().out
    assert main(["synthesize", str(spec_path), "-o", str(out)]) == EXIT_OUTPUT_DIR_UNUSABLE


@pytest.mark.parametrize("pre_existing", [False, True])
def test_cli_interrupt_before_writing_does_not_call_the_directory_partial(
    renderer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, pre_existing: bool
) -> None:
    from omr_lab.synthetic import render

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(render, "render_svg", interrupted)
    spec_path = _write_spec(tmp_path / "spec", _spec_document(text_fonts=[renderer.family]))
    out = tmp_path / "out"
    if pre_existing:
        out.mkdir()
        (out / "mine.txt").write_text("user file", encoding="utf-8")

    assert main(["synthesize", str(spec_path), "-o", str(out)]) == EXIT_INTERRUPTED

    err = capsys.readouterr().err
    assert "Nothing was written" in err and "Remove" not in err


def test_cli_interrupt_after_writing_started_says_to_remove_the_directory(
    renderer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from omr_lab.synthetic import degradations

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(degradations, "apply", interrupted)
    spec_path = _write_spec(tmp_path / "spec", _spec_document(text_fonts=[renderer.family]))
    out = tmp_path / "out"

    assert main(["synthesize", str(spec_path), "-o", str(out)]) == EXIT_INTERRUPTED

    assert f"Remove {out}" in capsys.readouterr().err


def test_cli_explains_how_to_install_missing_rendering_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import sys

    # None in sys.modules makes the import fail, as if the extra were not installed.
    monkeypatch.setitem(sys.modules, "verovio", None)
    monkeypatch.delitem(sys.modules, "omr_lab.synthetic.render", raising=False)
    monkeypatch.delattr(synthetic, "render", raising=False)
    spec_path = _write_spec(tmp_path, _spec_document())

    code = main(["synthesize", str(spec_path), "-o", str(tmp_path / "out")])

    assert code == EXIT_ENGINE_UNAVAILABLE
    assert "uv sync --extra synthetic" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# --- Manifest schema 0.2.0 ---------------------------------------------------------


def _manifest_sample(**extra: object) -> dict:
    return {
        "id": "a",
        "image": "a.png",
        "image_sha256": "0" * 64,
        "source": "unit",
        "license": "CC0-1.0",
        "condition": "clean",
        **extra,
    }


_SYNTHESIS = {
    "corpus": "unit",
    "corpus_seed": 7,
    "source_id": "a",
    "variant_id": "clean",
    "seed": 1,
    "degradations": [{"kind": "rotate", "params": {"degrees": 1.5}}],
}


def test_synthesis_is_valid_in_manifest_0_2_0_only() -> None:
    from omr_lab.schemas.benchmark import BenchmarkDocumentValidationError

    sample = _manifest_sample(synthesis=_SYNTHESIS)
    validate_benchmark_manifest({"schema_version": "0.2.0", "samples": [sample]})
    with pytest.raises(BenchmarkDocumentValidationError, match="synthesis"):
        validate_benchmark_manifest({"schema_version": "0.1.0", "samples": [sample]})
    validate_benchmark_manifest({"schema_version": "0.1.0", "samples": [_manifest_sample()]})


def test_synthesis_needs_its_seed_and_resolved_numbers() -> None:
    from omr_lab.schemas.benchmark import BenchmarkDocumentValidationError

    no_seed = {key: value for key, value in _SYNTHESIS.items() if key != "seed"}
    no_corpus_seed = {key: value for key, value in _SYNTHESIS.items() if key != "corpus_seed"}
    ranged = {**_SYNTHESIS, "degradations": [{"kind": "rotate", "params": {"degrees": {}}}]}
    too_big = {**_SYNTHESIS, "seed": 2**53}
    for synthesis in (no_seed, no_corpus_seed, ranged, too_big):
        document = {"schema_version": "0.2.0", "samples": [_manifest_sample(synthesis=synthesis)]}
        with pytest.raises(BenchmarkDocumentValidationError):
            validate_benchmark_manifest(document)
