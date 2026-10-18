import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import ANY

import pytest

from omr_lab.engines import (
    EngineAvailability,
    EngineOptions,
    EngineResult,
    InvalidEngineOptionError,
    resolve_options,
)
from omr_lab.engines.mock import MOCK_ENGINE_VERSION, MockEngine
from omr_lab.pipeline import (
    ENGINE_DIAGNOSTICS_FILENAME,
    RAW_DIRNAME,
    RUN_DOCUMENT_FILENAME,
    OutputDirectoryError,
    PipelineResult,
    RunErrorCode,
    RunWarningCode,
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
        "engine_diagnostics": ENGINE_DIAGNOSTICS_FILENAME,
    }
    assert [w["code"] for w in document["warnings"]] == ["score_not_normalized"]
    assert document["timing"]["duration_seconds"] >= 0
    assert document["timing"]["started_at"]


def test_raw_artifacts_and_run_document_are_stored_separately(score: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "run"

    run_pipeline(score, output_dir, MockEngine())

    assert sorted(p.name for p in output_dir.iterdir()) == [
        ENGINE_DIAGNOSTICS_FILENAME,
        RAW_DIRNAME,
        RUN_DOCUMENT_FILENAME,
    ]
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
    assert list((tmp_path / "run" / RAW_DIRNAME).iterdir()) == []


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
    assert document["artifacts"] == {
        "engine_log": "raw/mock.log",
        "engine_diagnostics": ENGINE_DIAGNOSTICS_FILENAME,
    }
    assert (tmp_path / "run" / "raw" / "mock.log").is_file()
    assert document["warnings"] == []
    assert (tmp_path / "run" / ENGINE_DIAGNOSTICS_FILENAME).read_text(encoding="utf-8") == (
        "Mock engine simulated a failure (outcome=failed).\n"
    )
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
                "notes": outside,
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
        "Engine artifact 'notes' was not recorded: it is not inside the engine's raw/ output "
        "directory.",
        "Engine artifact 'extra' was not recorded: the file does not exist.",
        "Engine artifact 'root' was not recorded: it is not inside the engine's raw/ output "
        "directory.",
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

    assert document["warnings"] == [{"code": "score_not_normalized", "message": ANY}]
    assert document["artifacts"] == {"engine_diagnostics": ENGINE_DIAGNOSTICS_FILENAME}
    log = (tmp_path / "run" / ENGINE_DIAGNOSTICS_FILENAME).read_bytes()
    assert log == "first\nsecond 中文\n".encode()


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
    assert [w["code"] for w in document["warnings"]] == ["run_document_incomplete"]
    assert "could not be written" in document["warnings"][0]["message"]
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


def test_artifact_outside_raw_such_as_run_json_is_not_recorded(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw.parent / RUN_DOCUMENT_FILENAME).write_text("engine output", encoding="utf-8")
        (raw / "score.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={"result": raw.parent / RUN_DOCUMENT_FILENAME, "raw": raw},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["status"] == "succeeded"
    assert document["artifacts"] == {}
    messages = [w["message"] for w in document["warnings"] if w["code"] == "artifact_not_recorded"]
    assert messages == [
        f"Engine artifact {key!r} was not recorded: it is not inside the engine's raw/ "
        "output directory."
        for key in ("result", "raw")
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="backslash is a path separator on Windows")
def test_artifact_path_breaking_a_schema_rule_is_not_recorded(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "a\\b.txt").write_text("x", encoding="utf-8")
        (raw / "score.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={"musicxml": raw / "score.musicxml", "notes": raw / "a\\b.txt"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["status"] == "succeeded"
    assert document["artifacts"] == {"musicxml": "raw/score.musicxml"}
    messages = [w["message"] for w in document["warnings"] if w["code"] == "artifact_not_recorded"]
    assert messages == [
        "Engine artifact 'notes' was not recorded: its path must use '/' as the separator, "
        "not '\\'."
    ]


def test_kinds_are_assigned_after_unrecorded_artifacts_are_dropped(
    score: Path, tmp_path: Path
) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "a.mxl").write_text("x", encoding="utf-8")
        (raw / "sub").mkdir()
        (raw / "sub" / "c.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={
                "musicxml": raw / "a.mxl",
                "musicxml:sub/b.musicxml": raw / "sub" / "b.musicxml",  # never written
                "musicxml:sub/c.musicxml": raw / "sub" / "c.musicxml",
            },
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {"musicxml": "raw/a.mxl", "musicxml_2": "raw/sub/c.musicxml"}


def test_unrecorded_primary_musicxml_fails_the_run(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "sub").mkdir()
        (raw / "sub" / "b.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            returncode=0,
            artifacts={
                "musicxml": raw / "a.mxl",  # never written
                "musicxml:sub/b.musicxml": raw / "sub" / "b.musicxml",
            },
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["status"] == "failed"
    assert document["error"]["code"] == "engine_failed"
    assert document["error"]["returncode"] == 0
    assert "primary MusicXML artifact 'musicxml' was not recorded" in document["error"]["message"]
    # The movement file keeps a numbered kind; it is never promoted to the primary kind.
    assert document["artifacts"] == {"musicxml_2": "raw/sub/b.musicxml"}
    assert [w["code"] for w in document["warnings"]] == ["artifact_not_recorded"]


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


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_dangling_symlink_output_dir_is_rejected(score: Path, tmp_path: Path) -> None:
    link = tmp_path / "run"
    link.symlink_to(tmp_path / "missing")

    with pytest.raises(OutputDirectoryError, match="cannot be used"):
        run_pipeline(score, link, MockEngine())


def test_output_dir_under_a_file_is_rejected(score: Path, tmp_path: Path) -> None:
    parent = tmp_path / "notes.txt"
    parent.write_text("x", encoding="utf-8")

    with pytest.raises(OutputDirectoryError, match="cannot be used"):
        run_pipeline(score, parent / "out", MockEngine())
    assert parent.read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("bad", [None, b"bytes stderr"])
def test_non_string_diagnostic_keeps_engine_failure(
    score: Path, tmp_path: Path, bad: object
) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult(
            "scripted", success=False, returncode=3, error="crash", diagnostics=("ok", bad)
        )
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"] == {"code": "engine_failed", "message": "crash", "returncode": 3}
    assert [w["code"] for w in document["warnings"]] == ["engine_diagnostic_not_recorded"]
    log = (tmp_path / "run" / ENGINE_DIAGNOSTICS_FILENAME).read_text(encoding="utf-8")
    assert log == "ok\n"


def test_error_after_engine_failure_keeps_engine_failed(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult(
            "scripted", success=False, returncode=3, error="crash", options=None
        )
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"] == {"code": "engine_failed", "message": "crash", "returncode": 3}
    assert [w["code"] for w in document["warnings"]] == ["unexpected_error"]
    assert "TypeError" in document["warnings"][0]["message"]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1.0, True])
def test_non_finite_or_negative_timeout_is_rejected(
    score: Path, tmp_path: Path, timeout: float
) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_pipeline(score, tmp_path / "run", MockEngine(), timeout_seconds=timeout)
    assert not (tmp_path / "run").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_raw_symlinked_out_of_run_dir_records_no_artifacts(score: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def build(raw: Path) -> EngineResult:
        raw.rmdir()
        raw.symlink_to(elsewhere, target_is_directory=True)
        (elsewhere / "score.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=False,
            returncode=1,
            error="crash",
            artifacts={"musicxml": raw / "score.musicxml"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["error"] == {"code": "engine_failed", "message": "crash", "returncode": 1}
    assert document["artifacts"] == {}
    assert [w["code"] for w in document["warnings"]] == ["artifact_not_recorded"]


def test_option_error_without_message_keeps_invalid_option(score: Path, tmp_path: Path) -> None:
    def build(_raw: Path) -> EngineResult:
        raise InvalidEngineOptionError()

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["error"] == {
        "code": "invalid_option",
        "message": "Engine 'scripted' rejected the options.",
    }
    assert document["engine"]["version"] == "scripted-1"


def test_engine_failure_with_blank_error_gets_a_message(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult("scripted", success=False, returncode=1, error="   ")
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"]["code"] == "engine_failed"
    assert document["error"]["message"].strip()
    assert document["error"]["returncode"] == 1


def test_directory_artifact_is_not_recorded(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "book").mkdir()
        (raw / "book" / "score.musicxml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={"musicxml": raw / "book", "engine_log": raw / "book" / "score.musicxml"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {"engine_log": "raw/book/score.musicxml"}
    messages = [w["message"] for w in document["warnings"] if w["code"] == "artifact_not_recorded"]
    assert messages == ["Engine artifact 'musicxml' was not recorded: it is not a regular file."]


def test_key_with_trailing_newline_is_not_kept_as_a_kind(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "a.xml").write_text("x", encoding="utf-8")
        (raw / "b.xml").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            artifacts={"musicxml": raw / "a.xml", "musicxml\n": raw / "b.xml"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {"musicxml": "raw/a.xml", "musicxml_2": "raw/b.xml"}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows file names are always Unicode")
def test_input_name_that_is_not_utf8_is_rejected(tmp_path: Path) -> None:
    name = os.fsdecode(b"\xa6\xb1score.png")  # Big5 bytes, decoded with surrogateescape
    with pytest.raises(ValueError, match="not valid UTF-8"):
        run_pipeline(tmp_path / name, tmp_path / "run", MockEngine())
    assert not (tmp_path / "run").exists()


def test_surrogate_in_diagnostic_is_written_escaped(score: Path, tmp_path: Path) -> None:
    bad = os.fsdecode(b"stderr \xff")
    engine = ScriptedEngine(
        build=lambda _: EngineResult(
            "scripted", success=False, returncode=1, error="crash", diagnostics=(bad,)
        )
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert document["error"] == {"code": "engine_failed", "message": "crash", "returncode": 1}
    assert document["warnings"] == []
    log = (tmp_path / "run" / ENGINE_DIAGNOSTICS_FILENAME).read_text(encoding="utf-8")
    assert log == "stderr \\udcff\n"
    assert not list((tmp_path / "run").glob(f".{RUN_DOCUMENT_FILENAME}.*"))


@pytest.mark.parametrize("existing", [False, True], ids=["missing", "empty"])
def test_output_dir_claimed_concurrently_is_rejected(
    score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    output_dir = tmp_path / "run"
    if existing:
        output_dir.mkdir()
    raw = output_dir / RAW_DIRNAME
    original_mkdir = Path.mkdir

    def mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == raw and not self.exists():
            original_mkdir(self)  # another run claims the directory after the empty check
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)

    with pytest.raises(OutputDirectoryError, match="already used by another run"):
        run_pipeline(score, output_dir, MockEngine())
    assert [p.name for p in output_dir.iterdir()] == [RAW_DIRNAME]


def test_parallel_runs_into_one_empty_directory_leave_one_result(
    score: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def run() -> None:
        barrier.wait()
        try:
            run_pipeline(score, output_dir, MockEngine())
        except OutputDirectoryError:
            outcomes.append("rejected")
        else:
            outcomes.append("succeeded")

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["rejected"] * 3 + ["succeeded"]
    assert sorted(p.name for p in (output_dir / RAW_DIRNAME).iterdir()) == [
        "mock.log",
        "mock.musicxml",
    ]


def test_absolute_paths_in_messages_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "ann"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    score = home / "scores" / "private" / "score.png"
    score.parent.mkdir(parents=True)
    score.write_bytes(PNG_BYTES)
    output_dir = home / "runs" / "run1"
    launcher = home / "bin" / "audiveris"

    def build(raw: Path) -> EngineResult:
        return EngineResult(
            "scripted",
            success=False,
            returncode=1,
            error=f"Audiveris failed; see {raw}",
            diagnostics=(
                f"command: [{str(launcher)!r}, '-output', {str(raw)!r}, '--', {str(score)!r}]",
                f"INFO processing {score}",
                f"sibling {home}a/x and {home.parent}",
            ),
        )

    run_pipeline(score, output_dir, ScriptedEngine(build=build))

    text = (output_dir / RUN_DOCUMENT_FILENAME).read_text(encoding="utf-8")
    log = (output_dir / ENGINE_DIAGNOSTICS_FILENAME).read_text(encoding="utf-8")
    assert str(home) not in text
    assert json.loads(text)["error"]["message"] == "Audiveris failed; see <output_dir>/raw"
    assert log.splitlines() == [
        "command: ['~/bin/audiveris', '-output', '<output_dir>/raw', '--', "
        "'<input_dir>/score.png']",
        "INFO processing <input_dir>/score.png",
        # Only whole path components match, so a longer sibling name is left alone.
        f"sibling {home}a/x and {home.parent}",
    ]


def test_unreadable_input_message_has_no_absolute_path(
    score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = Path.open

    def open_(self: Path, *args: object, **kwargs: object):
        if self == score:
            raise PermissionError(13, "Permission denied", str(self))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)

    document = _written(run_pipeline(score, tmp_path / "run", MockEngine()))

    assert document["error"] == {
        "code": "invalid_input",
        "message": "Input file 'score.png' cannot be read: Permission denied.",
    }


def test_keyboard_interrupt_writes_an_interrupted_record_and_reraises(
    score: Path, tmp_path: Path
) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "partial").write_text("x", encoding="utf-8")
        raise KeyboardInterrupt

    output_dir = tmp_path / "run"

    with pytest.raises(KeyboardInterrupt):
        run_pipeline(score, output_dir, ScriptedEngine(build=build))

    document = json.loads((output_dir / RUN_DOCUMENT_FILENAME).read_text(encoding="utf-8"))
    validate_run_document(document)
    assert document["status"] == "failed"
    assert document["error"] == {
        "code": "interrupted",
        "message": "The run was interrupted (KeyboardInterrupt).",
    }
    assert document["warnings"] == []


def test_string_diagnostics_are_one_entry(score: Path, tmp_path: Path) -> None:
    engine = ScriptedEngine(
        build=lambda _: EngineResult("scripted", success=True, diagnostics="boom")  # type: ignore[arg-type]
    )

    document = _written(run_pipeline(score, tmp_path / "run", engine))

    assert [w["code"] for w in document["warnings"]] == ["score_not_normalized"]
    log = (tmp_path / "run" / ENGINE_DIAGNOSTICS_FILENAME).read_text(encoding="utf-8")
    assert log == "boom\n"


def test_engine_key_with_the_diagnostics_kind_is_renamed(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw / "diag.txt").write_text("x", encoding="utf-8")
        return EngineResult(
            "scripted",
            success=True,
            diagnostics=("line",),
            artifacts={"engine_diagnostics": raw / "diag.txt"},
        )

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["artifacts"] == {
        "engine_diagnostics_2": "raw/diag.txt",
        "engine_diagnostics": ENGINE_DIAGNOSTICS_FILENAME,
    }


def test_unwritable_diagnostics_file_adds_a_warning(score: Path, tmp_path: Path) -> None:
    def build(raw: Path) -> EngineResult:
        (raw.parent / ENGINE_DIAGNOSTICS_FILENAME).mkdir()  # an adapter bug in the way
        return EngineResult("scripted", success=True, diagnostics=("a", "b"))

    document = _written(run_pipeline(score, tmp_path / "run", ScriptedEngine(build=build)))

    assert document["status"] == "succeeded"
    assert document["artifacts"] == {}
    assert document["warnings"][0]["code"] == "engine_diagnostic_not_recorded"
    assert document["warnings"][0]["message"].startswith("2 engine diagnostics were left out")


def _documented_codes(kind: str) -> set[str]:
    text = (Path(__file__).parents[2] / "docs" / "json-schema.md").read_text(encoding="utf-8")
    return set(re.findall(rf"^\| `([a-z_]+)` \| {kind} \|", text, flags=re.MULTILINE))


def test_code_enums_match_the_documented_code_table() -> None:
    assert {code.value for code in RunErrorCode} == _documented_codes("error")
    assert {code.value for code in RunWarningCode} == _documented_codes("warning")
