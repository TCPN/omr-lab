"""Ground truth and the correctness report for the end-to-end sample (issue #10).

Shared by tests/unit/test_e2e_sample.py and tests/integration/test_e2e_audiveris.py,
so the comparison code also runs in the default suite. Importable because pyproject.toml
puts tests/ on pytest's ``pythonpath``.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from omr_lab.musicxml import load_musicxml
from omr_lab.score import score_from_musicxml

E2E_DIR = Path(__file__).resolve().parent / "fixtures" / "e2e"
(SAMPLE,) = json.loads((E2E_DIR / "manifest.json").read_text(encoding="utf-8"))["samples"]
IMAGE = E2E_DIR / SAMPLE["image"]
GROUND_TRUTH = E2E_DIR / SAMPLE["ground_truth"]


def expected_score() -> dict:
    """The ground truth converted the way the pipeline converts an engine's MusicXML."""
    return score_from_musicxml(load_musicxml(GROUND_TRUTH), source_artifact="musicxml").score


def events(score: dict, kind: str) -> list[dict]:
    return [
        event
        for part in score["parts"]
        for measure in part["measures"]
        for event in measure["events"]
        if event["kind"] == kind
    ]


def note_tokens(score: dict, *, pitch: bool = True, duration: bool = True) -> list[tuple]:
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


def compare(expected: list, recognized: list) -> dict:
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


def correctness(expected: dict, recognized: dict) -> dict:
    def first(score: dict, kind: str, keys: tuple[str, ...]) -> list:
        return [[event[key] for key in keys] for event in events(score, kind)[:1]]

    def lyrics(score: dict) -> list[str]:
        return [lyric["text"] for note in events(score, "note") for lyric in note["lyrics"]]

    def key_fifths(score: dict) -> int:
        # No <key> means no key signature, which reads as C major (Audiveris omits it).
        found = first(score, "key", ("fifths",))
        return found[0][0] if found else 0

    def harmony_roots(score: dict) -> list[tuple]:
        return [
            (event["root"]["step"], event["root"]["alter"], event["chord_kind"])
            for event in events(score, "harmony")
            if event["root"] is not None
        ]

    return {
        "measures": {
            "expected": sum(len(part["measures"]) for part in expected["parts"]),
            "recognized": sum(len(part["measures"]) for part in recognized["parts"]),
        },
        "parts": {"expected": len(expected["parts"]), "recognized": len(recognized["parts"])},
        "first_key_matches": key_fifths(expected) == key_fifths(recognized),
        "first_time_matches": first(expected, "time", ("beats", "beat_type"))
        == first(recognized, "time", ("beats", "beat_type")),
        "first_clef_matches": first(expected, "clef", ("sign", "line"))
        == first(recognized, "clef", ("sign", "line")),
        "notes_pitch_and_duration": compare(note_tokens(expected), note_tokens(recognized)),
        "notes_pitch_only": compare(
            note_tokens(expected, duration=False), note_tokens(recognized, duration=False)
        ),
        "notes_duration_only": compare(
            note_tokens(expected, pitch=False), note_tokens(recognized, pitch=False)
        ),
        "chord_symbols": compare(harmony_roots(expected), harmony_roots(recognized)),
        "lyric_syllables": compare(lyrics(expected), lyrics(recognized)),
    }
