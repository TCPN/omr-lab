from pathlib import Path

import pytest

from omr_lab.engines import Engine, UnknownEngineOptionError
from omr_lab.engines.mock import MOCK_ENGINE_VERSION, MockEngine


def test_mock_engine_implements_engine_protocol() -> None:
    assert isinstance(MockEngine(), Engine)


def test_mock_engine_reports_availability() -> None:
    assert MockEngine().check_available().version == MOCK_ENGINE_VERSION
    unavailable = MockEngine(available=False).check_available()
    assert unavailable.available is False
    assert unavailable.message


def test_mock_musicxml_is_a_placeholder_without_notes(tmp_path: Path) -> None:
    input_path = tmp_path / "score.png"
    input_path.write_bytes(b"x")

    result = MockEngine().recognize(input_path, tmp_path / "raw", timeout_seconds=1)

    text = result.artifacts["musicxml"].read_text(encoding="utf-8")
    assert "<score-partwise" in text
    assert "<note" not in text
    assert result.success is True
    assert result.options == {"outcome": "succeeded"}


def test_mock_engine_rejects_unknown_option(tmp_path: Path) -> None:
    with pytest.raises(UnknownEngineOptionError):
        MockEngine().recognize(tmp_path / "x.png", tmp_path, timeout_seconds=1, options={"x": 1})
    assert list(tmp_path.iterdir()) == []
