"""Integration tests that run a real Audiveris installation.

Deselected by default; run with ``uv run pytest -m integration``. They skip when
Audiveris cannot be found (see docs/engines/audiveris.md for setup).

Recognition needs a score image. No redistributable sample is committed yet
(sample provenance is issue #10 and decision #29), so the recognition test reads
the image path from the ``OMR_LAB_AUDIVERIS_SAMPLE`` environment variable and
skips when it is not set.
"""

import os
from pathlib import Path

import pytest

from omr_lab.engines import AudiverisEngine

pytestmark = pytest.mark.integration

SAMPLE_ENV_VAR = "OMR_LAB_AUDIVERIS_SAMPLE"


@pytest.fixture(scope="module")
def engine() -> AudiverisEngine:
    engine = AudiverisEngine()
    lookup = engine.find_executable()
    if lookup.path is None:
        pytest.skip(f"Audiveris not installed: {lookup.message}")
    return engine


def test_check_available_reports_version(engine: AudiverisEngine) -> None:
    availability = engine.check_available()

    assert availability.available is True, availability.message
    assert availability.version, availability.message


def test_recognize_sample_produces_musicxml(engine: AudiverisEngine, tmp_path: Path) -> None:
    sample = os.environ.get(SAMPLE_ENV_VAR)
    if not sample:
        pytest.skip(
            f"No redistributable sample score is committed yet (issue #10, decision #29); "
            f"set {SAMPLE_ENV_VAR} to a local score image to run this test."
        )
    sample_path = Path(sample)
    original = sample_path.read_bytes()

    result = engine.recognize(sample_path, tmp_path / "out", timeout_seconds=600)

    assert result.success is True, (result.error, result.diagnostics)
    assert result.artifacts
    assert "musicxml" in result.artifacts
    assert all(path.is_file() for path in result.artifacts.values())
    assert sample_path.read_bytes() == original
    # A produced file is not proof of correct recognition; see docs/engine-interface.md.
