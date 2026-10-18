import json
from fractions import Fraction
from pathlib import Path

import pytest

from omr_lab.musicxml import load_musicxml, parse_musicxml_bytes
from omr_lab.schemas import RunWarningCode, validate_run_document
from omr_lab.score import rational, score_from_musicxml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "musicxml"
RUN_DOCUMENTS = REPO_ROOT / "tests" / "fixtures" / "run-documents"


def _run_document(score: dict) -> dict:
    document = json.loads(
        (RUN_DOCUMENTS / "valid" / "succeeded-minimal.json").read_text(encoding="utf-8")
    )
    document["score"] = score
    return document


def _convert(body: str, *, part_list: str | None = None):
    part_list = part_list or '<score-part id="P1"><part-name>Voice</part-name></score-part>'
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0">'
        f"<part-list>{part_list}</part-list>{body}</score-partwise>"
    )
    conversion = score_from_musicxml(parse_musicxml_bytes(xml.encode("utf-8")))
    validate_run_document(_run_document(conversion.score))
    return conversion


def _measure(content: str, *, divisions: str | None = "1") -> str:
    attributes = (
        "" if divisions is None else f"<attributes><divisions>{divisions}</divisions></attributes>"
    )
    return f'<part id="P1"><measure number="1">{attributes}{content}</measure></part>'


def _events(conversion, measure: int = 0) -> list[dict]:
    return conversion.score["parts"][0]["measures"][measure]["events"]


def _codes(conversion) -> list[str]:
    return [warning.code for warning in conversion.warnings]


NOTE_C4 = "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note>"


def test_feature_fixture_round_trips_to_the_expected_score() -> None:
    document = load_musicxml(FIXTURES / "score-features.musicxml")
    conversion = score_from_musicxml(document, source_artifact="musicxml")

    expected = json.loads((FIXTURES / "score-features.score.json").read_text(encoding="utf-8"))
    assert conversion.score == expected
    validate_run_document(_run_document(conversion.score))


def test_feature_fixture_reports_unsupported_constructs_only() -> None:
    conversion = score_from_musicxml(load_musicxml(FIXTURES / "score-features.musicxml"))

    assert [w.to_dict() for w in conversion.warnings] == [
        {
            "code": "score_unsupported_element",
            "message": "<slur> in <notations> is not represented in the score "
            "(2 occurrences; first in part 'P1', measure '1').",
        },
        {
            "code": "score_unsupported_element",
            "message": "<dynamics> in <direction-type> is not represented in the score "
            "(1 occurrence; first in part 'P1', measure '3').",
        },
    ]


def test_two_verses_on_the_same_notes_are_kept_verse_by_verse() -> None:
    conversion = score_from_musicxml(load_musicxml(FIXTURES / "score-features.musicxml"))

    verses: dict[str, list[tuple[str | None, str | None]]] = {}
    for measure in conversion.score["parts"][0]["measures"]:
        for event in measure["events"]:
            for lyric in event.get("lyrics", []):
                verses.setdefault(lyric["number"], []).append((lyric["syllabic"], lyric["text"]))

    assert sorted(verses) == ["1", "2"]
    assert "".join(text or "" for _, text in verses["1"]) == "我讚美主恩永遠"
    assert "".join(text or "" for _, text in verses["2"]) == "你感謝神愛直到"
    assert [syllabic for syllabic, _ in verses["2"][1:3]] == ["begin", "end"]
    # The tied note continues verse 1's melisma with an extender-only lyric.
    assert verses["1"][4] == (None, None)


def test_existing_fixture_converts_and_validates() -> None:
    conversion = score_from_musicxml(load_musicxml(FIXTURES / "valid-partwise.musicxml"))

    score = conversion.score
    assert score["work_title"] == "小星星 Synthetic Test Tune"
    assert score["creators"][1] == {"type": "lyricist", "name": "OMR Lab Fixture"}
    assert [part["name"] for part in score["parts"]] == ["Voice", None]
    assert conversion.warnings == ()
    validate_run_document(_run_document(score))


def test_offsets_and_durations_are_exact_whole_note_rationals() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>3</duration>"
            "<type>quarter</type><dot/></note>"
            "<note><pitch><step>D</step><octave>4</octave></pitch><duration>1</duration></note>"
            "<backup><duration>4</duration></backup>"
            "<forward><duration>2</duration></forward>"
            "<note><rest/><duration>2</duration><voice>2</voice></note>",
            divisions="2",
        )
    )

    events = _events(conversion)
    assert [(e["kind"], e["offset"], e["duration"]) for e in events] == [
        ("note", "0", "3/8"),
        ("note", "3/8", "1/8"),
        ("rest", "1/4", "1/4"),
    ]
    assert events[0]["dots"] == 1


def test_divisions_carry_over_and_offsets_restart_each_measure() -> None:
    conversion = _convert(
        '<part id="P1"><measure number="1"><attributes><divisions>4</divisions></attributes>'
        f'{NOTE_C4}{NOTE_C4}</measure><measure number="2">{NOTE_C4}</measure></part>'
    )

    assert [e["offset"] for e in _events(conversion, 0)] == ["0", "1/16"]
    assert [(e["offset"], e["duration"]) for e in _events(conversion, 1)] == [("0", "1/16")]


def test_chord_notes_merge_into_one_event_with_their_lyrics() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration>"
            '<lyric number="1"><text>a</text></lyric></note>'
            "<note><chord/><pitch><step>E</step><alter>-1</alter><octave>4</octave></pitch>"
            '<duration>2</duration><lyric number="2"><text>b</text></lyric></note>'
            f"{NOTE_C4}"
        )
    )

    events = _events(conversion)
    assert len(events) == 2
    assert [(p["step"], p["alter"]) for p in events[0]["pitches"]] == [("C", 0), ("E", -1)]
    assert [lyric["number"] for lyric in events[0]["lyrics"]] == ["1", "2"]
    assert events[1]["offset"] == "1/2"


def test_notations_on_merged_chord_notes_are_reported() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration></note>"
            "<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>2</duration>"
            "<notations><fermata/></notations></note>"
        )
    )

    assert len(_events(conversion)) == 1
    assert _codes(conversion) == [RunWarningCode.SCORE_UNSUPPORTED_ELEMENT]
    assert "<fermata> in <notations>" in conversion.warnings[0].message


def test_grace_notes_take_no_time() -> None:
    conversion = _convert(
        _measure(
            "<note><grace/><pitch><step>D</step><octave>5</octave></pitch><type>16th</type></note>"
            f"{NOTE_C4}"
        )
    )

    grace, note = _events(conversion)
    assert grace["grace"] == {"slash": False}
    assert grace["duration"] is None
    assert (note["offset"], note["grace"]) == ("0", None)


def test_elided_syllables_join_into_one_lyric() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            '<lyric number="1" name="verse"><syllabic>end</syllabic><text>a</text>'
            "<elision>‿</elision><syllabic>begin</syllabic><text>b</text></lyric></note>"
        )
    )

    assert _events(conversion)[0]["lyrics"] == [
        {"number": "1", "name": "verse", "syllabic": "middle", "text": "a‿b", "extend": None}
    ]


def test_missing_divisions_gives_null_timing_with_a_warning() -> None:
    conversion = _convert(_measure(f"{NOTE_C4}{NOTE_C4}", divisions=None))

    assert [(e["offset"], e["duration"]) for e in _events(conversion)] == [
        ("0", None),
        (None, None),
    ]
    assert _codes(conversion) == [RunWarningCode.SCORE_DIVISIONS_MISSING]
    assert "2 occurrences" in conversion.warnings[0].message


def test_backup_before_the_measure_start_is_reported() -> None:
    conversion = _convert(_measure(f"{NOTE_C4}<backup><duration>3</duration></backup>{NOTE_C4}"))

    assert [e["offset"] for e in _events(conversion)] == ["0", None]
    assert _codes(conversion) == [RunWarningCode.SCORE_TIMING_INCONSISTENT]


def test_invalid_pitch_drops_the_note_with_a_warning_but_keeps_its_time() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>H</step><octave>4</octave></pitch><duration>1</duration></note>"
            f"{NOTE_C4}"
        )
    )

    assert [e["offset"] for e in _events(conversion)] == ["1/4"]
    assert _codes(conversion) == [RunWarningCode.SCORE_INVALID_VALUE]


@pytest.mark.parametrize(
    ("content", "construct"),
    [
        (
            "<note><unpitched><display-step>C</display-step><display-octave>5"
            "</display-octave></unpitched><duration>1</duration></note>",
            "An unpitched <note>",
        ),
        (
            "<note><cue/><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>1</duration></note>",
            "A cue <note>",
        ),
        ("<harmony><function>V</function><kind>major</kind></harmony>", "A <harmony> without"),
        (
            "<attributes><key><key-step>C</key-step><key-alter>0</key-alter></key></attributes>",
            "A non-traditional <key>",
        ),
        ("<attributes><time><senza-misura/></time></attributes>", "<senza-misura>"),
        (
            "<attributes><transpose><chromatic>-2</chromatic></transpose></attributes>",
            "<transpose>",
        ),
        (
            "<figured-bass><figure><figure-number>6</figure-number></figure></figured-bass>",
            "<figured-bass>",
        ),
        (
            "<direction><direction-type><words>rit.</words></direction-type></direction>",
            "<words> in <direction-type> without a tempo",
        ),
        ("<barline><fermata/></barline>", "<fermata> in <barline>"),
        (
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            "<notations><fermata/></notations></note>",
            "<fermata> in <notations>",
        ),
        (
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            "<lyric><text>a</text><end-line/></lyric></note>",
            "<end-line> in <lyric>",
        ),
        (
            "<note><rest/><duration>1</duration><lyric><text>a</text></lyric></note>",
            "<lyric> on a rest",
        ),
    ],
)
def test_unsupported_constructs_are_reported_not_dropped(content: str, construct: str) -> None:
    conversion = _convert(_measure(content))

    assert _codes(conversion) == [RunWarningCode.SCORE_UNSUPPORTED_ELEMENT]
    assert construct in conversion.warnings[0].message


def test_presentation_elements_are_ignored_without_warnings() -> None:
    conversion = _convert(
        _measure(
            '<print new-page="yes"/>'
            "<note><pitch><step>F</step><alter>1</alter><octave>4</octave></pitch>"
            '<duration>1</duration><tie type="start"/><type>quarter</type><stem>up</stem>'
            '<notehead>normal</notehead><accidental>sharp</accidental><beam number="1">begin</beam>'
            '<notations><tied type="start"/></notations></note>'
        )
    )

    assert conversion.warnings == ()
    assert _events(conversion)[0]["pitches"] == [
        {"step": "F", "alter": 1, "octave": 4, "ties": ["start"]}
    ]


def test_composite_time_keeps_the_first_signature_and_warns() -> None:
    conversion = _convert(
        _measure(
            "<attributes><time><beats>3</beats><beat-type>8</beat-type>"
            "<beats>2</beats><beat-type>4</beat-type></time></attributes>"
        )
    )

    assert [(e["beats"], e["beat_type"]) for e in _events(conversion)] == [("3", "8")]
    assert _codes(conversion) == [RunWarningCode.SCORE_UNSUPPORTED_ELEMENT]


def test_measure_level_sound_tempo_becomes_a_tempo_event() -> None:
    conversion = _convert(_measure(f'{NOTE_C4}<sound tempo="72.5"/>'))

    tempo = _events(conversion)[1]
    assert tempo == {
        "kind": "tempo",
        "offset": "1/4",
        "staff": None,
        "bpm": 72.5,
        "beat_unit": None,
        "beat_unit_dots": 0,
        "per_minute": None,
        "words": None,
    }


def test_staff_numbers_are_read_from_elements_and_attributes() -> None:
    conversion = _convert(
        _measure(
            '<attributes><staves>2</staves><clef number="2"><sign>F</sign><line>4</line></clef>'
            "</attributes>"
            "<note><pitch><step>C</step><octave>3</octave></pitch><duration>1</duration>"
            "<staff>2</staff></note>"
        )
    )

    clef, note = _events(conversion)
    assert (clef["staff"], note["staff"]) == (2, 2)
    assert conversion.warnings == ()


def test_parts_follow_the_loader_part_order() -> None:
    conversion = _convert(
        f'<part id="P2"><measure number="1">{NOTE_C4}</measure></part>',
        part_list=(
            '<score-part id="P1"><part-name>Missing</part-name></score-part>'
            '<score-part id="P2"><part-name>Second</part-name></score-part>'
        ),
    )

    assert [part["id"] for part in conversion.score["parts"]] == ["P2"]


def test_warnings_are_aggregated_per_construct() -> None:
    slurred = (
        "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
        '<notations><slur type="start"/></notations></note>'
    )
    conversion = _convert(_measure(slurred * 50))

    assert len(conversion.warnings) == 1
    assert "50 occurrences" in conversion.warnings[0].message


@pytest.mark.parametrize(
    ("value", "text"),
    [(Fraction(0), "0"), (Fraction(2), "2"), (Fraction(6, 8), "3/4"), (Fraction(1, 12), "1/12")],
)
def test_rational_writes_lowest_terms(value: Fraction, text: str) -> None:
    assert rational(value) == text


def test_rational_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        rational(Fraction(-1, 4))
