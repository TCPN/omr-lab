import errno
import json
from pathlib import Path

import pytest

from omr_lab import __version__, cli, pipeline
from omr_lab.cli import (
    EXIT_ENGINE_UNAVAILABLE,
    EXIT_INTERRUPTED,
    EXIT_INVALID_INPUT,
    EXIT_INVALID_OPTION,
    EXIT_OK,
    EXIT_OUTPUT_DIR_UNUSABLE,
    EXIT_RUN_FAILED,
    EXIT_USAGE,
    build_parser,
    main,
    parse_options,
)
from omr_lab.engines.mock import MOCK_ENGINE_VERSION, MockEngine
from omr_lab.schemas import validate_run_document

PNG_BYTES = b"\x89PNG\r\n\x1a\nnot-a-real-image"


class _UnavailableAudiveris(MockEngine):
    name = "audiveris"

    def __init__(self) -> None:
        super().__init__(available=False)


@pytest.fixture(autouse=True)
def no_real_audiveris(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unit tests must not look for Java or Audiveris on the machine running them.
    monkeypatch.setitem(cli.ENGINES, "audiveris", _UnavailableAudiveris)


@pytest.fixture
def image(tmp_path: Path) -> Path:
    path = tmp_path / "score.png"
    path.write_bytes(PNG_BYTES)
    return path


def _exit_code(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code  # type: ignore[return-value]


def _mock_run(image: Path, output: Path, *extra: str) -> list[str]:
    return ["run", str(image), "-o", str(output), "--engine", "mock", *extra]


def test_help_lists_commands_and_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(["--help"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "Local-first Optical Music Recognition pipeline" in out
    assert "doctor" in out and "run" in out
    assert "Exit codes:" in out


def test_run_help_documents_options(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(["run", "--help"]) == EXIT_OK

    out = capsys.readouterr().out
    for flag in ("--output", "--engine", "--option", "--timeout"):
        assert flag in out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert "usage:" in capsys.readouterr().out.lower()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(["--version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == f"omr-lab {__version__}"


def test_run_defaults() -> None:
    args = build_parser().parse_args(["run", "in.png", "-o", "out"])

    assert (args.engine, args.options, args.timeout) == ("audiveris", [], 600.0)
    assert (args.input, args.output) == (Path("in.png"), Path("out"))


@pytest.mark.parametrize(
    ("pair", "value"),
    [
        ("outcome=failed", "failed"),
        ('outcome="failed"', "failed"),
        ("n=3", 3),
        ("x=0.5", 0.5),
        ("flag=true", True),
        ("flag=false", False),
        ("v=null", None),
        ("s=", ""),
        ("s=a=b", "a=b"),
        ('s="3"', "3"),
        ("s=[1, 2]", "[1, 2]"),
        ("s=NaN", "NaN"),
    ],
)
def test_option_values_are_json_scalars_or_text(pair: str, value: object) -> None:
    key = pair.partition("=")[0]
    assert parse_options([pair]) == {key: value}


@pytest.mark.parametrize("option", ["novalue", "=1"])
def test_malformed_option_is_a_usage_error(
    image: Path, tmp_path: Path, option: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _exit_code(_mock_run(image, tmp_path / "run", "--option", option))

    assert code == EXIT_USAGE
    assert "KEY=VALUE" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_repeated_option_is_a_usage_error(image: Path, tmp_path: Path) -> None:
    argv = _mock_run(image, tmp_path / "run", "--option", "a=1", "--option", "a=2")

    assert _exit_code(argv) == EXIT_USAGE


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "soon"])
def test_invalid_timeout_is_a_usage_error(image: Path, tmp_path: Path, timeout: str) -> None:
    assert _exit_code(_mock_run(image, tmp_path / "run", "--timeout", timeout)) == EXIT_USAGE


def test_unknown_engine_is_a_usage_error(image: Path, tmp_path: Path) -> None:
    argv = ["run", str(image), "-o", str(tmp_path / "run"), "--engine", "nope"]

    assert _exit_code(argv) == EXIT_USAGE


def test_doctor_reports_each_engine(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["doctor"])

    out = capsys.readouterr().out
    assert code == EXIT_ENGINE_UNAVAILABLE
    assert f"omr-lab {__version__}" in out
    assert "Python " in out
    assert "[missing] audiveris: Mock engine was configured as unavailable" in out
    assert f"[ok]      mock {MOCK_ENGINE_VERSION}" in out


def test_doctor_for_one_available_engine_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--engine", "mock"]) == EXIT_OK
    assert "audiveris" not in capsys.readouterr().out


def test_run_with_mock_engine_writes_run_document(
    image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"

    code = main(_mock_run(image, output))

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert f"Run document: {output / 'run.json'}" in captured.out
    assert "Status: succeeded (engine mock)" in captured.out
    document = json.loads((output / "run.json").read_text(encoding="utf-8"))
    validate_run_document(document)
    assert document["status"] == "succeeded"


def test_failed_run_records_options_and_reports_the_error(
    image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"

    code = main(_mock_run(image, output, "--option", "outcome=failed"))

    document = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert code == EXIT_RUN_FAILED
    assert document["options"] == {"outcome": "failed"}
    err = capsys.readouterr().err
    assert "Status: failed [engine_failed]: Mock engine simulated a failure" in err


def test_missing_input_exits_with_invalid_input(tmp_path: Path) -> None:
    code = main(_mock_run(tmp_path / "missing.png", tmp_path / "run"))

    assert code == EXIT_INVALID_INPUT
    assert (tmp_path / "run" / "run.json").exists()


def test_unavailable_engine_exits_with_engine_unavailable(image: Path, tmp_path: Path) -> None:
    assert main(["run", str(image), "-o", str(tmp_path / "run")]) == EXIT_ENGINE_UNAVAILABLE


def test_unknown_engine_option_exits_with_invalid_option(
    image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_mock_run(image, tmp_path / "run", "--option", "colour=red")) == (
        EXIT_INVALID_OPTION
    )
    assert "[invalid_option]" in capsys.readouterr().err


def test_non_empty_output_dir_exits_without_run_document(
    image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "keep.txt").write_text("earlier result", encoding="utf-8")

    code = main(_mock_run(image, output))

    assert code == EXIT_OUTPUT_DIR_UNUSABLE
    assert "is not empty" in capsys.readouterr().err
    assert sorted(p.name for p in output.iterdir()) == ["keep.txt"]


def test_aborted_run_directory_is_reported(
    image: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    (output / "raw").mkdir(parents=True)

    code = main(_mock_run(image, output))

    assert code == EXIT_OUTPUT_DIR_UNUSABLE
    assert "holds an aborted run" in capsys.readouterr().err
    assert not (output / "run.json").exists()


def test_interrupt_before_the_run_document_exits_with_130(
    image: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupt(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_pipeline", interrupt)

    assert main(_mock_run(image, tmp_path / "run")) == EXIT_INTERRUPTED
    assert "interrupted before the run document was written" in capsys.readouterr().err


def test_interrupt_during_the_engine_points_to_the_run_document(
    image: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupt(self: MockEngine, *args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(MockEngine, "recognize", interrupt)
    output = tmp_path / "run"

    assert main(_mock_run(image, output)) == EXIT_INTERRUPTED
    assert f"interrupted; see {output / 'run.json'}" in capsys.readouterr().err
    document = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert document["error"]["code"] == "interrupted"


def test_input_name_the_run_document_cannot_store_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = tmp_path / "bad\nname.png"
    name.write_bytes(PNG_BYTES)

    code = main(_mock_run(name, tmp_path / "run"))

    assert code == EXIT_USAGE
    assert "omr-lab: error:" in capsys.readouterr().err


def test_run_document_that_cannot_be_written_is_reported(
    image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def disk_full(path: Path, text: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pipeline, "_write_atomically", disk_full)
    output = tmp_path / "run"

    code = main(_mock_run(image, output))

    assert code == EXIT_OUTPUT_DIR_UNUSABLE
    err = capsys.readouterr().err
    assert "could not be written (No space left on device)" in err
    assert "aborted run" in err
    assert (output / "raw").is_dir() and not (output / "run.json").exists()
