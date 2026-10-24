import copy
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from omr_lab import synthetic
from omr_lab.benchmark import load_manifest, run_benchmark
from omr_lab.cli import (
    EXIT_ENGINE_UNAVAILABLE,
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
        (["variants", 1, "degradations", 0, "kind"], "warp", "kind"),
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
    assert set(schema_kinds["enum"]) == set(DEGRADATION_PARAMETERS) == set(degradations._APPLY)


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
    with pytest.raises(ValueError, match="Unknown degradation kind 'warp'"):
        degradations.apply(page, "warp", {})


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


def test_lighting_darkens_towards_the_angle(degradations, page) -> None:
    lit = degradations.apply(page, "lighting", {"strength": 0.5, "angle": 0.0})

    assert lit.getpixel((0, 0)) == 255
    assert lit.getpixel((page.width - 1, 0)) == round(255 * 0.5)


# --- Generation (Verovio and cairosvg) ---------------------------------------------


@pytest.fixture(scope="module")
def renderer():
    pytest.importorskip("verovio")
    try:
        import cairosvg  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(f"cairosvg unavailable: {exc}")
    from omr_lab.synthetic import render

    try:
        font = render.choose_text_font(("Liberation Serif", "Times New Roman", "DejaVu Serif"))
    except render.TextFontUnavailableError as exc:
        pytest.skip(str(exc))
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

    with pytest.raises(render.TextFontUnavailableError, match="fontconfig"):
        render.choose_text_font((renderer.family,))


def test_missing_text_font_is_a_dependency_error(renderer, tmp_path: Path) -> None:
    from omr_lab.synthetic import SyntheticDependencyError, generate_corpus

    spec_path = _write_spec(tmp_path, _spec_document(text_fonts=["No Such Font Omr Lab"]))

    with pytest.raises(SyntheticDependencyError, match="No Such Font Omr Lab"):
        generate_corpus(load_corpus_spec(spec_path), tmp_path / "out")


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
    ranged = {**_SYNTHESIS, "degradations": [{"kind": "rotate", "params": {"degrees": {}}}]}
    too_big = {**_SYNTHESIS, "seed": 2**53}
    for synthesis in (no_seed, ranged, too_big):
        document = {"schema_version": "0.2.0", "samples": [_manifest_sample(synthesis=synthesis)]}
        with pytest.raises(BenchmarkDocumentValidationError):
            validate_benchmark_manifest(document)
