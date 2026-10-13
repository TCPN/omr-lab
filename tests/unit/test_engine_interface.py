import json
import os
import signal
import subprocess
import sys
import time
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


class FakePopen:
    """Minimal stand-in for subprocess.Popen used by run_command."""

    def __init__(
        self,
        args,
        *,
        output: bytes = b"",
        errors: bytes = b"",
        returncode: int = 0,
        timeout_stderr: bytes | None = None,
        **kwargs,
    ) -> None:
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode: int | None = None
        self.stdout = None
        self.stderr = None
        self.killed = False
        self._out = output
        self._err = errors
        self._final_returncode = returncode
        self._timeout_stderr = timeout_stderr
        self.communicate_timeouts: list[float | None] = []

    def __enter__(self) -> "FakePopen":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        self.communicate_timeouts.append(timeout)
        if self._timeout_stderr is not None and not self.killed:
            raise subprocess.TimeoutExpired(self.args, timeout, stderr=self._timeout_stderr)
        self.returncode = -9 if self.killed else self._final_returncode
        return self._out, self._err

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


def _install_fake_popen(
    monkeypatch: pytest.MonkeyPatch, **behavior: object
) -> dict[str, FakePopen]:
    created: dict[str, FakePopen] = {}

    def fake_popen(args, **kwargs):
        created["process"] = FakePopen(args, **behavior, **kwargs)
        return created["process"]

    monkeypatch.setattr("omr_lab.engines.base.subprocess.Popen", fake_popen)
    monkeypatch.setattr("omr_lab.engines.base.os.killpg", lambda pid, sig: None, raising=False)
    monkeypatch.setattr(
        "omr_lab.engines.base.subprocess.run",
        lambda *args, **kwargs: None,  # taskkill on Windows
    )
    return created


def test_run_command_passes_arguments_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_fake_popen(monkeypatch, output=b"ok")

    result = run_command(["tool path/with spaces", "input file.png"], timeout_seconds=5)

    process = created["process"]
    assert result.succeeded is True
    assert result.stdout == "ok"
    assert process.args == ("tool path/with spaces", "input file.png")
    assert process.kwargs["stdout"] is subprocess.PIPE
    assert process.kwargs["stderr"] is subprocess.PIPE
    assert process.communicate_timeouts == [5]
    assert "shell" not in process.kwargs
    assert "text" not in process.kwargs
    if sys.platform == "win32":
        assert process.kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert process.kwargs["start_new_session"] is True


def test_run_command_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _install_fake_popen(monkeypatch, errors=b"still working", timeout_stderr=b"still")

    result = run_command(["slow-tool"], timeout_seconds=0.25)

    assert created["process"].killed is True
    assert result.succeeded is False
    assert result.timed_out is True
    assert result.returncode is None
    assert result.stderr == "still working"
    assert "timed out" in (result.error or "")


def test_run_command_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_popen(*args, **kwargs):
        raise FileNotFoundError("not installed")

    monkeypatch.setattr("omr_lab.engines.base.subprocess.Popen", fake_popen)
    result = run_command(["missing-tool"], timeout_seconds=1)

    assert result.succeeded is False
    assert "Unable to start command" in (result.error or "")


def test_run_command_reports_real_missing_executable(tmp_path: Path) -> None:
    result = run_command([tmp_path / "does-not-exist"], timeout_seconds=1)

    assert result.succeeded is False
    assert result.returncode is None
    assert "Unable to start command" in (result.error or "")


NON_ASCII_TEXT = "\u6a02\u8b5c \u00e9\u00e8 \u266f\u266d"  # 樂譜 éè ♯♭


def _non_utf8_locale_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("LANG", None)
    env.pop("LC_CTYPE", None)
    env["LC_ALL"] = "C"
    env["PYTHONUTF8"] = "0"  # Disable UTF-8 mode.
    env["PYTHONCOERCECLOCALE"] = "0"  # Keep the C locale (ASCII), do not coerce to C.UTF-8.
    env["PYTHONIOENCODING"] = "utf-8"  # The engine-like grandchild writes UTF-8.
    return env


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX locale environment variables")
def test_run_command_decodes_utf8_output_regardless_of_locale() -> None:
    # Run run_command inside an interpreter whose locale encoding is ASCII, as a
    # stand-in for cp950 on Traditional Chinese Windows. Its child writes UTF-8
    # bytes, which must still be decoded as UTF-8 rather than with the locale.
    child = (
        "import sys\n"
        f"sys.stdout.write({NON_ASCII_TEXT!a} + '\\n')\n"
        f"sys.stderr.write('err ' + {NON_ASCII_TEXT!a})\n"
    )
    parent = (
        "import json, locale, sys\n"
        "from omr_lab.engines import run_command\n"
        "result = run_command([sys.executable, '-c', sys.argv[1]], timeout_seconds=10)\n"
        "print(json.dumps({'encoding': locale.getpreferredencoding(False),\n"
        "    'succeeded': result.succeeded, 'error': result.error,\n"
        "    'stdout': result.stdout, 'stderr': result.stderr}))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", parent, child],
        env=_non_utf8_locale_env(),
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    report = json.loads(completed.stdout)
    assert report["encoding"].lower().replace("-", "") in {"ascii", "ansi_x3.41968"}
    assert report["succeeded"] is True, report
    assert report["stdout"] == NON_ASCII_TEXT + "\n"
    assert report["stderr"] == "err " + NON_ASCII_TEXT


def test_run_command_replaces_invalid_utf8_bytes() -> None:
    script = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"

    result = run_command([sys.executable, "-c", script], timeout_seconds=10)

    assert result.succeeded is True
    assert result.stdout == "ok \ufffd\ufffd end"


def test_run_command_real_timeout_keeps_partial_output() -> None:
    script = (
        "import sys, time\n"
        f"sys.stdout.write({NON_ASCII_TEXT!a})\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    started = time.monotonic()
    result = run_command(
        [sys.executable, "-c", script],
        timeout_seconds=1,
    )

    assert time.monotonic() - started < 10
    assert result.timed_out is True
    assert result.returncode is None
    assert result.stdout == NON_ASCII_TEXT


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_run_command_timeout_kills_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"with open({str(pid_file)!r}, 'w') as handle:\n"
        "    handle.write(str(grandchild.pid))\n"
        "time.sleep(60)\n"
    )

    result = run_command([sys.executable, "-c", script], timeout_seconds=1)

    assert result.timed_out is True
    grandchild_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _pid_alive(grandchild_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _pid_alive(grandchild_pid):
        os.kill(grandchild_pid, signal.SIGKILL)  # Do not leak it from the test run.
        pytest.fail(f"grandchild {grandchild_pid} survived the timeout")


def test_run_command_validates_arguments() -> None:
    with pytest.raises(ValueError, match="executable"):
        run_command([], timeout_seconds=1)
    with pytest.raises(ValueError, match="greater than zero"):
        run_command(["tool"], timeout_seconds=0)
