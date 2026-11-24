"""Unit tests for the oemer adapter; process execution is mocked throughout.

Lookup, version probing, caching and run-directory handling are shared with homr
(``ConsoleToolEngine``) and covered in depth by test_homr_engine.py; these tests
cover what is specific to oemer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omr_lab.engines import (
    ENGINES,
    CommandResult,
    Engine,
    InvalidEngineOptionError,
    OemerEngine,
    UnknownEngineOptionError,
    console_tool,
)


def _no_which(name: str) -> str | None:
    return None


class FakeOemer:
    """Replaces run_command: answers the version script and simulates oemer."""

    def __init__(
        self,
        *,
        run_result: CommandResult | None = None,
        write_musicxml: bool = True,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.run_result = run_result
        self.write_musicxml = write_musicxml

    def __call__(self, args, *, timeout_seconds: float, cwd=None) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        if "-c" in command:
            return CommandResult(args=command, returncode=0, stdout="0.1.8\n")
        if self.write_musicxml:
            output = Path(command[command.index("-o") + 1])
            output.write_text("<score-partwise/>", encoding="utf-8")
            output.with_name(f"{output.stem}_teaser.png").write_bytes(b"png")
        return self.run_result or CommandResult(args=command, returncode=0)

    @property
    def recognition_calls(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if "-c" not in c]


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    """A fake console script next to its environment's interpreter."""
    bin_dir = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    bin_dir.mkdir(parents=True)
    script = bin_dir / ("oemer.exe" if sys.platform == "win32" else "oemer")
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text(
        "", encoding="utf-8"
    )
    return script


@pytest.fixture
def score(tmp_path: Path) -> Path:
    path = tmp_path / "in" / "My Score.JPEG"
    path.parent.mkdir()
    path.write_bytes(b"not really a jpeg")
    return path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeOemer:
    runner = FakeOemer()
    monkeypatch.setattr(console_tool, "run_command", runner)
    return runner


def _engine(executable: Path | None = None, **kwargs) -> OemerEngine:
    kwargs.setdefault("environ", {})
    kwargs.setdefault("which", _no_which)
    return OemerEngine(executable, **kwargs)


def test_implements_engine_protocol_and_is_registered() -> None:
    engine: Engine = _engine()
    assert isinstance(engine, Engine)
    assert engine.name == "oemer"
    assert ENGINES["oemer"] is OemerEngine


def test_found_through_env_var_and_path(executable: Path) -> None:
    from_env = _engine(environ={"OEMER_EXE": str(executable)})
    from_path = _engine(which=lambda n: str(executable) if n == "oemer" else None)

    assert from_env.find_executable().path == executable
    assert from_path.find_executable().path == executable


def test_missing_oemer_is_unavailable_with_pinned_install_hint() -> None:
    availability = _engine().check_available()

    assert availability.available is False
    message = availability.message or ""
    assert "OEMER_EXE" in message
    assert "onnxruntime-gpu<1.20" in message and "numpy<2" in message


def test_version_is_read_for_the_oemer_package(executable: Path, fake: FakeOemer) -> None:
    availability = _engine(executable).check_available()

    assert availability.available is True
    assert availability.version == "0.1.8"
    script = fake.calls[0][-1]
    assert "m.version('oemer')" in script


def test_recognize_writes_into_a_run_directory(
    executable: Path, score: Path, tmp_path: Path, fake: FakeOemer
) -> None:
    out = tmp_path / "out"

    result = _engine(executable).recognize(score, out, timeout_seconds=600)

    assert result.success is True, result.error
    assert result.version == "0.1.8"
    assert result.options == {"deskew": True}
    (command,) = fake.recognition_calls
    run_dir = Path(command[1]).parent
    assert run_dir.parent == out.absolute() and run_dir.name.startswith("oemer-")
    assert command == (
        str(executable),
        str(run_dir / "input.jpeg"),
        "-o",
        str(run_dir / "input.musicxml"),
    )
    assert result.artifacts == {"musicxml": run_dir / "input.musicxml"}
    assert score.read_bytes() == b"not really a jpeg"
    assert sorted(p.name for p in score.parent.iterdir()) == [score.name]


def test_deskew_false_adds_without_deskew(
    executable: Path, score: Path, tmp_path: Path, fake: FakeOemer
) -> None:
    result = _engine(executable).recognize(
        score, tmp_path / "out", timeout_seconds=600, options={"deskew": False}
    )

    assert result.success is True
    assert result.options == {"deskew": False}
    assert fake.recognition_calls[0][-1] == "--without-deskew"


@pytest.mark.parametrize("value", ["no", 0, None])
def test_deskew_must_be_a_bool(executable: Path, score: Path, tmp_path: Path, value) -> None:
    with pytest.raises(InvalidEngineOptionError, match="deskew"):
        _engine(executable).recognize(
            score, tmp_path / "out", timeout_seconds=600, options={"deskew": value}
        )


def test_unknown_option_is_rejected(executable: Path, score: Path, tmp_path: Path) -> None:
    with pytest.raises(UnknownEngineOptionError):
        _engine(executable).recognize(
            score, tmp_path / "out", timeout_seconds=600, options={"use_tf": True}
        )


def test_pdf_is_rejected_before_oemer_runs(
    executable: Path, tmp_path: Path, fake: FakeOemer
) -> None:
    pdf = tmp_path / "score.pdf"
    pdf.write_bytes(b"%PDF")

    result = _engine(executable).recognize(pdf, tmp_path / "out", timeout_seconds=600)

    assert result.success is False
    assert ".png, .jpg, .jpeg" in (result.error or "")
    assert fake.recognition_calls == []


def test_python_exception_is_a_failure_with_last_stderr_line(
    executable: Path, score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashed = CommandResult(
        args=(),
        returncode=1,
        stderr="Traceback (most recent call last):\n...\nIndexError: index 1 is out of bounds\n",
    )
    runner = FakeOemer(run_result=crashed, write_musicxml=False)
    monkeypatch.setattr(console_tool, "run_command", runner)

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=600)

    assert result.success is False
    assert result.returncode == 1
    assert "oemer exited with code 1" in (result.error or "")
    assert "IndexError" in (result.error or "")


def test_zero_exit_without_musicxml_is_a_failure(
    executable: Path, score: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(console_tool, "run_command", FakeOemer(write_musicxml=False))

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=600)

    assert result.success is False
    assert "wrote no input.musicxml" in (result.error or "")


def test_incomplete_console_tool_subclass_fails_at_definition() -> None:
    with pytest.raises(TypeError, match="PACKAGE_NAME.*build_command"):

        class Incomplete(console_tool.ConsoleToolEngine):  # pyright: ignore[reportUnusedClass]
            ENGINE_NAME = "x"
            EXECUTABLE_ENV_VAR = "X_EXE"
            PATH_COMMAND_NAME = "x"
            PROJECT_URL = "https://example.invalid"
            INPUT_SUFFIXES = (".png",)

            def missing_executable_message(self) -> str:
                return "missing"
