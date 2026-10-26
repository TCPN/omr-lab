import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from omr_lab import benchmark, cli
from omr_lab.benchmark import (
    REPORT_JSON_FILENAME,
    REPORT_MARKDOWN_FILENAME,
    ManifestError,
    load_manifest,
    render_markdown,
    run_benchmark,
)
from omr_lab.cli import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_OUTPUT_DIR_UNUSABLE,
    EXIT_RUN_FAILED,
    main,
)
from omr_lab.engines import EngineOptions, EngineResult
from omr_lab.engines.mock import MOCK_ENGINE_VERSION, MockEngine
from omr_lab.metrics import compare_scores, summarize
from omr_lab.musicxml import load_musicxml
from omr_lab.pipeline import OutputDirectoryError
from omr_lab.schemas import validate_run_document
from omr_lab.schemas.benchmark import (
    BenchmarkDocumentValidationError,
    validate_benchmark_manifest,
    validate_benchmark_report,
)
from omr_lab.score import score_from_musicxml

E2E_MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "e2e" / "manifest.json"
TWINKLE = E2E_MANIFEST.parent / "twinkle.musicxml"

# Fields that differ between two runs of the same benchmark on the same machine.
NONDETERMINISTIC = ("generated_at",)


class _FailingOnBadMock(MockEngine):
    """The mock engine, failing for inputs whose name starts with 'bad'."""

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        if input_path.name.startswith("bad"):
            options = {**(options or {}), "outcome": "failed"}
        return super().recognize(
            input_path, output_dir, timeout_seconds=timeout_seconds, options=options
        )


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _sample(sample_id: str, image: str, sha256: str, **extra: object) -> dict:
    return {
        "id": sample_id,
        "image": image,
        "image_sha256": sha256,
        "source": "synthetic test bytes",
        "license": "CC0-1.0",
        "condition": "not a real image",
        **extra,
    }


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    """A small synthetic manifest covering every sample status."""
    base = tmp_path / "set"
    good = _write(base / "images" / "good.png", b"\x89PNG good")
    bad = _write(base / "images" / "bad.png", b"\x89PNG bad")
    truth = _write(base / "truth" / "good.musicxml", b"<score-partwise/>")
    samples = [
        _sample(
            "good",
            "images/good.png",
            good,
            ground_truth="truth/good.musicxml",
            ground_truth_sha256=truth,
        ),
        _sample("bad", "images/bad.png", bad),
        _sample("skipped", "images/good.png", good, excluded={"reason": "duplicate of good"}),
        _sample("changed", "images/good.png", "0" * 64),
        _sample("missing", "images/none.png", good),
    ]
    path = base / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": "0.1.0", "name": "unit", "samples": samples}),
        encoding="utf-8",
    )
    return path


def _run(manifest_path: Path, output: Path, **kwargs: object):
    return run_benchmark(
        load_manifest(manifest_path), output, _FailingOnBadMock(), timeout_seconds=60, **kwargs
    )


def test_e2e_fixture_manifest_is_a_valid_manifest() -> None:
    manifest = load_manifest(E2E_MANIFEST)
    assert [sample["id"] for sample in manifest.samples] == ["twinkle"]


def test_report_records_every_sample_status(manifest_path: Path, tmp_path: Path) -> None:
    result = _run(manifest_path, tmp_path / "out")
    report = result.report
    by_id = {entry["id"]: entry for entry in report["samples"]}

    assert [entry["id"] for entry in report["samples"]] == [
        "good",
        "bad",
        "skipped",
        "changed",
        "missing",
    ]
    assert by_id["good"]["status"] == "succeeded"
    assert by_id["good"]["ground_truth"] == "truth/good.musicxml"
    assert by_id["good"]["run"]["document"] == "runs/good/run.json"
    assert by_id["good"]["run"]["engine_version"] == MOCK_ENGINE_VERSION
    assert by_id["good"]["run"]["options"] == {"outcome": "succeeded"}
    # The fixture's ground truth is not loadable MusicXML.
    assert by_id["good"]["correctness"] is None
    assert by_id["good"]["correctness_problem"]["code"] == "ground_truth_invalid"
    assert "<input_dir>" in by_id["good"]["correctness_problem"]["message"]
    assert by_id["bad"]["status"] == "failed"
    assert by_id["bad"]["run"]["error_code"] == "engine_failed"
    assert by_id["skipped"]["status"] == "excluded"
    assert by_id["skipped"]["exclusion_reason"] == "duplicate of good"
    assert by_id["skipped"]["run"] is None
    assert by_id["changed"]["status"] == "not_run"
    assert by_id["changed"]["problem"]["code"] == "image_checksum_mismatch"
    assert by_id["missing"]["problem"]["code"] == "image_unreadable"

    summary = report["summary"]
    assert summary["samples"] == 5
    assert (summary["excluded"], summary["not_run"], summary["attempted"]) == (1, 2, 2)
    assert (summary["succeeded"], summary["failed"], summary["success_rate"]) == (1, 1, 0.5)
    assert summary["engine_versions"] == [MOCK_ENGINE_VERSION]
    assert summary["error_codes"] == {"engine_failed": 1}
    assert not result.all_succeeded


def test_report_files_are_written_and_valid(manifest_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    result = _run(manifest_path, output)

    written = json.loads((output / REPORT_JSON_FILENAME).read_text(encoding="utf-8"))
    validate_benchmark_report(written)
    assert written == result.report
    assert (output / REPORT_MARKDOWN_FILENAME).read_text(encoding="utf-8") == render_markdown(
        written
    )
    for entry in written["samples"]:
        if entry["run"] is not None:
            validate_run_document(
                json.loads((output / entry["run"]["document"]).read_text(encoding="utf-8"))
            )
    assert written["manifest"]["filename"] == "manifest.json"
    assert written["manifest"]["sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert written["engine"] == {
        "name": "mock",
        "requested_options": {},
        "timeout_seconds": 60,
    }
    assert set(written["environment"]) == {
        "omr_lab_version",
        "python_version",
        "python_implementation",
        "os",
        "os_release",
        "machine",
    }
    # No local path reaches the report.
    assert str(tmp_path) not in json.dumps(written)


def test_deterministic_fields_repeat(manifest_path: Path, tmp_path: Path) -> None:
    def stable(report: dict) -> dict:
        report = copy.deepcopy(report)
        for key in NONDETERMINISTIC:
            del report[key]
        del report["summary"]["run_duration_seconds"]
        for entry in report["samples"]:
            if entry["run"] is not None:
                del entry["run"]["duration_seconds"]
        return report

    first = _run(manifest_path, tmp_path / "first").report
    second = _run(manifest_path, tmp_path / "second").report
    assert stable(first) == stable(second)


def test_requested_options_are_recorded(manifest_path: Path, tmp_path: Path) -> None:
    report = _run(manifest_path, tmp_path / "out", options={"outcome": "failed"}).report
    assert report["engine"]["requested_options"] == {"outcome": "failed"}
    assert report["summary"]["succeeded"] == 0


def test_ground_truth_checksum_is_checked(tmp_path: Path) -> None:
    image = _write(tmp_path / "a.png", b"png")
    _write(tmp_path / "a.musicxml", b"<x/>")
    sample = _sample("a", "a.png", image, ground_truth="a.musicxml", ground_truth_sha256="1" * 64)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": "0.1.0", "samples": [sample]}), encoding="utf-8")

    report = run_benchmark(load_manifest(path), tmp_path / "out", MockEngine(), timeout_seconds=1)
    (entry,) = report.report["samples"]
    assert entry["status"] == "not_run"
    assert entry["problem"]["code"] == "ground_truth_checksum_mismatch"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), True])
def test_invalid_timeout_is_rejected_before_writing(
    manifest_path: Path, tmp_path: Path, timeout: float
) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_benchmark(
            load_manifest(manifest_path), tmp_path / "out", MockEngine(), timeout_seconds=timeout
        )
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", ["\udcff", float("nan"), Path("x")])
def test_unrecordable_options_are_rejected_before_writing(
    manifest_path: Path, tmp_path: Path, value: object
) -> None:
    with pytest.raises(ValueError, match="Engine options"):
        _run(manifest_path, tmp_path / "out", options={"x": value})
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_non_regular_image_is_not_opened(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe.png")  # opening it for reading would block
    path = tmp_path / "manifest.json"
    sample = _sample("a", "pipe.png", "a" * 64)
    path.write_text(json.dumps({"schema_version": "0.1.0", "samples": [sample]}), encoding="utf-8")

    report = run_benchmark(load_manifest(path), tmp_path / "out", MockEngine(), timeout_seconds=1)
    assert report.report["samples"][0]["problem"] == {
        "code": "image_unreadable",
        "message": "'pipe.png' is not a regular file.",
    }


def test_output_directory_must_be_empty(manifest_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "old").write_text("x")
    with pytest.raises(OutputDirectoryError, match="not empty"):
        _run(manifest_path, output)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("schema_version"), "schema_version"),
        (lambda d: d["samples"][0].update(id="Upper"), "id"),
        (lambda d: d["samples"][0].update(image="/abs/a.png"), "must be relative"),
        (lambda d: d["samples"][0].update(image="a\\b.png"), "separator"),
        (lambda d: d["samples"][0].update(image_sha256="ABC"), "image_sha256"),
        (lambda d: d["samples"][0].pop("license"), "license"),
        (lambda d: d["samples"][0].update(ground_truth="a.musicxml"), "ground_truth_sha256"),
        (
            lambda d: d["samples"][0].update(ground_truth="a.musicxml", ground_truth_sha256=None),
            "ground_truth_sha256",
        ),
        (lambda d: d["samples"][0].update(id="a."), "id"),
        (lambda d: d["samples"][0].update(id="nul"), "reserves"),
        (lambda d: d["samples"][0].update(id="com1.txt"), "reserves"),
        (lambda d: d["samples"][0].update(excluded={}), "reason"),
        (lambda d: d["samples"][0].update(extra=1), "extra"),
        (lambda d: d.update(samples=[]), "samples"),
    ],
)
def test_invalid_manifests_are_rejected(change, message: str) -> None:
    document = {"schema_version": "0.1.0", "samples": [_sample("a", "a.png", "a" * 64)]}
    validate_benchmark_manifest(document)
    change(document)
    with pytest.raises(BenchmarkDocumentValidationError, match=message):
        validate_benchmark_manifest(document)


def test_duplicate_ids_and_bad_json_are_manifest_errors(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    sample = _sample("a", "a.png", "a" * 64)
    path.write_text(
        json.dumps({"schema_version": "0.1.0", "samples": [sample, sample]}), encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="repeated: a"):
        load_manifest(path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ManifestError, match="not UTF-8 JSON"):
        load_manifest(path)
    path.write_text(
        '{"schema_version": "0.1.0", "samples": [{"source": "\\ud83c"}]}', encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="unpaired surrogate"):
        load_manifest(path)
    with pytest.raises(ManifestError, match="cannot be read"):
        load_manifest(tmp_path / "none.json")


def test_ground_truth_null_is_allowed_without_a_path() -> None:
    sample = _sample("a", "a.png", "a" * 64, ground_truth=None, ground_truth_sha256=None)
    validate_benchmark_manifest({"schema_version": "0.1.0", "samples": [sample]})


def test_run_not_started_message_has_no_local_path(
    manifest_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(input_path: Path, output_dir: Path, *args: object, **kwargs: object) -> None:
        # As run_pipeline does for a run directory it cannot use (on Windows, "a." and "a").
        raise OutputDirectoryError(f"Output directory {str(output_dir)!r} is already used.")

    monkeypatch.setattr(benchmark, "run_pipeline", refuse)
    report = _run(manifest_path, tmp_path / "out").report
    problem = report["samples"][0]["problem"]
    assert problem == {
        "code": "run_not_started",
        "message": f"Output directory {'<output_dir>/runs/good'!r} is already used.",
    }
    assert str(tmp_path) not in json.dumps(report)


def test_run_not_started_redacts_whole_path_components_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A manifest loaded by a relative path from its own directory: its base dir is ".",
    # which must not turn every "." in the message into a placeholder.
    image = _write(tmp_path / "data" / "a.png", b"png")
    (tmp_path / "data" / "manifest.json").write_text(
        json.dumps({"schema_version": "0.1.0", "samples": [_sample("a", "a.png", image)]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "data")

    def refuse(input_path: Path, *args: object, **kwargs: object) -> None:
        raise ValueError(f"Input file name {input_path.name!r} is not valid. Rename data.")

    monkeypatch.setattr(benchmark, "run_pipeline", refuse)
    report = run_benchmark(
        load_manifest("manifest.json"), "data", MockEngine(), timeout_seconds=1
    ).report
    assert report["samples"][0]["problem"]["message"] == (
        "Input file name 'a.png' is not valid. Rename data."
    )


def test_markdown_escapes_table_cells(manifest_path: Path, tmp_path: Path) -> None:
    report = _run(manifest_path, tmp_path / "out").report
    report["samples"][2]["exclusion_reason"] = "a | b\nc"
    assert "| a \\| b c |" in render_markdown(report)


def test_cli_benchmark_exit_codes(
    manifest_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(cli.ENGINES, "mock", _FailingOnBadMock)
    output = tmp_path / "out"
    argv = ["benchmark", str(manifest_path), "-o", str(output), "--engine", "mock"]
    assert main(argv) == EXIT_RUN_FAILED
    out = capsys.readouterr().out
    assert f"Report: {output / REPORT_JSON_FILENAME}" in out
    assert "  bad: failed [engine_failed]" in out
    assert "  changed: not_run [image_checksum_mismatch]" in out

    assert main(argv) == EXIT_OUTPUT_DIR_UNUSABLE
    assert (
        main(
            [
                "benchmark",
                str(tmp_path / "none.json"),
                "-o",
                str(tmp_path / "x"),
                "--engine",
                "mock",
            ]
        )
        == EXIT_INVALID_INPUT
    )


def test_cli_benchmark_succeeds_on_the_e2e_manifest(tmp_path: Path, capsys) -> None:
    argv = ["benchmark", str(E2E_MANIFEST), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert main(argv) == EXIT_OK
    report = json.loads((tmp_path / "out" / REPORT_JSON_FILENAME).read_text(encoding="utf-8"))
    assert report["manifest"]["name"] == "e2e-sample"
    assert report["summary"]["succeeded"] == 1
    # The mock recognizes nothing: an empty prediction against the twinkle ground truth.
    assert report["samples"][0]["correctness"]["notes"]["pitch"]["recall"] == 0.0
    assert (
        "Correctness over 1 sample(s) with ground truth: notes precision n/a, recall 0.000; "
        "lyrics character error rate 1.000" in capsys.readouterr().out
    )


class _EchoEngine(MockEngine):
    """Returns the ground truth as its recognition, except for 'bad' inputs, which fail."""

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        if input_path.name.startswith("bad"):
            options = {**(options or {}), "outcome": "failed"}
        result = super().recognize(
            input_path, output_dir, timeout_seconds=timeout_seconds, options=options
        )
        if result.success:
            result.artifacts["musicxml"].write_bytes(TWINKLE.read_bytes())
        return result


def _correctness_manifest(tmp_path: Path, truth_bytes: bytes | None = None) -> Path:
    base = tmp_path / "set"
    truth = _write(base / "truth.musicxml", truth_bytes or TWINKLE.read_bytes())
    samples = [
        _sample(
            name,
            f"{name}.png",
            _write(base / f"{name}.png", name.encode()),
            ground_truth="truth.musicxml",
            ground_truth_sha256=truth,
        )
        for name in ("good", "bad")
    ]
    samples.append(_sample("no-truth", "plain.png", _write(base / "plain.png", b"plain")))
    path = base / "manifest.json"
    path.write_text(json.dumps({"schema_version": "0.2.0", "samples": samples}), encoding="utf-8")
    return path


def test_report_measures_correctness_against_the_ground_truth(tmp_path: Path) -> None:
    result = run_benchmark(
        load_manifest(_correctness_manifest(tmp_path)),
        tmp_path / "out",
        _EchoEngine(),
        timeout_seconds=60,
    )
    report = result.report
    validate_benchmark_report(report)
    good, bad, plain = report["samples"]

    assert good["correctness"]["prediction"] == "score"
    assert good["correctness"]["notes"]["pitch_and_duration"]["f1"] == 1.0
    assert good["correctness"]["notes"]["ground_truth"] == 42
    assert good["correctness"]["measures"]["accuracy"] == 1.0
    assert good["correctness"]["lyrics"]["verses"][0]["character_error_rate"] == 0.0
    assert good["correctness_problem"] is None
    # A failed run is compared as a missing prediction, so it lowers recall.
    assert bad["status"] == "failed"
    assert bad["correctness"]["prediction"] == "missing"
    assert bad["correctness"]["notes"]["pitch"]["recall"] == 0.0
    assert plain["correctness"] is None
    assert plain["correctness_problem"] is None
    totals = report["summary"]["correctness"]
    assert (totals["samples"], totals["predictions_missing"]) == (2, 1)
    assert totals["notes"]["pitch_and_duration"]["recall"] == 0.5
    markdown = render_markdown(report)
    assert "## Correctness" in markdown
    assert "| good | 1.000 / 1.000 | 1.000 | 1.000 | 12/12 | yes | yes |" in markdown
    assert "| **Total** | 1.000 / 0.500 |" in markdown

    # Lyrics invented where the ground truth has none still show up.
    predicted = score_from_musicxml(load_musicxml(TWINKLE)).score
    instrumental = copy.deepcopy(predicted)
    for measure in instrumental["parts"][0]["measures"]:
        for event in measure["events"]:
            if event["kind"] == "note":
                event["lyrics"] = []
    good["correctness"] = compare_scores(instrumental, predicted)
    report["summary"]["correctness"] = summarize([good["correctness"], bad["correctness"]])
    invented = good["correctness"]["lyrics"]["extra_predicted_verses"][0]["predicted_characters"]
    assert invented > 0
    assert f"| – (+{invented} chars) |" in render_markdown(report)


def test_ground_truth_conversion_warnings_are_reported(tmp_path: Path) -> None:
    text = TWINKLE.read_text(encoding="utf-8")
    start = text.index("<note")
    duration = text.index("<duration>", start)
    end = text.index("</duration>", duration) + len("</duration>")
    broken = (text[:duration] + text[end:]).encode()  # first note without a duration

    report = run_benchmark(
        load_manifest(_correctness_manifest(tmp_path, broken)),
        tmp_path / "out",
        _EchoEngine(),
        timeout_seconds=60,
    ).report
    validate_benchmark_report(report)
    good, _, plain = report["samples"]

    assert good["ground_truth_warning_codes"]
    assert good["correctness"]["notes"]["duration"]["matched"] == 41
    assert plain["ground_truth_warning_codes"] is None
    assert "Ground truth of good had warnings: " in render_markdown(report)


def test_reports_of_schema_0_1_0_still_validate(manifest_path: Path, tmp_path: Path) -> None:
    report = _run(manifest_path, tmp_path / "out").report
    old = copy.deepcopy(report)
    old["schema_version"] = "0.1.0"
    del old["summary"]["correctness"]
    for entry in old["samples"]:
        del entry["correctness_problem"]
        del entry["ground_truth_warning_codes"]

    validate_benchmark_report(old)
    assert "This report predates correctness measurement." in render_markdown(old)
    with pytest.raises(BenchmarkDocumentValidationError, match="correctness"):
        validate_benchmark_report({**old, "schema_version": "0.2.0"})


def test_success_rate_rounds_down() -> None:
    run = {"engine_version": None, "error_code": None, "duration_seconds": 0.0}
    entries = [{"status": "succeeded", "run": run, "correctness": None}] * 2999 + [
        {"status": "failed", "run": {**run, "error_code": "engine_failed"}, "correctness": None}
    ]
    assert benchmark._summary(entries)["success_rate"] == 0.999


def test_cli_benchmark_unavailable_engine_writes_nothing(
    manifest_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(cli.ENGINES, "mock", lambda: MockEngine(available=False))
    output = tmp_path / "out"
    argv = ["benchmark", str(manifest_path), "-o", str(output), "--engine", "mock"]
    assert main(argv) == cli.EXIT_ENGINE_UNAVAILABLE
    assert not output.exists()


def test_cli_report_validation_failure_is_not_a_usage_error(
    manifest_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def invalid(report: dict) -> None:
        raise BenchmarkDocumentValidationError("report", ["$.x: broken"])

    monkeypatch.setattr(benchmark, "validate_benchmark_report", invalid)
    argv = ["benchmark", str(manifest_path), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert main(argv) == EXIT_OUTPUT_DIR_UNUSABLE
    assert "report failed validation" in capsys.readouterr().err
