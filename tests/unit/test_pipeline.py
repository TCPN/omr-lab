import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from omr_lab.engines import EngineAvailability, EngineOptions, EngineResult, resolve_options
from omr_lab.engines.mock import MOCK_ENGINE_VERSION, MockEngine
from omr_lab.pipeline import (
    RAW_DIRNAME,
    RUN_DOCUMENT_FILENAME,
    OutputDirectoryError,
    PipelineResult,
    run_pipeline,
)
from omr_lab.schemas import validate_run_document

PNG_BYTES = b"\x89PNG\r\n\x1a\nnot-a-real-image"


@pytest.fixture
def score(tmp_path: Path) -> Path:
    path = tmp_path / "input" / "score.png"
    path.parent.mkdir()
    path.write_bytes(PNG_BYTES)
    return path


def _written(result: PipelineResult) -> dict:
    document = json.loads(result.document_path.read_text(encoding="utf-8"))
    validate_run_document(document)
    assert document == result.document
    return document


@dataclass
class ScriptedEngine:
    """Engine whose recognize() returns a result built by a test callback."""

    build: object
    name: str = "scripted"
    version: str | None = "scripted-1"
    calls: list[Path] = field(default_factory=list)

    def check_available(self) -> EngineAvailability:
        return EngineAvailability(engine_name=self.name, available=True, version=self.version)

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        self.calls.append(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return self.build(output_dir)  # type: ignore[operator]


def test_mock_engine_success_writes_valid_run_document(score: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "run"

    result = run_pipeline(score, output_dir, MockEngine())

    document = _written(result)
    assert result.succeeded
    assert result.document_path == output_dir / RUN_DOCUMENT_FILENAME
    assert document["status"] == "succeeded"
    assert document["error"] is None
    assert document["score"] is None
    assert document["input"]["filename"] == "score.png"
    assert document["input"]["media_type"] == "image/png"
    assert len(document["input"]["sha256"]) == 64
    assert document["engine"] == {"name": "mock", "version": MOCK_ENGINE_VERSION}
    assert document["options"] == {"outcome": "succeeded"}
    assert document["artifacts"] == {
        "musicxml": "raw/mock.musicxml",
        "engine_log": "raw/mock.log",
    }
    assert [w["code"] for w in document["warnings"]] == [
        "engine_diagnostic",
        "score_not_normalized",
    ]
    assert document["timing"]["duration_seconds"] >= 0
    assert document["timing"]["started_at"]


def test_raw_artifacts_and_run_document_are_stored_separately(score: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "run"

    run_pipeline(score, output_dir, MockEngine())

    assert sorted(p.name for p in output_dir.iterdir()) == [RAW_DIRNAME, RUN_DOCUMENT_FILENAME]
    assert sorted(p.name for p in (output_dir / RAW_DIRNAME).iterdir()) == [
        "mock.log",
        "mock.musicxml",
    ]
    assert score.read_bytes() == PNG_BYTES
    assert sorted(p.name for p in score.parent.iterdir()) == ["score.png"]


def test_mock_engine_is_deterministic_apart_from_timing(score: Path, tmp_path: Path) -> None:
    first = run_pipeline(score, tmp_path / "a", MockEngine()).document
    second = run_pipeline(score, tmp_path / "b", MockEngine()).document

    del first["timing"], second["timing"]
    assert first == second
    assert (tmp_path / "a" / "raw" / "mock.musicxml").read_bytes() == (
        tmp_path / "b" / "raw" / "mock.musicxml"
    ).read_bytes()


def test_engine_options_are_recorded(score: Path, tmp_path: Path) -> None:
    result = run_pipeline(score, tmp_path / "run", MockEngine(), options={"outcome": "succeeded"})

    assert _written(result)["options"] == {"outcome": "succeeded"}


def test_missing_input_records_invalid_input(tmp_path: Path) -> None:
    engine = ScriptedEngine(build=lambda _: pytest.fail("engine must not run"))

    result = run_pipeline(tmp_path / "missing.png", tmp_path / "run", engine)

    document = _written(result)
    assert not result.succeeded
    assert document["error"]["code"] == "invalid_input"
    assert "missing.png" in document["error"]["message"]
    assert document["input"] == {
        "filename": "missing.png",
        "media_type": "image/png",
        "sha256": None,
    }
    assert document["artifacts"] == {}
    assert document["warnings"] == []
    assert engine.calls == []


@pytest.mark.parametrize(
    ("name", "content", "fragment"),
    [
        ("notes.txt", b"text", "unsupported extension"),
        ("empty.png", b"", "is empty"),
    ],
)
def test_unusable_input_file_records_invalid_input(
    tmp_path: Path, name: str, content: bytes, fragment: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content)

    document = _written(run_pipeline(path, tmp_path / "run", MockEngine()))

    assert document["error"]["code"] == "invalid_input"
    assert fragment in document["error"]["message"]


def test_directory_input_records_invalid_input(tmp_path: Path) -> None:
    folder = tmp_path / "folder.png"
    folder.mkdir()

    document = _written(run_pipeline(folder, tmp_path / "run", MockEngine()))

    assert document["error"]["code"] == "invalid_input"
    assert "not a regular file" in document["error"]["message"]


def test_uppercase_extension_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "PAGE.PDF"
    path.write_bytes(b"%PDF-1.7")

    document = _written(run_pipeline(path, tmp_path / "run", MockEngine()))

    assert document["status"] == "succeeded"
    assert document["input"]["media_type"] == "application/pdf"


def test_unavailable_engine_records_engine_unavailable(score: Path, tmp_path: Path) -> None:
    result = run_pipeline(score, tmp_path / "run", MockEngine(available=False))

    document = _written(result)
    assert document["status"] == "failed"
    assert document["error"]["code"] == "engine_unavailable"
    assert "available=False" in document["error"]["message"]
    assert document["engine"] == {"name": "mock", "version": None}
    assert document["options"] == {}
    assert not (tmp_path / "run" / RAW_DIRNAME).exists()


def test_engine_failure_keeps_diagnostics_and_raw_log(score: Path, tmp_path: Path) -> None:
    result = run_pipeline(score, tmp_path / "run", MockEngine(), options={"outcome": "failed"})

    document = _written(result)
    assert document["status"] == "failed"
    assert document["error"] == {
        "code": "engine_failed",
        "message": "Mock engine simulated a failure for score.png.",
        "returncode": 1,
    }
    assert document["options"] == {"outcome": "failed"}
    assert document["artifacts"] == {"engine_log": "raw/mock.log"}
    assert (tmp_path / "run" / "raw" / "mock.log").is_file()
    assert document["warnings"] == [
        {
            "code": "engine_diagnostic",
            "message": "Mock engine simulated a failure (outcome=failed).",
        }
    ]
    assert document["score"] is None


def test_engine_failure_without_message_gets_a_generic_one(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(build=lambda _: EngineResult("scripted", success=False, returncode=3))

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"]["code"] == "engine_failed"
    assert "return code 3" in document["error"]["message"]
    assert document["error"]["returncode"] == 3


@pytest.mark.parametrize(
    ("options", "fragment"),
    [
        ({"outcom": "failed"}, "Unknown option(s) for engine 'mock': outcom"),
        ({"outcome": "maybe"}, "must be one of succeeded, failed"),
        ({"outcome": ["failed"]}, "must be a string, number, boolean, or None"),
    ],
)
def test_invalid_option_records_invalid_option(
    score: Path, tmp_path: Path, options: dict, fragment: str
) -> None:
    result = run_pipeline(score, tmp_path / "run", MockEngine(), options=options)

    document = _written(result)
    assert document["error"]["code"] == "invalid_option"
    assert fragment in document["error"]["message"]
    assert document["options"] == {}
    assert document["artifacts"] == {}


def test_unexpected_engine_exception_records_internal_error(score: Path, tmp_path: Path) -> None:
    def explode(_: Path) -> EngineResult:
        raise RuntimeError("adapter bug")

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=explode)))

    assert document["error"]["code"] == "internal_error"
    assert "RuntimeError" in document["error"]["message"]
    assert "adapter bug" in document["error"]["message"]


def test_multi_file_artifact_keys_map_to_snake_case_kinds(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "sub").mkdir()
        files = {
            "musicxml:sub/score.mvt2.musicxml": raw / "sub" / "score.mvt2.musicxml",
            "musicxml:sub/score.mvt1.musicxml": raw / "sub" / "score.mvt1.musicxml",
            "Engine-Log": raw / "engine.log",
        }
        for path in files.values():
            path.write_text("x", encoding="utf-8")
        return EngineResult("scripted", success=True, artifacts=files)

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {
        "musicxml": "raw/sub/score.mvt1.musicxml",
        "musicxml_2": "raw/sub/score.mvt2.musicxml",
        "engine_log": "raw/engine.log",
    }


def test_valid_kinds_are_kept_and_renamed_keys_avoid_collisions(
    score: Path, tmp_path: Path
) -> None:
    def build(raw: Path) -> EngineResult:
        names = ["a", "b", "c", "d", "e"]
        for name in names:
            (raw / name).write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={
                "musicxml_2": raw / "a",
                "musicxml": raw / "b",
                "musicxml:x": raw / "c",
                "2nd page": raw / "d",
                ":::": raw / "e",
            },
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {
        "musicxml_2": "raw/a",
        "musicxml": "raw/b",
        "musicxml_3": "raw/c",
        "artifact_2nd_page": "raw/d",
        "artifact": "raw/e",
    }


def test_relative_artifact_paths_resolve_from_working_directory(
    score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def build(raw: Path) -> EngineResult:
        assert not raw.is_absolute()
        (raw / "out.musicxml").write_text("x", encoding="utf-8")
        return EngineResult("scripted", success=True, artifacts={"musicxml": raw / "out.musicxml"})

    document = _written(run_pipeline(score, Path("run"), ScriptedEngine(build=build)))

    assert document["artifacts"] == {"musicxml": "raw/out.musicxml"}


def test_artifacts_outside_output_dir_or_missing_are_not_recorded(
    score: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.musicxml"
    outside.write_text("x", encoding="utf-8")

    def build(raw: Path) -> EngineResult:
        (raw / "ok.log").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={
                "musicxml": outside,
                "engine_log": raw / "ok.log",
                "extra": raw / "missing.txt",
                "root": raw.parent,
            },
        )

    result = run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build))

    document = _written(result)
    assert document["status"] == "succeeded"
    assert document["artifacts"] == {"engine_log": "raw/ok.log"}
    messages = [w["message"] for w in document["warnings"] if w["code"] == "artifact_not_recorded"]
    assert messages == [
        "Engine artifact 'musicxml' was not recorded: it is not inside the run output directory.",
        "Engine artifact 'extra' was not recorded: the file does not exist.",
        "Engine artifact 'root' was not recorded: it is not inside the run output directory.",
    ]
    assert str(tmp_path) not in result.document_path.read_text(encoding="utf-8")


def test_engine_version_from_result_overrides_availability(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult("scripted", success=True, version="from-result")
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["engine"] == {"name": "scripted", "version": "from-result"}


def test_blank_diagnostics_are_skipped_and_order_is_kept(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult(
            "scripted", success=True, diagnostics=("first", "  ", "second 中文")
        )
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["warnings"][:2] == [
        {"code": "engine_diagnostic", "message": "first"},
        {"code": "engine_diagnostic", "message": "second 中文"},
    ]
    assert "second 中文" in (tmp_path / "run" / RUN_DOCUMENT_FILENAME).read_text(encoding="utf-8")


def test_unserializable_options_fall_back_to_minimal_failed_record(
    score: Path, tmp_path: Path
) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult("scripted", success=True, options={"bad": float("nan")})
    )

    result = run_pipeline(score, tmp_path / "run", engine)

    document = _written(result)
    assert document["status"] == "failed"
    assert document["error"]["code"] == "internal_error"
    assert "could not be written" in document["error"]["message"]
    assert document["options"] == {}
    assert document["artifacts"] == {}
    assert document["input"]["sha256"] is not None


def test_options_are_passed_to_engine(score: Path, tmp_path: Path) -> None:
    seen: list[EngineOptions | None] = []

    class RecordingEngine(ScriptedEngine):
        def recognize(self, input_path, output_dir, *, timeout_seconds, options=None):
            seen.append(options)
            effective = resolve_options(self.name, options, {"device": "cpu"})
            return EngineResult(self.name, success=True, options=effective)

    engine = RecordingEngine(build=None)
    document = _written(run_pipeline(score, tmp_path / "run", engine, options={"device": "cuda"}))

    assert seen == [{"device": "cuda"}]
    assert document["options"] == {"device": "cuda"}


def test_non_empty_output_dir_is_rejected(score: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "keep.txt").write_text("previous run", encoding="utf-8")

    with pytest.raises(OutputDirectoryError, match="not empty"):
        run_pipeline(score, output_dir, MockEngine())
    assert sorted(p.name for p in output_dir.iterdir()) == ["keep.txt"]


def test_output_path_that_is_a_file_is_rejected(score: Path, tmp_path: Path) -> None:
    target = tmp_path / "run"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(OutputDirectoryError, match="not a directory"):
        run_pipeline(score, target, MockEngine())


def test_input_directory_cannot_be_the_output_dir(score: Path) -> None:
    with pytest.raises(OutputDirectoryError):
        run_pipeline(score, score.parent, MockEngine())
    assert score.read_bytes() == PNG_BYTES


def test_existing_empty_output_dir_is_used(score: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()

    assert run_pipeline(score, output_dir, MockEngine()).succeeded


def test_nested_output_dir_is_created(score: Path, tmp_path: Path) -> None:
    result = run_pipeline(score, tmp_path / "runs" / "2026" / "a", MockEngine())

    assert result.document_path.is_file()


def test_invalid_timeout_is_rejected(score: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_pipeline(score, tmp_path / "run", MockEngine(), timeout_seconds=0)
    assert not (tmp_path / "run").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="backslash is a path separator on Windows")
def test_unrecordable_input_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no file name"):
        run_pipeline(tmp_path / "bad\\name.png", tmp_path / "run", MockEngine())
    assert not (tmp_path / "run").exists()


def test_fallback_keeps_engine_failure_version_and_diagnostics(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult(
            "scripted",
            success=False,
            returncode=2,
            error="Engine crashed: out of memory",
            diagnostics=("loading page 1",),
            options={"bad": float("inf")},
        )
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"] == {
        "code": "engine_failed",
        "message": "Engine crashed: out of memory",
        "returncode": 2,
    }
    assert document["engine"]["version"] == "scripted-1"
    assert [w["code"] for w in document["warnings"]] == [
        "engine_diagnostic",
        "run_document_incomplete",
    ]
    assert "could not be written" in document["warnings"][1]["message"]
    assert document["options"] == {}


def test_fallback_for_succeeded_run_drops_score_not_normalized(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult("scripted", success=True, options={"bad": float("nan")})
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"]["code"] == "internal_error"
    assert document["engine"]["version"] == "scripted-1"
    assert document["warnings"] == []


def test_fallback_falls_back_to_minimal_record_when_error_is_invalid(
    score: Path, tmp_path: Path
) -> None:
    # A non-integer return code breaks the error object itself, so only the minimal
    # record validates.
    engine = ScriptedEngine(
        build=lambda _: EngineResult(
            "scripted", success=False, returncode="2", options={"bad": float("nan")}
        )
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"]["code"] == "internal_error"
    assert document["engine"]["version"] is None
    assert document["warnings"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="':' is not allowed in Windows file names")
def test_drive_prefix_artifact_name_is_not_recorded(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        # Directly under output_dir, so the relative path starts with "c:".
        (raw.parent / "c:notes.txt").write_text("x", encoding="utf-8")
        (raw / "score.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={"musicxml": raw / "score.musicxml", "notes": raw.parent / "c:notes.txt"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["status"] == "succeeded"
    assert document["artifacts"] == {"musicxml": "raw/score.musicxml"}
    messages = [w["message"] for w in document["warnings"] if w["code"] == "artifact_not_recorded"]
    assert messages == [
        "Engine artifact 'notes' was not recorded: its path must not start with a Windows "
        "drive prefix such as 'C:'."
    ]


def test_kinds_are_assigned_after_unrecorded_artifacts_are_dropped(
    score: Path, tmp_path: Path
) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "sub").mkdir()
        (raw / "sub" / "b.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={
                "musicxml": raw / "a.mxl",  # never written
                "musicxml:sub/b.musicxml": raw / "sub" / "b.musicxml",
            },
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {"musicxml": "raw/sub/b.musicxml"}


def test_input_that_cannot_be_stat_ed_is_invalid_input(
    score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Path.exists() raises PermissionError on Python 3.11-3.13 for an input in a
    # directory without search permission; simulate that without relying on chmod.
    original_stat = Path.stat

    def stat(self: Path, *args: object, **kwargs: object):
        if self == score:
            raise PermissionError(13, "Permission denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    engine = ScriptedEngine(build=lambda _: EngineResult("scripted", success=True))

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"]["code"] == "invalid_input"
    assert "cannot be read" in document["error"]["message"]
    assert engine.calls == []


def test_unresolvable_artifact_keeps_engine_failure(
    score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Path.resolve() raises RuntimeError for a symlink loop on Python 3.11/3.12.
    original_resolve = Path.resolve

    def resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self.name == "loop":
            raise RuntimeError("Symlink loop from 'loop'")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    def build(raw: Path) -> EngineResult:
        (raw / "score.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=False,
            returncode=3,
            error="Engine crashed",
            artifacts={"engine_log": raw / "loop", "musicxml": raw / "score.musicxml"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["error"] == {
        "code": "engine_failed",
        "message": "Engine crashed",
        "returncode": 3,
    }
    assert document["artifacts"] == {"musicxml": "raw/score.musicxml"}
    assert document["warnings"] == [
        {
            "code": "artifact_not_recorded",
            "message": (
                "Engine artifact 'engine_log' was not recorded: its path cannot be resolved."
            ),
        }
    ]
