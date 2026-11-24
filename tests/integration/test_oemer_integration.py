"""Integration tests that run a real oemer installation (issue #25).

Deselected by default; run with ``uv run pytest -m integration -s``. They skip when
oemer cannot be found (see docs/engines/oemer.md for setup). oemer downloads its
models (about 200 MB) on its first run, so run it once on any image before running
these tests offline. A run takes several minutes on a CPU.

The tests assert only that oemer produced output: a recognized MusicXML file that
loads, a ``score``, and an unchanged input file. Musical correctness against the
committed sample's ground truth is printed, not asserted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from e2e_report import IMAGE, correctness, expected_score

from omr_lab.engines import OemerEngine
from omr_lab.musicxml import load_musicxml
from omr_lab.score import score_from_musicxml

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine() -> OemerEngine:
    engine = OemerEngine()
    lookup = engine.find_executable()
    if lookup.path is None:
        pytest.skip(f"oemer not installed: {lookup.message}")
    return engine


def test_check_available_reports_version(engine: OemerEngine) -> None:
    availability = engine.check_available()

    assert availability.available is True, availability.message
    assert availability.version, availability.message


def test_recognize_sample_produces_musicxml(engine: OemerEngine, tmp_path: Path) -> None:
    original = IMAGE.read_bytes()

    result = engine.recognize(IMAGE, tmp_path / "out", timeout_seconds=1800)

    assert result.success is True, (result.error, result.diagnostics)
    assert result.timed_out is False
    assert IMAGE.read_bytes() == original
    score = score_from_musicxml(load_musicxml(result.artifacts["musicxml"])).score
    # A produced file is not proof of correct recognition: report, do not assert.
    print(json.dumps(correctness(expected_score(), score), indent=2))
