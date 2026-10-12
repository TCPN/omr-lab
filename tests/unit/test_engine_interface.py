import subprocess
from pathlib import Path

import pytest

from omr_lab.engines import (
    CommandResult,
    Engine,
    EngineAvailability,
    EngineResult,
    run_command,
)


class FakeEngine:
    name = "fake"

    def check_available(self) -> EngineAvailability:
        return EngineAvailability(engine_name=self.name, available=True, version="test-1")

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
    ) -> EngineResult:
        del timeout_seconds
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact = output_dir / "result.musicxml"
        artifact.write_text("<score-partwise version='4.0' />", encoding="utf-8")
        return EngineResult(
            engine_name=self.name,
            success=True,
            artifacts={"musicxml": artifact},
            version="test-1",
            diagnostics=(f"Processed {input_path.name}",),
            returncode=0,
        )


def test_fake_engine_implements_protocol_without_external_dependencies(tmp_path: Path) -> None:
    engine: Engine = FakeEngine()
    input_path = tmp_path / "score.png"
    input_path.write_bytes(b"fixture")

    availability = engine.check_available()
    result = engine.recognize(input_path, tmp_path / "out", timeout_seconds=2)

    assert availability.available is True
    assert availability.version == "test-1"
    assert result.success is True
    assert result.artifacts["musicxml"].is_file()
    assert result.diagnostics == ("Processed score.png",)


def test_command_result_marks_nonzero_exit_as_failure() -> None:
    result = CommandResult(args=("fake-tool",), returncode=2, stderr="bad input")

    assert result.succeeded is False


def test_run_command_passes_arguments_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(args, **kwargs):
        observed["args"] = args
        observed.update(kwargs)
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "ok", "stderr": ""},
        )()

    monkeypatch.setattr("omr_lab.engines.base.subprocess.run", fake_run)
    result = run_command(["tool path/with spaces", "input file.png"], timeout_seconds=5)

    assert result.succeeded is True
    assert observed["args"] == ("tool path/with spaces", "input file.png")
    assert observed["capture_output"] is True
    assert observed["text"] is True
    assert observed["timeout"] == 5
    assert "shell" not in observed


def test_run_command_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], stderr=b"still working")

    monkeypatch.setattr("omr_lab.engines.base.subprocess.run", fake_run)
    result = run_command(["slow-tool"], timeout_seconds=0.25)

    assert result.succeeded is False
    assert result.timed_out is True
    assert result.stderr == "still working"
    assert "timed out" in (result.error or "")


def test_run_command_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("not installed")

    monkeypatch.setattr("omr_lab.engines.base.subprocess.run", fake_run)
    result = run_command(["missing-tool"], timeout_seconds=1)

    assert result.succeeded is False
    assert "Unable to start command" in (result.error or "")


def test_run_command_validates_arguments() -> None:
    with pytest.raises(ValueError, match="executable"):
        run_command([], timeout_seconds=1)
    with pytest.raises(ValueError, match="greater than zero"):
        run_command(["tool"], timeout_seconds=0)
