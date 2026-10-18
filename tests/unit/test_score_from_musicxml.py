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


def test_chord_notes_of_a_dropped_note_are_not_merged_into_an_earlier_note() -> None:
    conversion = _convert(
        _measure(
            f"{NOTE_C4}"
            "<note><pitch><step>H</step><octave>4</octave></pitch><duration>1</duration></note>"
            "<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration>"
            "</note>"
        )
    )

    events = _events(conversion)
    assert [(e["offset"], [p["step"] for p in e["pitches"]]) for e in events] == [
        ("0", ["C"]),
        ("1/4", ["E"]),
    ]
    assert set(_codes(conversion)) == {RunWarningCode.SCORE_INVALID_VALUE}


@pytest.mark.parametrize("tag", ["backup", "forward"])
def test_non_positive_backup_or_forward_is_invalid(tag: str) -> None:
    conversion = _convert(_measure(f"{NOTE_C4}<{tag}><duration>-1</duration></{tag}>{NOTE_C4}"))

    assert [e["offset"] for e in _events(conversion)] == ["0", None]
    assert _codes(conversion) == [RunWarningCode.SCORE_INVALID_VALUE]
    assert f"<{tag}> <duration>" in conversion.warnings[0].message


def test_chord_member_with_different_staff_is_reported() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            "<staff>1</staff></note>"
            "<note><chord/><pitch><step>E</step><octave>3</octave></pitch><duration>1</duration>"
            '<staff>2</staff><notations><tuplet type="start"/></notations></note>'
        )
    )

    (event,) = _events(conversion)
    assert [p["octave"] for p in event["pitches"]] == [4, 3]
    assert event["staff"] == 1
    assert event["tuplets"] == [{"type": "start", "number": None}]
    assert _codes(conversion) == [RunWarningCode.SCORE_UNSUPPORTED_ELEMENT]
    assert "staff differs" in conversion.warnings[0].message


@pytest.mark.parametrize(
    "content", ['<sound tempo="0"/>', '<direction><sound tempo="-5"/></direction>']
)
def test_non_positive_tempo_gives_no_tempo_event(content: str) -> None:
    conversion = _convert(_measure(content))

    assert _events(conversion) == []
    assert _codes(conversion) == [RunWarningCode.SCORE_INVALID_VALUE]


def test_tuplet_repeated_on_chord_members_is_kept_once() -> None:
    tuplet = '<notations><tuplet type="start" number="1"/></notations>'
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            f"{tuplet}</note>"
            "<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration>"
            f"{tuplet}</note>"
        )
    )

    assert _events(conversion)[0]["tuplets"] == [{"type": "start", "number": 1}]


def test_repeated_verse_on_a_chord_member_is_kept_once() -> None:
    lyric = '<lyric number="1"><text>a</text></lyric>'
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            f"{lyric}</note>"
            "<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration>"
            f"{lyric}</note>"
        )
    )

    assert len(_events(conversion)[0]["lyrics"]) == 1
    assert _codes(conversion) == [RunWarningCode.SCORE_UNSUPPORTED_ELEMENT]


def test_chord_member_with_empty_duration_keeps_later_offsets() -> None:
    conversion = _convert(
        _measure(
            f"{NOTE_C4}"
            "<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration/></note>"
            f"{NOTE_C4}"
        )
    )

    assert [e["offset"] for e in _events(conversion)] == ["0", "1/4"]
    assert "its duration was recorded as null" in conversion.warnings[0].message


@pytest.mark.parametrize("duration", ["<duration>abc</duration>", "<duration>0</duration>", ""])
def test_invalid_note_duration_says_later_offsets_are_null(duration: str) -> None:
    conversion = _convert(
        _measure(f"<note><pitch><step>C</step><octave>4</octave></pitch>{duration}</note>")
    )

    assert "later offsets in the measure are null" in conversion.warnings[0].message


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


def test_empty_elision_keeps_the_syllable_boundary() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            '<lyric><text>a</text><elision smufl="lyricsElision"/><text>e</text></lyric></note>'
        )
    )

    assert _events(conversion)[0]["lyrics"][0]["text"] == "a\u203fe"


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
        ('<sound dacapo="yes"/>', "<sound dacapo>"),
        ('<direction><sound fine="yes"/></direction>', "<sound fine>"),
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


@pytest.mark.parametrize(
    "body",
    [
        "<note><pitch><step>C</step><octave>4</octave></pitch>"
        "<duration>1e999999999</duration></note>",
        "<attributes><divisions>1e999999999</divisions></attributes>",
    ],
)
def test_exponent_numbers_are_rejected_without_being_computed(body: str) -> None:
    conversion = _convert(_measure(body))

    assert RunWarningCode.SCORE_INVALID_VALUE in _codes(conversion)


@pytest.mark.parametrize(
    "content",
    [
        '<attributes><clef number="²"><sign>G</sign><line>2</line></clef></attributes>',
        '<barline><repeat direction="backward" times="²"/></barline>',
        "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
        '<notations><tuplet type="start" number="²"/></notations></note>',
    ],
)
def test_unicode_digits_in_number_attributes_do_not_crash(content: str) -> None:
    conversion = _convert(_measure(content))

    assert conversion.score["parts"][0]["measures"][0]


@pytest.mark.parametrize(
    "content",
    [
        "<attributes><key><fifths>1_0</fifths></key></attributes>",
        "<note><pitch><step>C</step><octave>\u0664</octave></pitch><duration>1</duration></note>",
        "<note><pitch><step>C</step><alter>1e0</alter><octave>4</octave></pitch>"
        "<duration>1</duration></note>",
        "<note><pitch><step>C</step><alter>1_0</alter><octave>4</octave></pitch>"
        "<duration>1</duration></note>",
        "<note><pitch><step>C</step><alter>1" + "0" * 39 + "</alter><octave>4</octave></pitch>"
        "<duration>1</duration></note>",
    ],
)
def test_numbers_must_be_plain_ascii_xml_numbers(content: str) -> None:
    conversion = _convert(_measure(content))

    assert _events(conversion) == []
    assert RunWarningCode.SCORE_INVALID_VALUE in _codes(conversion)


def test_very_long_decimals_are_rejected() -> None:
    nines = "9" * 4299
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            f"<duration>{nines}</duration></note>",
            divisions="0.001",
        )
    )

    assert _events(conversion)[0]["duration"] is None
    assert _codes(conversion) == [RunWarningCode.SCORE_INVALID_VALUE]


def test_position_that_grows_too_precise_becomes_unknown() -> None:
    content = "".join(
        f"<attributes><divisions>{10**30 + k}</divisions></attributes>{NOTE_C4}"
        for k in range(1, 30, 2)
    )
    conversion = _convert(_measure(content))

    assert _events(conversion)[-1]["offset"] is None
    assert _codes(conversion) == [RunWarningCode.SCORE_TIMING_INCONSISTENT]


@pytest.mark.parametrize(
    "content",
    [
        "<backup/>",
        "<forward><duration> </duration></forward>",
        "<note><pitch><step>D</step><octave>4</octave></pitch><duration/></note>",
    ],
)
def test_missing_or_empty_durations_are_reported(content: str) -> None:
    conversion = _convert(_measure(f"{NOTE_C4}{content}{NOTE_C4}"))

    assert _events(conversion)[-1]["offset"] is None
    assert _codes(conversion) == [RunWarningCode.SCORE_INVALID_VALUE]


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


def test_measure_level_sound_offset_moves_the_tempo() -> None:
    conversion = _convert(_measure('<sound tempo="80"><offset>2</offset></sound>'))

    assert _events(conversion)[0]["offset"] == "1/2"
    assert conversion.warnings == ()


@pytest.mark.parametrize(
    "content",
    [
        '<sound tempo="80"><swing><straight/></swing></sound>',
        '<direction><sound tempo="80"><offset>1</offset></sound></direction>',
    ],
)
def test_unmodelled_sound_children_are_reported(content: str) -> None:
    conversion = _convert(_measure(content))

    assert _codes(conversion) == [RunWarningCode.SCORE_UNSUPPORTED_ELEMENT]


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


def test_different_bad_values_of_one_construct_share_a_warning() -> None:
    notes = "".join(
        f"<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
        f"<staff>1{letter}</staff></note>"
        for letter in "abc"
    )
    conversion = _convert(_measure(notes))

    assert [w.message for w in conversion.warnings] == [
        "Non-integer <staff> was recorded as null "
        "(3 occurrences; first '1a' in part 'P1', measure '1')."
    ]


def test_number_of_distinct_warnings_is_capped() -> None:
    tags = "".join(f"<x{index}/>" for index in range(150))
    conversion = _convert(_measure(tags))

    assert len(conversion.warnings) == 101
    assert conversion.warnings[-1].message == (
        "50 more occurrence(s) of problems of other kinds were not listed."
    )


@pytest.mark.parametrize("number", ["x", "0"])
def test_invalid_tuplet_number_is_reported(number: str) -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration>"
            f'<notations><tuplet type="start" number="{number}"/></notations></note>'
        )
    )

    assert _events(conversion)[0]["tuplets"] == [{"type": "start", "number": None}]
    assert _codes(conversion) == [RunWarningCode.SCORE_INVALID_VALUE]


def test_offset_without_divisions_only_nulls_its_own_offset() -> None:
    conversion = _convert(
        _measure(
            "<harmony><root><root-step>C</root-step></root><kind>major</kind>"
            "<offset>2</offset></harmony>",
            divisions=None,
        )
    )

    assert _events(conversion)[0]["offset"] is None
    assert "its offset was recorded as null" in conversion.warnings[0].message
    assert "later offsets" not in conversion.warnings[0].message


def test_long_offending_values_are_shortened_in_warnings() -> None:
    conversion = _convert(
        _measure(
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            f"<duration>{'1' * 5000}</duration></note>"
        )
    )

    (warning,) = conversion.warnings
    assert len(warning.message) < 200
    assert "(5000 characters)" in warning.message


def test_overflow_is_counted_per_warning_code() -> None:
    tags = "".join(f"<x{index}/>" for index in range(150))
    conversion = _convert(_measure(f"{tags}{NOTE_C4}<backup><duration>3</duration></backup>"))

    overflow = conversion.warnings[100:]
    assert [(w.code, w.message.split()[0]) for w in overflow] == [
        (RunWarningCode.SCORE_UNSUPPORTED_ELEMENT, "50"),
        (RunWarningCode.SCORE_TIMING_INCONSISTENT, "1"),
    ]


@pytest.mark.parametrize(
    ("value", "text"),
    [(Fraction(0), "0"), (Fraction(2), "2"), (Fraction(6, 8), "3/4"), (Fraction(1, 12), "1/12")],
)
def test_rational_writes_lowest_terms(value: Fraction, text: str) -> None:
    assert rational(value) == text


def test_rational_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        rational(Fraction(-1, 4))
