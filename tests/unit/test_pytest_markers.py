"""Check that the pytest marker configuration in pyproject.toml behaves as documented."""

import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

MARKED_TESTS = """
import pytest

def test_plain():
    pass

@pytest.mark.integration
def test_integration():
    pass

@pytest.mark.gpu
def test_gpu():
    pass

@pytest.mark.network
def test_network():
    pass
"""


def _write_project_config(pytester: pytest.Pytester) -> None:
    options = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]
    ini = options["ini_options"]
    markers = "".join(f"\n    {marker}" for marker in ini["markers"])
    # `-p pytester` is only needed by this test module itself.
    addopts = ini["addopts"].replace("-p pytester", "")
    pytester.makeini(f"[pytest]\naddopts = {addopts}\nmarkers ={markers}\n")


def test_default_run_deselects_marked_tests(pytester: pytest.Pytester) -> None:
    _write_project_config(pytester)
    pytester.makepyfile(MARKED_TESTS)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, deselected=3)


@pytest.mark.parametrize("marker", ["integration", "gpu", "network"])
def test_command_line_marker_opts_in(pytester: pytest.Pytester, marker: str) -> None:
    _write_project_config(pytester)
    pytester.makepyfile(MARKED_TESTS)
    result = pytester.runpytest("-m", marker, "-v")
    result.assert_outcomes(passed=1, deselected=3)
    result.stdout.fnmatch_lines([f"*::test_{marker} PASSED*"])


def test_unknown_marker_is_an_error(pytester: pytest.Pytester) -> None:
    _write_project_config(pytester)
    pytester.makepyfile("import pytest\n\n@pytest.mark.typo\ndef test_x():\n    pass\n")
    result = pytester.runpytest()
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*'typo' not found in `markers` configuration option*"])
