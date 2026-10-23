"""End-to-end run of the committed sample score with a real Audiveris (issue #10).

Deselected by default; run with ``uv run pytest -m integration -s``. It skips when
Audiveris cannot be found (see docs/engines/audiveris.md for setup).

The test asserts only that the run produced output: exit code 0, a valid run
document with ``status: succeeded``, a loadable MusicXML artifact and a ``score``.
How well the music was recognized is measured against the sample's ground truth and
reported, not asserted, because a valid output is not a correct one. The report is
written to ``correctness.json`` in the run directory and printed (use ``-s`` to see it).

Set ``OMR_LAB_E2E_OUTPUT`` to a directory to keep the evidence: each run then writes
a new ``twinkle-<UTC timestamp>`` run directory there instead of in pytest's temporary
directory.
"""

from __future__ import annotations

import difflib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omr_lab.cli import EXIT_OK, main
from omr_lab.engines import AudiverisEngine
from omr_lab.musicxml import load_musicxml
from omr_lab.schemas import validate_run_document
from omr_lab.score import score_from_musicxml

pytestmark = pytest.mark.integration

OUTPUT_ENV_VAR = "OMR_LAB_E2E_OUTPUT"
E2E_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "e2e"
(SAMPLE,) = json.loads((E2E_DIR / "manifest.json").read_text(encoding="utf-8"))["samples"]


@pytest.fixture(scope="module")
def audiveris_available() -> None:
    availability = AudiverisEngine().check_available()
    if not availability.available:
        pytest.skip(f"Audiveris not available: {availability.message}")


def _run_dir(tmp_path: Path) -> Path:
    root = os.environ.get(OUTPUT_ENV_VAR)
    if not root:
        return tmp_path / "run"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(root) / f"{SAMPLE['id']}-{stamp}"


def _events(score: dict, kind: str) -> list[dict]:
    return [
        event
        for part in score["parts"]
        for measure in part["measures"]
        for event in measure["events"]
        if event["kind"] == kind
    ]


def _note_tokens(score: dict, *, pitch: bool = True, duration: bool = True) -> list[tuple]:
    """One token per note or rest, in document order, for sequence comparison."""
    tokens = []
    for part in score["parts"]:
        for measure in part["measures"]:
            for event in measure["events"]:
                if event["kind"] not in ("note", "rest"):
                    continue
                token: tuple = (event["kind"],)
                if pitch and event["kind"] == "note":
                    token += tuple((p["step"], p["alter"], p["octave"]) for p in event["pitches"])
                if duration:
                    token += (event["duration"],)
                tokens.append(token)
    return tokens


def _compare(expected: list, recognized: list) -> dict:
    """Longest-matching-blocks alignment; recall over expected, precision over recognized."""
    matcher = difflib.SequenceMatcher(None, expected, recognized, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return {
        "expected": len(expected),
        "recognized": len(recognized),
        "matched": matched,
        "recall": round(matched / len(expected), 3) if expected else None,
        "precision": round(matched / len(recognized), 3) if recognized else None,
    }


def _correctness(expected: dict, recognized: dict) -> dict:
    def first(score: dict, kind: str, keys: tuple[str, ...]) -> list:
        return [[event[key] for key in keys] for event in _events(score, kind)[:1]]

    def lyrics(score: dict) -> list[str]:
        return [lyric["text"] for note in _events(score, "note") for lyric in note["lyrics"]]

    def harmony_roots(score: dict) -> list[tuple]:
        return [
            (event["root"]["step"], event["root"]["alter"], event["chord_kind"])
            for event in _events(score, "harmony")
            if event["root"] is not None
        ]

    return {
        "measures": {
            "expected": sum(len(part["measures"]) for part in expected["parts"]),
            "recognized": sum(len(part["measures"]) for part in recognized["parts"]),
        },
        "parts": {"expected": len(expected["parts"]), "recognized": len(recognized["parts"])},
        "first_key_matches": first(expected, "key", ("fifths",))
        == first(recognized, "key", ("fifths",)),
        "first_time_matches": first(expected, "time", ("beats", "beat_type"))
        == first(recognized, "time", ("beats", "beat_type")),
        "first_clef_matches": first(expected, "clef", ("sign", "line"))
        == first(recognized, "clef", ("sign", "line")),
        "notes_pitch_and_duration": _compare(_note_tokens(expected), _note_tokens(recognized)),
        "notes_pitch_only": _compare(
            _note_tokens(expected, duration=False), _note_tokens(recognized, duration=False)
        ),
        "notes_duration_only": _compare(
            _note_tokens(expected, pitch=False), _note_tokens(recognized, pitch=False)
        ),
        "chord_symbols": _compare(harmony_roots(expected), harmony_roots(recognized)),
        "lyric_syllables": _compare(lyrics(expected), lyrics(recognized)),
    }


def test_sample_end_to_end_with_audiveris(audiveris_available: None, tmp_path: Path) -> None:
    image = E2E_DIR / SAMPLE["image"]
    run_dir = _run_dir(tmp_path)

    code = main(["run", str(image), "-o", str(run_dir), "--engine", "audiveris"])

    # Output generation: asserted.
    document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    validate_run_document(document)
    assert code == EXIT_OK, document.get("error")
    assert document["status"] == "succeeded"
    assert document["engine"]["name"] == "audiveris"
    load_musicxml(run_dir / document["artifacts"]["musicxml"])
    assert document["score"] is not None

    # Musical correctness: reported, not asserted.
    ground_truth = E2E_DIR / SAMPLE["ground_truth"]
    expected = score_from_musicxml(load_musicxml(ground_truth), source_artifact="musicxml").score
    report = {
        "sample": SAMPLE["id"],
        "engine": document["engine"],
        "timing": document["timing"],
        "warnings": len(document["warnings"]),
        "correctness": _correctness(expected, document["score"]),
        "note": "Matched counts come from a longest-matching-blocks alignment of the "
        "note sequences; they are a rough indicator, not a benchmark metric.",
    }
    (run_dir / "correctness.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nEnd-to-end run directory: {run_dir}")
    print(json.dumps(report["correctness"], indent=2))


def test_report_helpers_score_the_ground_truth_as_perfect() -> None:
    # Runs with the integration tests; checks the comparison itself without Audiveris.
    ground_truth = E2E_DIR / SAMPLE["ground_truth"]
    expected = score_from_musicxml(load_musicxml(ground_truth), source_artifact="musicxml").score

    report = _correctness(expected, expected)

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
