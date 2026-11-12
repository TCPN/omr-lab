"""Unit tests for the homr adapter; process execution is mocked throughout."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from omr_lab.engines import CommandResult, Engine, HomrEngine, UnknownEngineOptionError
from omr_lab.engines import homr as homr_module


def _no_which(name: str) -> str | None:
    return None


class FakeRunner:
    """Replaces run_command: answers the version script and ``--help``, simulates homr."""

    def __init__(
        self,
        *,
        version_result: CommandResult | None = None,
        help_result: CommandResult | None = None,
        run_result: Callable[[tuple[str, ...]], CommandResult] | None = None,
        write_musicxml: bool = True,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.version_result = version_result
        self.help_result = help_result
        self.run_result = run_result
        self.write_musicxml = write_musicxml

    def __call__(self, args, *, timeout_seconds: float, cwd=None) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append((command, timeout_seconds))
        if "-c" in command:
            return self.version_result or CommandResult(
                args=command, returncode=0, stdout="0.7.1\n"
            )
        if command[1] == "--help":
            return self.help_result or CommandResult(args=command, returncode=0, stdout="usage")
        image = Path(command[1])
        if self.write_musicxml:
            image.with_suffix(".musicxml").write_text("<score-partwise/>", encoding="utf-8")
            image.with_name(f"{image.stem}_teaser.png").write_bytes(b"png")
        if self.run_result is not None:
            return self.run_result(command)
        return CommandResult(args=command, returncode=0, stderr="Finished\n")

    @property
    def recognition_calls(self) -> list[tuple[str, ...]]:
        return [c for c, _ in self.calls if "-c" not in c and c[1] != "--help"]


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    """A fake console script next to its environment's interpreter."""
    bin_dir = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    bin_dir.mkdir(parents=True)
    script = bin_dir / ("homr.exe" if sys.platform == "win32" else "homr")
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / ("python.exe" if sys.platform == "win32" else "python")).write_text(
        "", encoding="utf-8"
    )
    return script


@pytest.fixture
def score(tmp_path: Path) -> Path:
    path = tmp_path / "in" / "My Score.PNG"
    path.parent.mkdir()
    path.write_bytes(b"not really a png")
    return path


def _install(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> FakeRunner:
    monkeypatch.setattr(homr_module, "run_command", runner)
    return runner


def _engine(executable: Path | None = None, **kwargs) -> HomrEngine:
    kwargs.setdefault("environ", {})
    kwargs.setdefault("which", _no_which)
    return HomrEngine(executable, **kwargs)


# --- discovery and availability -------------------------------------------


def test_implements_engine_protocol() -> None:
    engine: Engine = _engine()
    assert isinstance(engine, Engine)
    assert engine.name == "homr"


def test_lookup_order_explicit_env_path(executable: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.write_text("", encoding="utf-8")

    explicit = _engine(executable, environ={"HOMR_EXE": str(other)}, which=lambda n: str(other))
    from_env = _engine(environ={"HOMR_EXE": str(executable)}, which=lambda n: str(other))
    from_path = _engine(which=lambda n: str(executable) if n == "homr" else None)

    assert explicit.find_executable() == (executable, None)
    assert from_env.find_executable() == (executable, None)
    assert from_path.find_executable() == (executable, None)


def test_wrong_env_var_is_reported_without_path_fallback(executable: Path) -> None:
    engine = _engine(environ={"HOMR_EXE": "/nowhere/homr"}, which=lambda n: str(executable))

    path, message = engine.find_executable()

    assert path is None
    assert "HOMR_EXE" in (message or "") and "/nowhere/homr" in (message or "")


def test_missing_homr_is_unavailable_with_install_hint() -> None:
    availability = _engine().check_available()

    assert availability.available is False
    assert "uv tool install" in (availability.message or "")
    assert "HOMR_EXE" in (availability.message or "")


def test_version_comes_from_the_environment_interpreter(
    monkeypatch: pytest.MonkeyPatch, executable: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())

    availability = _engine(executable).check_available()

    assert availability.available is True
    assert availability.version == "0.7.1"
    command = runner.calls[0][0]
    assert Path(command[0]).parent == executable.parent
    assert command[1:3] == ("-I", "-c")


def test_symlinked_shim_finds_the_interpreter_of_its_target(
    monkeypatch: pytest.MonkeyPatch, executable: Path, tmp_path: Path
) -> None:
    shim = tmp_path / "local-bin" / "homr"
    shim.parent.mkdir()
    try:
        shim.symlink_to(executable)
    except OSError:
        pytest.skip("symlinks are not available")
    _install(monkeypatch, FakeRunner())

    availability = _engine(shim).check_available()

    assert availability.version == "0.7.1"


def test_without_interpreter_help_decides_and_version_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lone = tmp_path / "homr"
    lone.write_text("", encoding="utf-8")
    runner = _install(monkeypatch, FakeRunner())

    availability = _engine(lone).check_available()

    assert availability.available is True
    assert availability.version is None
    assert "version could not be read" in (availability.message or "")
    assert runner.calls[-1][0][1] == "--help"


def test_failing_help_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lone = tmp_path / "homr"
    lone.write_text("", encoding="utf-8")
    broken = CommandResult(args=(), returncode=1, stderr="ModuleNotFoundError: cv2\n")
    _install(monkeypatch, FakeRunner(help_result=broken))

    availability = _engine(lone).check_available()

    assert availability.available is False
    assert "exit code 1" in (availability.message or "")
    assert "ModuleNotFoundError: cv2" in (availability.message or "")


def test_unusable_version_output_falls_back_to_help(
    monkeypatch: pytest.MonkeyPatch, executable: Path
) -> None:
    noisy = CommandResult(args=(), returncode=0, stdout="Traceback (most recent call last)\n")
    _install(monkeypatch, FakeRunner(version_result=noisy))

    availability = _engine(executable).check_available()

    assert availability.available is True
    assert availability.version is None


def test_availability_is_cached(monkeypatch: pytest.MonkeyPatch, executable: Path) -> None:
    runner = _install(monkeypatch, FakeRunner())
    engine = _engine(executable)

    engine.check_available()
    engine.check_available()

    assert len(runner.calls) == 1


# --- recognition ----------------------------------------------------------


def test_recognize_runs_homr_on_a_copy_and_reports_musicxml(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    original = score.read_bytes()

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=42)

    assert result.success is True, result.error
    assert result.version == "0.7.1"
    assert result.returncode == 0
    assert result.options == {}
    (command,) = runner.recognition_calls
    work_input = Path(command[1])
    assert command[0] == str(executable)
    assert work_input.is_absolute()
    assert work_input.name == "input.png"
    assert work_input.parent.parent == tmp_path / "out"
    assert work_input.parent.name.startswith("homr-")
    assert work_input.read_bytes() == original
    assert result.artifacts == {"musicxml": work_input.with_suffix(".musicxml")}
    assert score.read_bytes() == original
    assert sorted(p.name for p in score.parent.iterdir()) == ["My Score.PNG"]
    assert runner.calls[-1][1] == 42
    assert any(line.startswith("run directory: ") for line in result.diagnostics)


def test_each_run_gets_its_own_directory(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    engine = _engine(executable)

    engine.recognize(score, tmp_path / "out", timeout_seconds=5)
    engine.recognize(score, tmp_path / "out", timeout_seconds=5)

    first, second = (Path(c[1]).parent for c in runner.recognition_calls)
    assert first != second


@pytest.mark.parametrize("name", ["score.pdf", "score.jpeg", "score.JPG"])
def test_accepted_input_types_keep_their_suffix(
    monkeypatch: pytest.MonkeyPatch, executable: Path, tmp_path: Path, name: str
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    source = tmp_path / name
    source.write_bytes(b"x")

    result = _engine(executable).recognize(source, tmp_path / "out", timeout_seconds=5)

    assert result.success is True
    assert Path(runner.recognition_calls[0][1]).suffix == Path(name).suffix.lower()


def test_unsupported_input_type_fails_before_running(
    monkeypatch: pytest.MonkeyPatch, executable: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    source = tmp_path / "score.tiff"
    source.write_bytes(b"x")

    result = _engine(executable).recognize(source, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert ".tiff" in (result.error or "") and "PNG" in (result.error or "")
    assert runner.calls == []


def test_missing_input_fails(executable: Path, tmp_path: Path) -> None:
    result = _engine(executable).recognize(tmp_path / "nope.png", tmp_path, timeout_seconds=5)

    assert result.success is False
    assert "does not exist" in (result.error or "")


def test_unavailable_homr_fails_with_its_message(score: Path, tmp_path: Path) -> None:
    result = _engine().recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert "homr was not found" in (result.error or "")


def test_output_dir_that_is_a_file_fails(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    runner = _install(monkeypatch, FakeRunner())
    blocker = tmp_path / "out"
    blocker.write_text("", encoding="utf-8")

    result = _engine(executable).recognize(score, blocker, timeout_seconds=5)

    assert result.success is False
    assert "not a directory" in (result.error or "")
    assert runner.calls == []


def test_nonzero_exit_is_failure_with_last_stderr_line(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    def failed(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(
            args=command,
            returncode=1,
            stderr=(
                "Processing x\nAn error occurred while processing x: No noteheads or clefs found\n"
            ),
        )

    _install(monkeypatch, FakeRunner(run_result=failed, write_musicxml=False))

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert result.timed_out is False
    assert result.returncode == 1
    assert "code 1" in (result.error or "")
    assert "No noteheads or clefs found" in (result.error or "")
    assert result.artifacts == {}


def test_timeout_is_reported_as_timed_out(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    def hung(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(
            args=command, timed_out=True, error="Command timed out after 5 seconds"
        )

    _install(monkeypatch, FakeRunner(run_result=hung, write_musicxml=False))

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert result.timed_out is True
    assert result.returncode is None
    assert "timed out" in (result.error or "")


def test_zero_exit_without_musicxml_is_failure(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    _install(monkeypatch, FakeRunner(write_musicxml=False))

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is False
    assert "wrote no input.musicxml" in (result.error or "")


def test_long_output_lines_are_shortened_in_diagnostics(
    monkeypatch: pytest.MonkeyPatch, executable: Path, score: Path, tmp_path: Path
) -> None:
    def chatty(command: tuple[str, ...]) -> CommandResult:
        return CommandResult(args=command, returncode=0, stderr="Writing XML " + "x" * 5000)

    _install(monkeypatch, FakeRunner(run_result=chatty))

    result = _engine(executable).recognize(score, tmp_path / "out", timeout_seconds=5)

    assert result.success is True
    assert max(len(line) for line in result.diagnostics if "Writing XML" in line) < 400


def test_options_are_rejected(executable: Path, score: Path, tmp_path: Path) -> None:
    with pytest.raises(UnknownEngineOptionError):
        _engine(executable).recognize(
            score, tmp_path / "out", timeout_seconds=5, options={"device": "cuda"}
        )
