"""Unit tests for the Audiveris adapter; process execution is mocked throughout."""

import os
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from omr_lab.engines import (
    AudiverisEngine,
    CommandResult,
    Engine,
    UnknownEngineOptionError,
)
from omr_lab.engines import audiveris as audiveris_module

VERSION_OUTPUT = """Audiveris
- Version:      5.10.0
- Commit:       7cd25598ed15dcf1fd041ab60c1e7543f55588d4
- OS:           Windows 10 10.0
- Architecture: amd64
- Java VM:      Java HotSpot(TM) 64-Bit Server VM (build 25+37-LTS-3491, mixed mode, sharing)
- OCR Engine:   Tesseract OCR, version 5.5.1
"""


def _no_which(name: str) -> str | None:
    return None


class FakeRunner:
    """Replaces run_command; answers -version and simulates a batch run."""

    def __init__(
        self,
        *,
        version_result: CommandResult | None = None,
        batch_result: Callable[[tuple[str, ...]], CommandResult] | None = None,
        write: Sequence[str] = ("score.mxl",),
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.version_result = version_result
        self.batch_result = batch_result
        self.write = write

    def __call__(self, args, *, timeout_seconds: float, cwd=None) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append((command, timeout_seconds))
        if command[1] == "-version":
            return self.version_result or CommandResult(
                args=command, returncode=0, stdout=VERSION_OUTPUT
            )
        output_dir = Path(command[command.index("-output") + 1])
        for name in self.write:
            target = output_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"PK\x03\x04 fake mxl")
        if self.batch_result is not None:
            return self.batch_result(command)
        return CommandResult(args=command, returncode=0, stdout="INFO done\n")


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "Audiveris"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return path


@pytest.fixture
def score(tmp_path: Path) -> Path:
    path = tmp_path / "in" / "score.png"
    path.parent.mkdir()
    path.write_bytes(b"not really a png")
    return path


def _install(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> FakeRunner:
    monkeypatch.setattr(audiveris_module, "run_command", runner)
    return runner


def _engine(launcher: Path | None = None, **kwargs) -> AudiverisEngine:
    kwargs.setdefault("environ", {})
    kwargs.setdefault("which", _no_which)
    return AudiverisEngine(launcher, **kwargs)


# --- executable discovery -------------------------------------------------


def test_implements_engine_protocol() -> None:
    engine: Engine = _engine()
    assert isinstance(engine, Engine)
    assert engine.name == "audiveris"


def test_explicit_executable_wins_over_env_and_path(launcher: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.write_text("", encoding="utf-8")
    engine = _engine(launcher, environ={"AUDIVERIS_EXE": str(other)}, which=lambda n: str(other))

    assert engine.find_executable() == (launcher, None)


def test_env_var_used_before_path(launcher: Path) -> None:
    engine = _engine(environ={"AUDIVERIS_EXE": str(launcher)}, which=lambda n: "/usr/bin/x")

    assert engine.find_executable() == (launcher, None)


def test_env_var_pointing_nowhere_is_reported(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "Audiveris"
    engine = _engine(environ={"AUDIVERIS_EXE": str(missing)}, which=lambda n: "/usr/bin/x")

    path, message = engine.find_executable()

    assert path is None
    assert "AUDIVERIS_EXE" in (message or "")
    assert str(missing) in (message or "")


def test_explicit_missing_executable_is_reported(tmp_path: Path) -> None:
    path, message = _engine(tmp_path / "missing").find_executable()

    assert path is None
    assert "passed explicitly" in (message or "")


def test_path_lookup_tries_documented_launcher_names() -> None:
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return "/opt/audiveris/bin/Audiveris" if name == "Audiveris" else None

    path, _ = _engine(which=which).find_executable()

    assert asked == ["audiveris", "Audiveris"]
    assert path == Path("/opt/audiveris/bin/Audiveris")


@pytest.mark.parametrize("suffix", [".bat", ".cmd", ".BAT"])
def test_explicit_batch_launcher_is_refused(tmp_path: Path, suffix: str) -> None:
    batch = tmp_path / f"Audiveris{suffix}"
    batch.write_text("@echo off\n", encoding="utf-8")

    for engine in (_engine(batch), _engine(environ={"AUDIVERIS_EXE": str(batch)})):
        path, message = engine.find_executable()
        assert path is None
        assert "batch file" in (message or "")
        assert "Audiveris.exe" in (message or "")


def test_batch_launcher_on_path_is_skipped(tmp_path: Path) -> None:
    batch = tmp_path / "Audiveris.bat"
    exe = tmp_path / "audiveris.exe"
    found = {"Audiveris": str(batch), "audiveris": None}

    path, message = _engine(which=found.get).find_executable()
    assert path is None
    assert "batch file" in (message or "")

    found["audiveris"] = str(exe)
    assert _engine(which=found.get).find_executable() == (exe, None)


def test_relative_explicit_launcher_is_made_absolute(
    monkeypatch: pytest.MonkeyPatch, launcher: Path
) -> None:
    monkeypatch.chdir(launcher.parent)

    path, message = _engine(Path(launcher.name)).find_executable()

    assert message is None
    assert path is not None and path.is_absolute()
    assert path == launcher


def test_default_install_location_is_used(monkeypatch: pytest.MonkeyPatch, launcher: Path) -> None:
    monkeypatch.setattr(audiveris_module, "_default_launchers", lambda: (launcher,))

    assert _engine().find_executable() == (launcher, None)


def test_missing_audiveris_gives_actionable_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(audiveris_module, "_default_launchers", lambda: (tmp_path / "none",))
    runner = _install(monkeypatch, FakeRunner())

    availability = _engine().check_available()

    assert availability.available is False
    assert availability.version is None
    message = availability.message or ""
    assert "https://github.com/Audiveris/audiveris/releases" in message
    assert "AUDIVERIS_EXE" in message
    assert "PATH" in message
    assert runner.calls == []


# --- version detection ----------------------------------------------------


def test_parse_version_from_handbook_sample() -> None:
    assert audiveris_module.parse_version(VERSION_OUTPUT) == "5.10.0"
    assert audiveris_module.parse_version("no version here") is None


def test_check_available_reports_version(monkeypatch: pytest.MonkeyPatch, launcher: Path) -> None:
    runner = _install(monkeypatch, FakeRunner())

    availability = _engine(launcher, version_timeout_seconds=7).check_available()

    assert availability.available is True
    assert availability.version == "5.10.0"
    assert runner.calls == [((str(launcher), "-version"), 7)]


def test_check_available_runs_version_command_once(
    monkeypatch: pytest.MonkeyPatch, launcher: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    engine = _engine(launcher)

    first = engine.check_available()
    second = engine.check_available()

    assert first is second
    assert len(runner.calls) == 1


def test_failed_availability_is_cached_and_reused_by_recognize(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    failed = CommandResult(args=(), returncode=1, stderr="Error: no Java runtime\n")
    runner = _install(monkeypatch, FakeRunner(version_result=failed))
    engine = _engine(launcher)

    availability = engine.check_available()
    result = engine.recognize(score, tmp_path / "out", timeout_seconds=5)

    assert engine.check_available() is availability
    assert availability.available is False
    assert result.success is False
    assert result.error == availability.message
    assert [call[0][1] for call in runner.calls] == ["-version"]


def test_check_available_when_version_command_fails(
    monkeypatch: pytest.MonkeyPatch, launcher: Path
) -> None:
    failed = CommandResult(args=(), returncode=1, stderr="Error: no Java runtime\n")
    _install(monkeypatch, FakeRunner(version_result=failed))

    availability = _engine(launcher).check_available()

    assert availability.available is False
    assert "exit code 1" in (availability.message or "")
    assert "no Java runtime" in (availability.message or "")


def test_check_available_with_unparseable_version(
    monkeypatch: pytest.MonkeyPatch, launcher: Path
) -> None:
    _install(monkeypatch, FakeRunner(version_result=CommandResult(args=(), returncode=0)))

    availability = _engine(launcher).check_available()

    assert availability.available is True
    assert availability.version is None
    assert "could not be parsed" in (availability.message or "")


# --- recognition ----------------------------------------------------------


def test_recognize_builds_verified_batch_command(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    output_dir = tmp_path / "out dir"

    result = _engine(launcher).recognize(score, output_dir, timeout_seconds=123)

    assert runner.calls[-1] == (
        (
            str(launcher),
            "-batch",
            "-transcribe",
            "-export",
            "-output",
            str(output_dir),
            "--",
            str(score),
        ),
        123,
    )
    assert result.success is True
    assert result.error is None
    assert result.returncode == 0
    assert result.version == "5.10.0"
    assert result.artifacts == {"musicxml": output_dir / "score.mxl"}
    assert result.options == {}
    assert any("INFO done" in line for line in result.diagnostics)
    assert score.read_bytes() == b"not really a png"


def test_version_is_detected_once(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    engine = _engine(launcher)

    engine.check_available()
    engine.recognize(score, tmp_path / "a", timeout_seconds=5)
    engine.recognize(score, tmp_path / "b", timeout_seconds=5)

    assert [call[0][1] for call in runner.calls] == ["-version", "-batch", "-batch"]


@pytest.mark.parametrize("name", ["sheets", "device"])
def test_no_options_are_supported_yet(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path, name: str
) -> None:
    # -sheets is deferred until its multi-value parsing is verified on a real install.
    runner = _install(monkeypatch, FakeRunner())

    with pytest.raises(UnknownEngineOptionError, match=r"Supported options: \(none\)"):
        _engine(launcher).recognize(score, tmp_path / "out", timeout_seconds=5, options={name: "1"})
    assert runner.calls == []


def test_missing_executable_returns_failure_result(
    monkeypatch: pytest.MonkeyPatch, score: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(audiveris_module, "_default_launchers", lambda: ())
    runner = _install(monkeypatch, FakeRunner())

    result = _engine().recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert "AUDIVERIS_EXE" in (result.error or "")
    assert result.options == {}
    assert runner.calls == []


def test_missing_input_returns_failure(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())

    result = _engine(launcher).recognize(tmp_path / "x.png", tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert "does not exist" in (result.error or "")
    assert runner.calls == []


def test_omr_project_input_is_refused(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    book = tmp_path / "book.omr"
    book.write_bytes(b"omr")

    result = _engine(launcher).recognize(book, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert ".omr" in (result.error or "")
    assert runner.calls == []


def test_output_that_would_overwrite_input_is_refused(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    source = tmp_path / "score.mxl"
    source.write_bytes(b"original")

    result = _engine(launcher).recognize(source, tmp_path, timeout_seconds=5)

    assert result.success is False
    assert "over the input" in (result.error or "")
    assert source.read_bytes() == b"original"
    assert runner.calls == []


def test_hard_linked_output_that_would_overwrite_input_is_refused(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    source = tmp_path / "input.png"
    source.write_bytes(b"original")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    try:
        os.link(source, output_dir / "input.mxl")
    except OSError as exc:
        pytest.skip(f"hard links not supported here: {exc}")

    result = _engine(launcher).recognize(source, output_dir, timeout_seconds=5)

    assert result.success is False
    assert "over the input" in (result.error or "")
    assert source.read_bytes() == b"original"
    assert runner.calls == []


def test_uncompressed_xml_output_is_collected(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    # Audiveris writes <stem>.xml when its compression preference is disabled.
    _install(monkeypatch, FakeRunner(write=("score.xml",)))
    output_dir = tmp_path / "out"

    result = _engine(launcher).recognize(score, output_dir, timeout_seconds=5)

    assert result.success is True
    assert result.artifacts == {"musicxml": output_dir / "score.xml"}


def test_nonzero_exit_is_failure_even_with_artifact(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    def batch(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(args=command, returncode=3, stderr="SEVERE boom\n")

    _install(monkeypatch, FakeRunner(batch_result=batch))

    result = _engine(launcher).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert result.returncode == 3
    assert "code 3" in (result.error or "")
    assert "musicxml" in result.artifacts  # raw output is kept for inspection
    assert any("SEVERE boom" in line for line in result.diagnostics)


def test_timeout_is_failure(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    def batch(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(
            args=command, timed_out=True, error="Command timed out after 5 seconds"
        )

    _install(monkeypatch, FakeRunner(batch_result=batch, write=()))

    result = _engine(launcher).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert result.returncode is None
    assert "timed out" in (result.error or "")


def test_zero_exit_without_artifact_is_failure(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    _install(monkeypatch, FakeRunner(write=("score.omr",)))

    result = _engine(launcher).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert result.returncode == 0
    assert "wrote no .mxl/.xml/.musicxml" in (result.error or "")
    assert result.artifacts == {}


def test_output_dir_with_existing_musicxml_is_refused(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    output_dir = tmp_path / "out"
    (output_dir / "sub").mkdir(parents=True)
    (output_dir / "sub" / "old.mxl").write_bytes(b"old run")
    runner = FakeRunner(write=("score.mxl",))
    _install(monkeypatch, runner)

    result = _engine(launcher).recognize(score, output_dir, timeout_seconds=5)

    assert result.success is False
    assert result.artifacts == {}
    assert "already contains MusicXML (sub/old.mxl)" in (result.error or "")
    assert runner.calls == []


def test_multiple_artifacts_are_all_reported(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    _install(monkeypatch, FakeRunner(write=("score.mvt1.mxl", "sub/score.mvt2.musicxml")))
    output_dir = tmp_path / "out"

    result = _engine(launcher).recognize(score, output_dir, timeout_seconds=5)

    assert result.success is True
    assert result.artifacts == {
        "musicxml:score.mvt1.mxl": output_dir / "score.mvt1.mxl",
        "musicxml:sub/score.mvt2.musicxml": output_dir / "sub" / "score.mvt2.musicxml",
    }


def test_long_output_is_truncated_in_diagnostics(
    monkeypatch: pytest.MonkeyPatch, launcher: Path, score: Path, tmp_path: Path
) -> None:
    stdout = "".join(f"line {i}\n" for i in range(100))

    def batch(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(args=command, returncode=0, stdout=stdout)

    _install(monkeypatch, FakeRunner(batch_result=batch))

    result = _engine(launcher).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert "stdout (last 20 of 100 lines):" in result.diagnostics
    assert "line 99" in result.diagnostics
    assert "line 79" not in result.diagnostics
