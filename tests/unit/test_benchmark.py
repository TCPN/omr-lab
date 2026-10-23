import copy
import hashlib
import json
from pathlib import Path

import pytest

from omr_lab import cli
from omr_lab.benchmark import (
    REPORT_JSON_FILENAME,
    REPORT_MARKDOWN_FILENAME,
    ManifestError,
    load_manifest,
    render_markdown,
    run_benchmark,
)
from omr_lab.cli import EXIT_INVALID_INPUT, EXIT_OK, EXIT_OUTPUT_DIR_UNUSABLE, EXIT_RUN_FAILED, main
from omr_lab.engines import EngineOptions, EngineResult
from omr_lab.engines.mock import MOCK_ENGINE_VERSION, MockEngine
from omr_lab.pipeline import OutputDirectoryError
from omr_lab.schemas import validate_run_document
from omr_lab.schemas.benchmark import (
    BenchmarkDocumentValidationError,
    validate_benchmark_manifest,
    validate_benchmark_report,
)

E2E_MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "e2e" / "manifest.json"

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
    assert by_id["good"]["correctness"] is None
    assert by_id["bad"]["status"] == "failed"
    assert by_id["bad"]["run"]["error_code"] == "engine_failed"
    assert by_id["skipped"]["status"] == "excluded"
    assert by_id["skipped"]["exclusion_reason"] == "duplicate of good"
    assert by_id["skipped"]["run"] is None
    assert by_id["changed"]["status"] == "not_run"
    assert by_id["changed"]["problem"]["code"] == "image_checksum_mismatch"
    assert by_id["missing"]["problem"]["code"] == "image_missing"

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
    with pytest.raises(ManifestError, match="cannot be read"):
        load_manifest(tmp_path / "none.json")


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


def test_cli_benchmark_succeeds_on_the_e2e_manifest(tmp_path: Path) -> None:
    argv = ["benchmark", str(E2E_MANIFEST), "-o", str(tmp_path / "out"), "--engine", "mock"]
    assert main(argv) == EXIT_OK
    report = json.loads((tmp_path / "out" / REPORT_JSON_FILENAME).read_text(encoding="utf-8"))
    assert report["manifest"]["name"] == "e2e-sample"
    assert report["summary"]["succeeded"] == 1
