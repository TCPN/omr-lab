"""Optional image preprocessing (docs/preprocessing.md): steps, pipeline, CLI and benchmark.

The transforms need the ``preprocess`` extra. Tests that run them skip without it,
unless OMR_LAB_REQUIRE_PREPROCESS is set (as in CI), where a missing extra fails.
Test images are drawn here; no engine, Java or network is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from omr_lab.benchmark import load_manifest, render_markdown, run_benchmark
from omr_lab.cli import EXIT_ENGINE_UNAVAILABLE, EXIT_OK, EXIT_RUN_FAILED, EXIT_USAGE, main
from omr_lab.engines import EngineOptions, EngineResult
from omr_lab.engines.mock import MockEngine
from omr_lab.pipeline import (
    PREPROCESSED_IMAGE_KIND,
    RunErrorCode,
    run_pipeline,
)
from omr_lab.preprocess import (
    INSTALL_HINT,
    STEP_NAMES,
    PreprocessingError,
    PreprocessingUnavailableError,
    parse_steps,
    preprocess_image,
)
from omr_lab.schemas import validate_run_document
from omr_lab.schemas.benchmark import validate_benchmark_report


def _require_opencv() -> tuple[object, object]:
    try:
        import cv2
        import numpy
    except ImportError as exc:
        reason = f"the preprocess extra is not installed ({exc.name})"
        # CI sets OMR_LAB_REQUIRE_PREPROCESS so that a broken extra fails, not skips.
        if os.environ.get("OMR_LAB_REQUIRE_PREPROCESS"):
            pytest.fail(f"OMR_LAB_REQUIRE_PREPROCESS is set, but {reason}")
        pytest.skip(reason)
    return cv2, numpy


@pytest.fixture
def no_opencv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import cv2`` fail, as without the preprocess extra."""
    import omr_lab.preprocess as package

    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.delitem(sys.modules, "omr_lab.preprocess.steps", raising=False)
    monkeypatch.delattr(package, "steps", raising=False)


@pytest.fixture
def cv():
    cv2, np = _require_opencv()
    return cv2, np


def _staff_page(np, width: int = 1200, height: int = 800):
    """White page with two five-line staves and some note heads (grayscale)."""
    page = np.full((height, width), 255, np.uint8)
    for top in (200, 500):
        for line in range(5):
            y = top + line * 14
            page[y : y + 2, 80 : width - 80] = 0
        for x in range(150, width - 120, 90):
            page[top + 20 : top + 30, x : x + 14] = 0
    return page


def _rotated(cv2, np, image, degrees: float):
    from omr_lab.preprocess.steps import rotate

    return rotate(image, degrees)


def _on_background(cv2, np, page, corners, size=(1800, 1400), background=90):
    """Warp ``page`` onto a darker textured background at the given corner positions."""
    height, width = page.shape
    rng = np.random.default_rng(0)
    canvas = np.clip(rng.normal(background, 6, (size[1], size[0])), 0, 255).astype(np.uint8)
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    transform = cv2.getPerspectiveTransform(source, np.float32(corners))
    warped = cv2.warpPerspective(page, transform, size, borderValue=0)
    mask = cv2.warpPerspective(np.full_like(page, 255), transform, size, borderValue=0)
    return np.where(mask > 127, warped, canvas).astype(np.uint8)


def _write_png(cv2, path: Path, image) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(data.tobytes())
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- step names --------------------------------------------------------------------------


def test_parse_steps_keeps_order_and_rejects_unknown_or_repeated_steps() -> None:
    assert parse_steps("page, deskew") == ("page", "deskew")
    assert parse_steps(["threshold", "grayscale"]) == ("threshold", "grayscale")
    assert parse_steps("") == ()
    with pytest.raises(ValueError, match="Unknown preprocessing step.*'dewarp'.*Available"):
        parse_steps("page,dewarp")
    with pytest.raises(ValueError, match="only once; repeated: page"):
        parse_steps(["page", "deskew", "page"])


def test_step_names_match_the_implemented_steps(cv) -> None:
    from omr_lab.preprocess.steps import STEPS

    assert tuple(STEPS) == STEP_NAMES


def test_missing_opencv_is_reported_with_the_install_hint(tmp_path: Path, no_opencv: None) -> None:
    output = tmp_path / "out.png"
    with pytest.raises(PreprocessingUnavailableError) as excinfo:
        preprocess_image(tmp_path / "in.png", output, ["grayscale"])
    assert INSTALL_HINT in str(excinfo.value)
    assert not output.exists()


# --- the transforms ----------------------------------------------------------------------


def test_grayscale_converts_colour_and_leaves_grayscale_alone(cv) -> None:
    _, np = cv
    from omr_lab.preprocess.steps import STEPS

    colour = np.zeros((10, 12, 3), np.uint8)
    colour[:, :, 2] = 200
    outcome = STEPS["grayscale"].run(colour, STEPS["grayscale"].params)
    assert outcome.applied and outcome.image.shape == (10, 12)
    again = STEPS["grayscale"].run(outcome.image, STEPS["grayscale"].params)
    assert not again.applied and again.message == "The image is already grayscale."


@pytest.mark.parametrize("degrees", [-6.0, -1.5, 2.5, 8.0])
def test_deskew_measures_and_undoes_a_known_rotation(cv, degrees: float) -> None:
    cv2, np = cv
    from omr_lab.preprocess.steps import STEPS, estimate_skew

    tilted = _rotated(cv2, np, _staff_page(np), degrees)
    step = STEPS["deskew"]
    outcome = step.run(tilted, step.params)
    assert outcome.applied
    # rotate() turns counter-clockwise, so undoing it is the opposite angle.
    assert outcome.result["angle_degrees"] == pytest.approx(-degrees, abs=0.15)
    assert abs(estimate_skew(outcome.image, 15, 1200)) < 0.15


def test_deskew_leaves_a_straight_image_unchanged(cv) -> None:
    _, np = cv
    from omr_lab.preprocess.steps import STEPS

    page = _staff_page(np)
    outcome = STEPS["deskew"].run(page, STEPS["deskew"].params)
    assert not outcome.applied
    assert outcome.image is page
    assert outcome.result == {"angle_degrees": 0.0}
    assert "below 0.1 degrees" in outcome.message


def test_deskew_does_not_trust_an_angle_at_the_search_limit(cv) -> None:
    cv2, np = cv
    from omr_lab.preprocess.steps import deskew

    tilted = _rotated(cv2, np, _staff_page(np), 7.0)
    params = {"detection_size": 1200, "max_degrees": 5, "min_degrees": 0.1}
    outcome = deskew(tilted, params)
    assert not outcome.applied
    assert outcome.image is tilted
    assert "search limit" in outcome.message


def test_page_finds_the_sheet_corrects_perspective_and_crops(cv) -> None:
    cv2, np = cv
    from omr_lab.preprocess.steps import STEPS, estimate_skew

    corners = [[300, 250], [1450, 330], [1400, 1150], [250, 1060]]
    photo = _on_background(cv2, np, _staff_page(np), corners)
    step = STEPS["page"]
    outcome = step.run(photo, step.params)
    assert outcome.applied, outcome.message
    found = np.array(outcome.result["corners"])
    assert np.abs(found - np.array(corners)).max() < 12
    assert outcome.result["contrast"] >= step.params["min_contrast"]
    width, height = outcome.result["output_size"]
    assert outcome.image.shape == (height, width)
    # The background is gone and the staves are level again.
    assert np.median(outcome.image) == 255
    assert abs(estimate_skew(outcome.image, 15, 1200)) < 0.3


def test_page_without_a_background_is_left_unchanged(cv) -> None:
    _, np = cv
    from omr_lab.preprocess.steps import STEPS

    page = _staff_page(np)
    outcome = STEPS["page"].run(page, STEPS["page"].params)
    assert not outcome.applied
    assert outcome.image is page
    assert outcome.message


def test_page_on_a_background_as_bright_as_the_page_is_left_unchanged(cv) -> None:
    cv2, np = cv
    from omr_lab.preprocess.steps import STEPS

    corners = [[300, 250], [1450, 330], [1400, 1150], [250, 1060]]
    photo = _on_background(cv2, np, _staff_page(np), corners, background=225)
    outcome = STEPS["page"].run(photo, STEPS["page"].params)
    assert not outcome.applied
    assert outcome.image is photo
    assert "grey levels brighter" in outcome.message


def test_threshold_writes_only_black_and_white(cv) -> None:
    cv2, np = cv
    from omr_lab.preprocess.steps import STEPS

    page = _staff_page(np)
    # A lighting gradient that a single global threshold would not handle.
    shaded = (page.astype(np.float64) * np.linspace(0.55, 1.0, page.shape[1])).astype(np.uint8)
    outcome = STEPS["threshold"].run(shaded, STEPS["threshold"].params)
    assert outcome.applied
    assert set(np.unique(outcome.image)) <= {0, 255}
    assert outcome.result["block_size"] % 2 == 1
    # Staff lines stay black and the shaded paper becomes white.
    assert outcome.image[200, 600] == 0
    assert outcome.image[100, 200] == 255


# --- preprocess_image ----------------------------------------------------------------------


def test_preprocess_image_writes_a_new_file_and_leaves_the_input_unchanged(
    cv, tmp_path: Path
) -> None:
    cv2, np = cv
    source = _write_png(cv2, tmp_path / "in" / "score.png", _rotated(cv2, np, _staff_page(np), 3))
    before = _sha256(source)
    output = tmp_path / "out.png"

    result = preprocess_image(source, output, ["deskew", "threshold"])

    assert _sha256(source) == before
    assert result.output_path == output
    assert not result.orientation_applied
    assert [step.name for step in result.steps] == ["deskew", "threshold"]
    assert result.steps[0].applied and result.steps[0].result["angle_degrees"] == pytest.approx(
        -3, abs=0.15
    )
    assert result.steps[0].params == {"detection_size": 1200, "max_degrees": 15, "min_degrees": 0.1}
    written = cv2.imread(str(output), cv2.IMREAD_UNCHANGED)
    assert written.shape == (result.height, result.width)
    with pytest.raises(FileExistsError):
        preprocess_image(source, output, ["grayscale"])


def test_transparent_pixels_become_white(cv, tmp_path: Path) -> None:
    cv2, np = cv
    rgba = np.zeros((20, 30, 4), np.uint8)  # fully transparent black
    rgba[5:10, 5:10] = (0, 0, 0, 255)  # one opaque black square
    source = _write_png(cv2, tmp_path / "alpha.png", rgba)
    preprocess_image(source, tmp_path / "out.png", ["grayscale"])
    out = cv2.imread(str(tmp_path / "out.png"), cv2.IMREAD_UNCHANGED)
    assert out.shape == (20, 30)
    assert out[0, 0] == 255 and out[7, 7] == 0


def _with_exif_orientation(jpeg: bytes, orientation: int) -> bytes:
    """Insert an EXIF segment holding only the Orientation tag after the JPEG's SOI marker."""
    import struct

    tiff = b"MM\x00*\x00\x00\x00\x08" + struct.pack(">H", 1)
    tiff += struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0) + struct.pack(">I", 0)
    app1 = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpeg[2:]


def test_exif_orientation_is_applied_before_the_steps(cv, tmp_path: Path) -> None:
    cv2, np = cv
    # Stored sideways (landscape) with Orientation=6: the photo is upright when turned
    # 90 degrees clockwise, as phone cameras write a portrait shot.
    stored = np.full((100, 200), 255, np.uint8)
    stored[0:20, 0:20] = 0  # stored top-left corner
    ok, jpeg = cv2.imencode(".jpg", stored)
    assert ok
    source = tmp_path / "phone.jpg"
    source.write_bytes(_with_exif_orientation(jpeg.tobytes(), 6))

    result = preprocess_image(source, tmp_path / "out.png", ["grayscale"])

    out = cv2.imread(str(tmp_path / "out.png"), cv2.IMREAD_UNCHANGED)
    assert result.orientation_applied
    assert (result.width, result.height) == (100, 200)
    assert out.shape == (200, 100)
    # Turned clockwise, the stored top-left corner is now top-right.
    assert out[5, 95] < 64 and out[5, 5] > 192


def test_the_run_document_records_an_applied_orientation(cv, tmp_path: Path) -> None:
    cv2, np = cv
    ok, jpeg = cv2.imencode(".jpg", _staff_page(np))
    assert ok
    source = tmp_path / "phone.jpg"
    source.write_bytes(_with_exif_orientation(jpeg.tobytes(), 6))
    document = run_pipeline(
        source, tmp_path / "run", MockEngine(), preprocess=["grayscale"]
    ).document
    validate_run_document(document)
    assert document["preprocessing"]["orientation_applied"] is True
    assert document["preprocessing"]["steps"][0]["applied"] is False


def test_transparent_images_follow_the_orientation_of_their_colour(cv) -> None:
    cv2, np = cv
    from omr_lab.preprocess import steps

    rgba = np.full((20, 30, 4), (255, 255, 255, 0), np.uint8)  # transparent white
    rgba[0:5, 0:5] = (0, 0, 0, 255)  # opaque black in the stored top-left corner
    ok, png = cv2.imencode(".png", rgba)
    assert ok
    original = cv2.imdecode

    # OpenCV applies no orientation to PNG; stand in for a format that does, by turning
    # only the decode that applies it (180 degrees keeps the shape).
    def turned(buffer, flags):
        image = original(buffer, flags)
        return image if flags == cv2.IMREAD_UNCHANGED else np.ascontiguousarray(image[::-1, ::-1])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(steps.cv2, "imdecode", turned)
    try:
        image, applied = steps.load_image(png.tobytes())
    finally:
        monkeypatch.undo()
    assert applied
    assert image.shape == (20, 30, 3)
    assert image[17, 27].tolist() == [0, 0, 0] and image[0, 0].tolist() == [255, 255, 255]


def test_pdf_and_undecodable_inputs_are_rejected(cv, tmp_path: Path) -> None:
    pdf = tmp_path / "score.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(PreprocessingError, match="single images, not .pdf files"):
        preprocess_image(pdf, tmp_path / "a.png", ["grayscale"])
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    with pytest.raises(PreprocessingError, match="cannot decode 'broken.png'"):
        preprocess_image(broken, tmp_path / "b.png", ["grayscale"])
    assert not (tmp_path / "a.png").exists() and not (tmp_path / "b.png").exists()


# --- pipeline --------------------------------------------------------------------------------


class _RecordingEngine(MockEngine):
    """The mock engine, remembering which file it was asked to read."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[Path] = []

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        self.inputs.append(input_path)
        return super().recognize(
            input_path, output_dir, timeout_seconds=timeout_seconds, options=options
        )


def test_pipeline_gives_the_engine_a_preprocessed_copy_and_records_it(cv, tmp_path: Path) -> None:
    cv2, np = cv
    source = _write_png(cv2, tmp_path / "in" / "score.png", _rotated(cv2, np, _staff_page(np), 2))
    before = _sha256(source)
    engine = _RecordingEngine()

    result = run_pipeline(source, tmp_path / "run", engine, preprocess=["grayscale", "deskew"])

    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    validate_run_document(document)
    assert result.succeeded
    assert _sha256(source) == before
    assert document["input"]["sha256"] == before
    assert engine.inputs == [tmp_path / "run" / "preprocessed" / "input.png"]
    assert document["artifacts"][PREPROCESSED_IMAGE_KIND] == "preprocessed/input.png"
    record = document["preprocessing"]
    assert record["requested"] == ["grayscale", "deskew"]
    assert record["orientation_applied"] is False
    assert [(step["name"], step["applied"]) for step in record["steps"]] == [
        ("grayscale", False),
        ("deskew", True),
    ]
    assert record["steps"][1]["result"]["angle_degrees"] == pytest.approx(-2, abs=0.15)
    assert record["output"] == "preprocessed/input.png"
    assert record["output_sha256"] == _sha256(tmp_path / "run" / "preprocessed" / "input.png")
    assert record["duration_seconds"] >= 0


def test_pipeline_without_preprocessing_records_null(tmp_path: Path) -> None:
    source = tmp_path / "score.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    engine = _RecordingEngine()
    result = run_pipeline(source, tmp_path / "run", engine, preprocess=[])
    assert result.document["preprocessing"] is None
    assert engine.inputs == [source]
    assert not (tmp_path / "run" / "preprocessed").exists()


def test_failed_preprocessing_fails_the_run_before_the_engine(cv, tmp_path: Path) -> None:
    pdf = tmp_path / "score.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    engine = _RecordingEngine()

    result = run_pipeline(pdf, tmp_path / "run", engine, preprocess=["deskew"])

    document = result.document
    validate_run_document(document)
    assert not result.succeeded
    assert engine.inputs == []
    assert document["error"]["code"] == RunErrorCode.PREPROCESSING_FAILED
    assert "not .pdf files" in document["error"]["message"]
    assert document["preprocessing"] == {
        "requested": ["deskew"],
        "orientation_applied": None,
        "steps": [],
        "output": None,
        "output_sha256": None,
        "duration_seconds": document["preprocessing"]["duration_seconds"],
    }
    assert PREPROCESSED_IMAGE_KIND not in document["artifacts"]


def test_missing_extra_fails_the_run_with_the_install_hint(tmp_path: Path, no_opencv: None) -> None:
    source = tmp_path / "score.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    result = run_pipeline(source, tmp_path / "run", MockEngine(), preprocess=["page"])
    assert result.document["error"]["code"] == RunErrorCode.PREPROCESSING_FAILED
    assert INSTALL_HINT in result.document["error"]["message"]


def test_unknown_step_raises_before_the_output_directory_is_created(tmp_path: Path) -> None:
    source = tmp_path / "score.png"
    source.write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="Unknown preprocessing step"):
        run_pipeline(source, tmp_path / "run", MockEngine(), preprocess=["dewarp"])
    assert not (tmp_path / "run").exists()


def test_an_engine_artifact_cannot_take_the_preprocessed_image_kind(cv, tmp_path: Path) -> None:
    cv2, np = cv
    source = _write_png(cv2, tmp_path / "in" / "score.png", _staff_page(np))

    class _Claiming(MockEngine):
        def recognize(self, input_path, output_dir, *, timeout_seconds, options=None):
            result = super().recognize(
                input_path, output_dir, timeout_seconds=timeout_seconds, options=options
            )
            extra = output_dir / "other.png"
            extra.write_bytes(b"x")
            return EngineResult(
                engine_name=result.engine_name,
                success=result.success,
                artifacts={**result.artifacts, PREPROCESSED_IMAGE_KIND: extra},
                version=result.version,
                options=result.options,
            )

    document = run_pipeline(
        source, tmp_path / "run", _Claiming(), preprocess=["grayscale"]
    ).document
    assert document["artifacts"][PREPROCESSED_IMAGE_KIND] == "preprocessed/input.png"
    assert document["artifacts"][f"{PREPROCESSED_IMAGE_KIND}_2"] == "raw/other.png"


# --- CLI and benchmark -----------------------------------------------------------------------


def _exit_code(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code  # type: ignore[return-value]


def test_cli_rejects_unknown_or_empty_steps(tmp_path: Path) -> None:
    image = tmp_path / "score.png"
    image.write_bytes(b"\x89PNG")
    run = ["run", str(image), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert _exit_code([*run, "--preprocess", "dewarp"]) == EXIT_USAGE
    assert _exit_code([*run, "--preprocess", ""]) == EXIT_USAGE
    assert not (tmp_path / "out").exists()


def test_cli_without_the_extra_exits_before_running(
    tmp_path: Path, no_opencv: None, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "score.png"
    image.write_bytes(b"\x89PNG")
    argv = ["run", str(image), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert _exit_code([*argv, "--preprocess", "page"]) == EXIT_ENGINE_UNAVAILABLE
    assert INSTALL_HINT in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_run_with_preprocessing(cv, tmp_path: Path) -> None:
    cv2, np = cv
    image = _write_png(cv2, tmp_path / "score.png", _staff_page(np))
    argv = ["run", str(image), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert _exit_code([*argv, "--preprocess", "grayscale,threshold"]) == EXIT_OK
    document = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    assert document["preprocessing"]["requested"] == ["grayscale", "threshold"]


def test_cli_preprocessing_failure_exits_as_a_failed_run(cv, tmp_path: Path) -> None:
    pdf = tmp_path / "score.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    argv = ["run", str(pdf), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert _exit_code([*argv, "--preprocess", "deskew"]) == EXIT_RUN_FAILED


def _manifest(cv2, np, base: Path) -> Path:
    samples = []
    for sample_id, degrees in (("level", 0.0), ("tilted", 3.0)):
        path = _write_png(
            cv2, base / "images" / f"{sample_id}.png", _rotated(cv2, np, _staff_page(np), degrees)
        )
        samples.append(
            {
                "id": sample_id,
                "image": f"images/{sample_id}.png",
                "image_sha256": _sha256(path),
                "source": "drawn by the unit test",
                "license": "CC0-1.0",
                "condition": "synthetic staves",
            }
        )
    manifest = base / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "0.1.0", "samples": samples}), "utf-8")
    return manifest


def test_benchmark_records_requested_and_applied_steps(cv, tmp_path: Path) -> None:
    cv2, np = cv
    manifest = load_manifest(_manifest(cv2, np, tmp_path / "set"))
    images = {p: _sha256(p) for p in (tmp_path / "set" / "images").iterdir()}

    result = run_benchmark(
        manifest, tmp_path / "out", MockEngine(), timeout_seconds=60, preprocess=["deskew"]
    )

    report = result.report
    validate_benchmark_report(report)
    assert report["preprocessing"] == {"requested": ["deskew"]}
    by_id = {entry["id"]: entry for entry in report["samples"]}
    assert by_id["level"]["run"]["preprocessing"] == {"deskew": False}
    assert by_id["tilted"]["run"]["preprocessing"] == {"deskew": True}
    assert {p: _sha256(p) for p in images} == images
    markdown = render_markdown(report)
    assert "- Preprocessing: deskew" in markdown
    assert "| Preprocessing applied |" in markdown
    assert "| tilted | succeeded | synthetic staves |" in markdown


def test_benchmark_without_preprocessing_records_null(tmp_path: Path) -> None:
    base = tmp_path / "set"
    (base / "images").mkdir(parents=True)
    (base / "images" / "a.png").write_bytes(b"\x89PNG a")
    sample = {
        "id": "a",
        "image": "images/a.png",
        "image_sha256": _sha256(base / "images" / "a.png"),
        "source": "test bytes",
        "license": "CC0-1.0",
        "condition": "not a real image",
    }
    (base / "manifest.json").write_text(
        json.dumps({"schema_version": "0.1.0", "samples": [sample]}), "utf-8"
    )
    report = run_benchmark(
        load_manifest(base / "manifest.json"), tmp_path / "out", MockEngine(), timeout_seconds=60
    ).report
    assert report["preprocessing"] is None
    assert report["samples"][0]["run"]["preprocessing"] is None
    assert "Preprocessing" not in render_markdown(report)
    old = {**report, "schema_version": "0.2.0"}
    del old["preprocessing"]
    old["samples"] = [
        {**entry, "run": {k: v for k, v in entry["run"].items() if k != "preprocessing"}}
        for entry in report["samples"]
    ]
    validate_benchmark_report(old)


def test_benchmark_rejects_unknown_steps_before_writing(tmp_path: Path) -> None:
    base = tmp_path / "set"
    base.mkdir()
    sample = {
        "id": "a",
        "image": "a.png",
        "image_sha256": "0" * 64,
        "source": "none",
        "license": "CC0-1.0",
        "condition": "none",
    }
    (base / "manifest.json").write_text(
        json.dumps({"schema_version": "0.1.0", "samples": [sample]}), "utf-8"
    )
    with pytest.raises(ValueError, match="Unknown preprocessing step"):
        run_benchmark(
            load_manifest(base / "manifest.json"),
            tmp_path / "out",
            MockEngine(),
            timeout_seconds=60,
            preprocess=["blur"],
        )
    assert not (tmp_path / "out").exists()
