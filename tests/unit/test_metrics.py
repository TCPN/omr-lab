"""Musical-correctness metrics on hand-built ground-truth / predicted score pairs."""

import copy

import pytest

from omr_lab import metrics
from omr_lab.metrics import ComparisonTooLargeError, compare_scores, summarize


def _note(
    step: str,
    octave: int = 4,
    duration: str | None = "1/4",
    *,
    alter: float = 0,
    lyrics: tuple[tuple[str | None, str | None], ...] = (),
    chord: tuple[tuple[str, int], ...] = (),
    grace: bool = False,
) -> dict:
    pitches = [{"step": step, "alter": alter, "octave": octave, "ties": []}]
    pitches += [{"step": s, "alter": 0, "octave": o, "ties": []} for s, o in chord]
    return {
        "kind": "note",
        "offset": "0",
        "voice": "1",
        "staff": None,
        "duration": None if grace else duration,
        "type": "quarter",
        "dots": 0,
        "time_modification": None,
        "tuplets": [],
        "grace": {"slash": False} if grace else None,
        "pitches": pitches,
        "lyrics": [
            {
                "number": number,
                "name": None,
                "syllabic": "single",
                "text": text,
                "extend": None,
                "elided": False,
            }
            for number, text in lyrics
        ],
    }


def _rest(duration: str = "1/4", *, hidden: bool = False) -> dict:
    return {
        "kind": "rest",
        "offset": "0",
        "voice": "1",
        "staff": None,
        "duration": duration,
        "type": "quarter",
        "dots": 0,
        "time_modification": None,
        "tuplets": [],
        "measure_rest": False,
        "hidden": hidden,
    }


def _harmony(step: str, kind: str = "major", *, bass: str | None = None, text=None) -> dict:
    return {
        "kind": "harmony",
        "offset": "0",
        "staff": None,
        "root": {"step": step, "alter": 0},
        "chord_kind": kind,
        "chord_kind_text": text,
        "bass": {"step": bass, "alter": 0} if bass else None,
        "degrees": [],
    }


def _key(fifths: int) -> dict:
    return {"kind": "key", "offset": "0", "staff": None, "fifths": fifths, "mode": None}


def _time(beats: str, beat_type: str) -> dict:
    return {
        "kind": "time",
        "offset": "0",
        "staff": None,
        "beats": beats,
        "beat_type": beat_type,
        "symbol": None,
    }


def _score(*measures: list[dict], parts: int = 1) -> dict:
    part = {
        "id": "P1",
        "name": None,
        "abbreviation": None,
        "measures": [
            {"number": str(i + 1), "implicit": False, "barlines": [], "events": events}
            for i, events in enumerate(measures)
        ],
    }
    return {
        "source": {"format": "musicxml", "artifact": None},
        "work_title": None,
        "work_number": None,
        "movement_title": None,
        "movement_number": None,
        "creators": [],
        "parts": [part] + [copy.deepcopy(part) for _ in range(parts - 1)],
    }


def _truth() -> dict:
    """Two measures of 4/4 in G: C D E F | G (half) rest, with chords and two verses."""
    return _score(
        [
            _key(1),
            _time("4", "4"),
            _harmony("C"),
            _note("C", lyrics=(("1", "小"), ("2", "雨"))),
            _note("D", lyrics=(("1", "雨"), ("2", "滴"))),
            _harmony("G", "dominant", bass="B"),
            _note("E", lyrics=(("1", "滴"), ("2", "答"))),
            _note("F", alter=1, lyrics=(("1", "答"), ("2", "響"))),
        ],
        [
            _harmony("C"),
            _note("G", duration="1/2", lyrics=(("1", "啦"), ("2", "啦"))),
            _rest("1/2"),
        ],
    )


def _measures(score: dict) -> list[dict]:
    return score["parts"][0]["measures"]


def test_identical_scores_are_fully_correct() -> None:
    result = compare_scores(_truth(), _truth())

    assert result["prediction"] == "score"
    assert result["key_signatures"] == {"ground_truth": [1], "predicted": [1], "correct": True}
    assert result["time_signatures"]["predicted"] == ["4/4"]
    assert result["measures"] == {"ground_truth": 2, "predicted": 2, "exact": 2, "accuracy": 1.0}
    notes = result["notes"]
    assert (notes["ground_truth"], notes["predicted"]) == (5, 5)
    for key in ("pitch", "duration", "pitch_and_duration"):
        assert notes[key]["matched"] == 5
        assert notes[key]["f1"] == 1.0
    assert notes["rests"]["matched"] == 1
    assert result["chord_symbols"]["f1"] == 1.0
    assert [v["character_error_rate"] for v in result["lyrics"]["verses"]] == [0.0, 0.0]
    assert result["lyrics"]["extra_predicted_verses"] == []


def test_a_wrong_pitch_counts_against_pitch_only() -> None:
    predicted = _truth()
    _measures(predicted)[0]["events"][4]["pitches"][0]["step"] = "B"  # D -> B

    notes = compare_scores(_truth(), predicted)["notes"]

    assert notes["pitch"]["matched"] == 4
    assert notes["duration"]["matched"] == 5
    assert notes["pitch_and_duration"]["matched"] == 4
    assert notes["pitch"]["precision"] == notes["pitch"]["recall"] == 0.8


def test_spelling_and_accidentals_count() -> None:
    predicted = _truth()
    # F#4 read as F4 (a missed key signature or accidental) and as Gb4 (respelled).
    _measures(predicted)[0]["events"][7]["pitches"][0]["alter"] = 0
    assert compare_scores(_truth(), predicted)["notes"]["pitch"]["matched"] == 4
    respelled = _truth()
    _measures(respelled)[0]["events"][7]["pitches"][0].update(step="G", alter=-1)
    assert compare_scores(_truth(), respelled)["notes"]["pitch"]["matched"] == 4


def test_a_missed_note_does_not_shift_the_rest() -> None:
    predicted = _truth()
    del _measures(predicted)[0]["events"][4]  # D missing

    result = compare_scores(_truth(), predicted)
    both = result["notes"]["pitch_and_duration"]

    assert (both["ground_truth"], both["predicted"], both["matched"]) == (5, 4, 4)
    assert both["precision"] == 1.0
    assert both["recall"] == 0.8
    assert both["f1"] == round(2 * 4 / 9, 4)
    assert result["measures"]["exact"] == 1


def test_an_extra_note_lowers_precision() -> None:
    predicted = _truth()
    _measures(predicted)[1]["events"].insert(1, _note("A", duration="1/8"))

    both = compare_scores(_truth(), predicted)["notes"]["pitch_and_duration"]

    assert (both["matched"], both["precision"], both["recall"]) == (5, round(5 / 6, 4), 1.0)


def test_a_dropped_measure() -> None:
    predicted = _truth()
    del _measures(predicted)[1]

    result = compare_scores(_truth(), predicted)

    assert result["measures"] == {"ground_truth": 2, "predicted": 1, "exact": 1, "accuracy": 0.5}
    assert result["notes"]["pitch_and_duration"]["recall"] == 0.8
    assert result["notes"]["rests"]["recall"] == 0.0


def test_wrong_durations() -> None:
    predicted = _truth()
    _measures(predicted)[1]["events"][1]["duration"] = "3/4"  # half read as dotted half
    _measures(predicted)[1]["events"][2]["duration"] = "1/4"

    result = compare_scores(_truth(), predicted)

    assert result["notes"]["pitch"]["matched"] == 5
    assert result["notes"]["duration"]["matched"] == 4
    assert result["notes"]["rests"]["matched"] == 0
    assert result["measures"]["exact"] == 1


def test_a_chord_must_have_every_pitch() -> None:
    truth = _score([_note("C", chord=(("E", 4), ("G", 4)))])
    predicted = _score([_note("C", chord=(("E", 4),))])
    reordered = _score([_note("G", chord=(("C", 4), ("E", 4)))])

    assert compare_scores(truth, predicted)["notes"]["pitch"]["matched"] == 0
    assert compare_scores(truth, reordered)["notes"]["pitch"]["matched"] == 1


def test_grace_notes_and_hidden_rests_are_not_compared() -> None:
    truth = _score([_note("C"), _rest("1/4")])
    predicted = _score([_note("B", 3, grace=True), _note("C"), _rest("1/4"), _rest(hidden=True)])

    result = compare_scores(truth, predicted)

    assert result["notes"]["predicted"] == 1
    assert result["notes"]["rests"]["predicted"] == 1
    assert result["measures"]["exact"] == 1


def test_no_key_signature_means_zero_fifths() -> None:
    explicit = _score([_key(0), _note("C")])
    implicit = _score([_note("C")])
    later = _score([_note("C")], [_key(2), _note("D")])

    assert compare_scores(explicit, implicit)["key_signatures"] == {
        "ground_truth": [0],
        "predicted": [0],
        "correct": True,
    }
    assert compare_scores(later, _score([_key(0), _note("C")], [_key(2), _note("D")]))[
        "key_signatures"
    ]["predicted"] == [0, 2]
    assert (
        compare_scores(_score([_key(1), _note("C")]), implicit)["key_signatures"]["correct"]
        is False
    )


def test_signatures_are_compared_as_changes() -> None:
    truth = _score([_key(1), _time("3", "4"), _note("C")], [_key(1), _note("D")])
    predicted = _score([_key(0), _time("3", "4"), _note("C")], [_note("D")])

    result = compare_scores(truth, predicted)

    assert result["key_signatures"] == {"ground_truth": [1], "predicted": [0], "correct": False}
    assert result["time_signatures"]["correct"] is True


def test_chord_symbols_compare_meaning_not_display() -> None:
    truth = _score([_harmony("C", text=None), _harmony("G", bass="B"), _note("C")])
    shown_differently = _score([_harmony("C", text=""), _harmony("G", bass="B"), _note("C")])
    wrong_bass = _score([_harmony("C"), _harmony("G"), _note("C")])

    assert compare_scores(truth, shown_differently)["chord_symbols"]["matched"] == 2
    chords = compare_scores(truth, wrong_bass)["chord_symbols"]
    assert (chords["matched"], chords["precision"], chords["recall"]) == (1, 0.5, 0.5)


def test_lyrics_per_verse() -> None:
    predicted = _truth()
    events = _measures(predicted)[0]["events"]
    events[3]["lyrics"][0]["text"] = "水"  # verse 1: one wrong character
    for event in events + _measures(predicted)[1]["events"]:
        if event["kind"] == "note":
            # Verse 2 recognized under number "3" instead.
            for lyric in event["lyrics"]:
                if lyric["number"] == "2":
                    lyric["number"] = "3"

    lyrics = compare_scores(_truth(), predicted)["lyrics"]
    verse1, verse2 = lyrics["verses"]

    assert verse1["number"] == "1"
    assert (verse1["syllable_errors"], verse1["character_errors"]) == (1, 1)
    assert verse1["character_error_rate"] == 0.2
    assert verse2["number"] == "2"
    assert verse2["predicted_syllables"] == 0
    assert verse2["syllable_error_rate"] == 1.0
    assert lyrics["extra_predicted_verses"] == ["3"]


def test_lyric_errors_count_characters_inside_syllables() -> None:
    truth = _score([_note("C", lyrics=(("1", "Twin"),)), _note("C", lyrics=(("1", "kle"),))])
    predicted = _score([_note("C", lyrics=(("1", "Twim"),)), _note("C", lyrics=(("1", "kle"),))])

    verse = compare_scores(truth, predicted)["lyrics"]["verses"][0]

    assert (verse["syllable_errors"], verse["syllable_error_rate"]) == (1, 0.5)
    assert (verse["character_errors"], verse["character_error_rate"]) == (1, round(1 / 7, 4))


def test_lyric_error_rate_can_exceed_one() -> None:
    truth = _score([_note("C", lyrics=(("1", "a"),))])
    predicted = _score([_note("C", lyrics=(("1", "xyz"),))])

    verse = compare_scores(truth, predicted)["lyrics"]["verses"][0]

    assert verse["character_error_rate"] == 3.0


def test_a_missing_prediction_misses_everything() -> None:
    result = compare_scores(_truth(), None)

    assert result["prediction"] == "missing"
    assert result["parts"] == {"ground_truth": 1, "predicted": 0}
    both = result["notes"]["pitch_and_duration"]
    assert (both["precision"], both["recall"], both["f1"]) == (None, 0.0, 0.0)
    assert result["measures"]["accuracy"] == 0.0
    assert result["key_signatures"]["correct"] is False
    assert [v["character_error_rate"] for v in result["lyrics"]["verses"]] == [1.0, 1.0]


def test_only_the_first_part_is_compared() -> None:
    truth = _score([_note("C")], parts=2)
    predicted = _score([_note("C")])
    truth["parts"][1]["measures"][0]["events"] = [_note("A")]

    result = compare_scores(truth, predicted)

    assert result["parts"] == {"ground_truth": 2, "predicted": 1}
    assert result["notes"]["pitch"]["matched"] == 1


def test_empty_ground_truth_gives_null_rates() -> None:
    result = compare_scores(_score([]), _score([]))

    assert result["notes"]["pitch"] == {
        "ground_truth": 0,
        "predicted": 0,
        "matched": 0,
        "precision": None,
        "recall": None,
        "f1": None,
    }
    # An empty measure matches an empty measure.
    assert result["measures"]["accuracy"] == 1.0


def test_too_large_comparisons_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics, "MAX_ALIGNMENT_CELLS", 10)

    with pytest.raises(ComparisonTooLargeError, match="exceeds 10 cells"):
        compare_scores(_truth(), _truth())


def test_summary_sums_counts_before_computing_rates() -> None:
    perfect = compare_scores(_truth(), _truth())
    short = _score([_note("C"), _note("D")])
    half = compare_scores(short, _score([_note("C"), _note("E")]))

    totals = summarize([perfect, half, compare_scores(short, None)])

    assert totals["samples"] == 3
    assert totals["predictions_missing"] == 1
    both = totals["notes"]["pitch_and_duration"]
    assert (both["ground_truth"], both["predicted"], both["matched"]) == (9, 7, 6)
    assert both["recall"] == round(6 / 9, 4)
    # The missing prediction has no key, not even the implicit 0 fifths of a score.
    assert totals["key_signatures_correct"] == 2
    assert totals["lyrics"]["verses"] == 2
    assert summarize([]) is None


def test_worked_example_in_docs() -> None:
    """The example in docs/metrics.md, number by number."""
    truth = _score(
        [
            _key(1),
            _time("4", "4"),
            _harmony("C"),
            _note("C", lyrics=(("1", "小"),)),
            _note("D", lyrics=(("1", "雨"),)),
            _note("E", lyrics=(("1", "滴"),)),
            _note("F", alter=1, lyrics=(("1", "答"),)),
        ]
    )
    predicted = _score(
        [
            _key(1),
            _time("4", "4"),
            _note("C", lyrics=(("1", "小"),)),
            _note("E", lyrics=(("1", "水"),)),
            _note("F", lyrics=(("1", "答"),)),
        ]
    )

    result = compare_scores(truth, predicted)
    notes = result["notes"]

    assert (notes["pitch"]["matched"], notes["pitch"]["precision"]) == (2, 0.6667)
    assert (notes["pitch"]["recall"], notes["pitch"]["f1"]) == (0.5, 0.5714)
    assert (notes["duration"]["matched"], notes["duration"]["f1"]) == (3, 0.8571)
    assert notes["pitch_and_duration"]["matched"] == 2
    assert result["measures"]["accuracy"] == 0.0
    assert result["key_signatures"]["correct"] and result["time_signatures"]["correct"]
    chords = result["chord_symbols"]
    assert (chords["recall"], chords["precision"], chords["f1"]) == (0.0, None, 0.0)
    verse = result["lyrics"]["verses"][0]
    assert (verse["syllable_errors"], verse["syllable_error_rate"]) == (2, 0.5)
    assert (verse["character_errors"], verse["character_error_rate"]) == (2, 0.5)
