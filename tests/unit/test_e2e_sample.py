"""The committed end-to-end sample and the input-to-run.json flow, without a real engine.

The real-engine run of the same sample is tests/integration/test_e2e_audiveris.py.
"""

import copy
import hashlib
import json
import shutil
import struct
from pathlib import Path

import pytest
from e2e_report import GROUND_TRUTH, IMAGE, SAMPLE, correctness, events, expected_score

from omr_lab import cli
from omr_lab.cli import EXIT_OK, main
from omr_lab.engines import EngineAvailability, EngineOptions, EngineResult
from omr_lab.musicxml import load_musicxml
from omr_lab.schemas import validate_run_document
from omr_lab.score import score_from_musicxml


class GroundTruthEngine:
    """Stand-in engine that "recognizes" the sample perfectly by copying its ground truth.

    It exercises everything around the engine (CLI, pipeline, MusicXML loading, score
    conversion, run document) with a known-correct engine output.
    """

    name = "ground-truth"

    def check_available(self) -> EngineAvailability:
        return EngineAvailability(engine_name=self.name, available=True, version="test")

    def recognize(
        self,
        input_path: Path,
        output_dir: Path,
        *,
        timeout_seconds: float,
        options: EngineOptions | None = None,
    ) -> EngineResult:
        assert input_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact = output_dir / "twinkle.musicxml"
        shutil.copyfile(GROUND_TRUTH, artifact)
        return EngineResult(self.name, success=True, artifacts={"musicxml": artifact})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_checksums_match_the_committed_files() -> None:
    assert _sha256(IMAGE) == SAMPLE["image_sha256"]
    assert _sha256(GROUND_TRUTH) == SAMPLE["ground_truth_sha256"]
    assert SAMPLE["license"] == "CC0-1.0"


def test_image_is_a_single_grayscale_png() -> None:
    header = IMAGE.read_bytes()[:26]

    assert header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", header[16:26])
    assert (width, height, bit_depth, color_type) == (2480, 1235, 8, 0)


def test_ground_truth_converts_without_warnings() -> None:
    document = load_musicxml(GROUND_TRUTH)
    conversion = score_from_musicxml(document, source_artifact="musicxml")

    assert document.warnings == () and conversion.warnings == ()
    (part,) = conversion.score["parts"]
    events = [event for measure in part["measures"] for event in measure["events"]]
    notes = [event for event in events if event["kind"] == "note"]
    assert len(part["measures"]) == 12
    assert len(notes) == 42
    assert sum(event["kind"] == "harmony" for event in events) == 22
    assert all(len(note["lyrics"]) == 1 for note in notes)
    assert conversion.score["work_title"] == "Twinkle, Twinkle, Little Star"


def test_sample_runs_through_the_cli_into_a_valid_run_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(cli.ENGINES, GroundTruthEngine.name, GroundTruthEngine)
    output = tmp_path / "run"

    code = main(["run", str(IMAGE), "-o", str(output), "--engine", GroundTruthEngine.name])

    assert code == EXIT_OK, capsys.readouterr().err
    document = json.loads((output / "run.json").read_text(encoding="utf-8"))
    validate_run_document(document)
    assert document["status"] == "succeeded"
    assert document["warnings"] == []
    assert document["input"]["filename"] == "twinkle.png"
    assert document["score"] == expected_score()
    assert _sha256(IMAGE) == SAMPLE["image_sha256"]


def test_report_helpers_score_the_ground_truth_as_perfect() -> None:
    expected = expected_score()

    report = correctness(expected, expected)

    assert report["measures"] == {"expected": 12, "recognized": 12}
    assert report["notes_pitch_and_duration"] == {
        "expected": 42,
        "recognized": 42,
        "matched": 42,
        "recall": 1.0,
        "precision": 1.0,
    }
    assert report["chord_symbols"]["matched"] == 22
    assert report["first_key_matches"] and report["first_time_matches"]


def test_report_counts_a_wrong_pitch_and_a_missing_note() -> None:
    expected = expected_score()
    recognized = copy.deepcopy(expected)
    first_measure = recognized["parts"][0]["measures"][0]["events"]
    notes = [event for event in first_measure if event["kind"] == "note"]
    notes[0]["pitches"][0]["step"] = "D"
    first_measure.remove(notes[1])

    report = correctness(expected, recognized)

    assert report["notes_pitch_and_duration"]["matched"] == 40
    assert report["notes_pitch_and_duration"]["recognized"] == 41
    assert report["notes_duration_only"]["matched"] == 41
    assert report["lyric_syllables"]["matched"] == 41


def test_report_treats_a_missing_key_signature_as_c_major() -> None:
    # Audiveris writes no <key> for C major; the ground truth has fifths 0.
    expected = expected_score()
    recognized = copy.deepcopy(expected)
    first_measure = recognized["parts"][0]["measures"][0]["events"]
    first_measure[:] = [event for event in first_measure if event["kind"] != "key"]

    assert correctness(expected, recognized)["first_key_matches"] is True
    first_measure.insert(0, {**events(expected, "key")[0], "fifths": 1})
    assert correctness(expected, recognized)["first_key_matches"] is False
