"""Convert a loaded MusicXML document into the score model (docs/score-model.md).

Only what the MusicXML says is recorded: absent values are ``None`` (``null``), never
guessed. Constructs the model has no field for produce a ``score_unsupported_element``
warning, aggregated per construct, instead of being dropped silently. Presentation-only
elements listed in ``IGNORED_ELEMENTS`` are skipped without a warning.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from omr_lab.musicxml import MusicXmlDocument
from omr_lab.schemas.codes import RunWarningCode

IGNORED_ELEMENTS: dict[str, frozenset[str]] = {
    "note": frozenset(
        {
            "stem",
            "beam",
            "notehead",
            "notehead-text",
            "accidental",
            "instrument",
            "play",
            "listen",
            "footnote",
            "level",
        }
    ),
    "notations": frozenset({"tied", "footnote", "level"}),
    "attributes": frozenset({"staves", "instruments", "footnote", "level"}),
    "measure": frozenset({"print"}),
    "direction": frozenset({"voice", "listening", "footnote", "level"}),
    "harmony": frozenset({"frame", "footnote", "level"}),
    "barline": frozenset({"footnote", "level"}),
    "sound": frozenset({"instrument-change", "midi-device", "midi-instrument", "play"}),
    "lyric": frozenset({"footnote", "level"}),
    "clef": frozenset({"footnote", "level"}),
    "key": frozenset({"footnote", "level"}),
    "time": frozenset({"footnote", "level"}),
    "time-modification": frozenset({"footnote", "level"}),
}
"""Child elements skipped without a warning, by parent element.

They describe layout or playback (stems, beams, printed accidentals, page breaks,
instrument sounds) rather than the music the score model records. ``<staves>`` is
skipped because every event carries its own staff number; ``<tied>`` because the
sounding ``<tie>`` is recorded instead.
"""

_STEPS = frozenset("ABCDEFG")
_CLEF_SIGNS = frozenset({"G", "F", "C", "percussion", "TAB", "jianpu", "none"})
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
# Real files use a few digits; the cap keeps a crafted value from growing huge.
_MAX_DECIMAL_LENGTH = 32
# A position needing more bits can only come from many different <divisions> in one
# measure; past this it is treated as unknown rather than written as a huge rational.
_MAX_POSITION_BITS = 512
_ASCII_UNSIGNED_INT = re.compile(r"[0-9]+")
_ASCII_SIGNED_INT = re.compile(r"[+-]?[0-9]+")
_SYLLABIC = frozenset({"single", "begin", "middle", "end"})
# <sound> attributes that change the playing order; the model has no field for them yet.
_NAVIGATION_ATTRIBUTES = ("segno", "dalsegno", "coda", "tocoda", "dacapo", "fine", "forward-repeat")


@dataclass(frozen=True)
class ScoreWarning:
    """A construct that was not (fully) converted; same shape as run-document warnings."""

    code: RunWarningCode
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": str(self.code), "message": self.message}


@dataclass(frozen=True)
class ScoreConversion:
    """The result of a conversion: the ``score`` object and its warnings."""

    score: dict[str, Any]
    warnings: tuple[ScoreWarning, ...]


def rational(value: Fraction) -> str:
    """Write a non-negative Fraction as the schema's ``rational`` string ("3/4", "2")."""
    if value < 0:
        raise ValueError(f"rational values must not be negative: {value}")
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _nullable_rational(value: Fraction | None) -> str | None:
    return None if value is None else rational(value)


_MAX_DISTINCT_WARNINGS = 100
_MAX_EXAMPLE_LENGTH = 40


def _shortened(example: object) -> str:
    """repr() of an offending value, cut so a huge source value cannot bloat a warning."""
    text = repr(example)
    if len(text) <= _MAX_EXAMPLE_LENGTH:
        return text
    return f"{text[: _MAX_EXAMPLE_LENGTH - 1]}… ({len(str(example))} characters)"


class _Issues:
    """Collects problems per (code, construct) so a large score cannot flood warnings."""

    def __init__(self) -> None:
        self._counts: dict[tuple[RunWarningCode, str], int] = {}
        self._first: dict[tuple[RunWarningCode, str], str] = {}
        self._overflow: dict[RunWarningCode, int] = {}

    def add(self, code: RunWarningCode, what: str, where: str, example: object = None) -> None:
        """Count one problem. ``what`` must not contain the offending value (pass it as
        ``example``), so different bad values of one construct share one warning."""
        key = (code, what)
        if key not in self._counts:
            if len(self._counts) >= _MAX_DISTINCT_WARNINGS:
                self._overflow[code] = self._overflow.get(code, 0) + 1
                return
            self._counts[key] = 0
            self._first[key] = (
                f"in {where}" if example is None else f"{_shortened(example)} in {where}"
            )
        self._counts[key] += 1

    def warnings(self) -> tuple[ScoreWarning, ...]:
        result = []
        for (code, what), count in self._counts.items():
            times = "1 occurrence" if count == 1 else f"{count} occurrences"
            first = self._first[(code, what)]
            if code is RunWarningCode.SCORE_UNSUPPORTED_ELEMENT:
                message = f"{what} is not represented in the score ({times}; first {first})."
            else:
                message = f"{what} ({times}; first {first})."
            result.append(ScoreWarning(code, message))
        for code, count in self._overflow.items():
            result.append(
                ScoreWarning(
                    code, f"{count} more occurrence(s) of problems of other kinds were not listed."
                )
            )
        return tuple(result)


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


class _PartConverter:
    def __init__(self, part_id: str, issues: _Issues) -> None:
        self.part_id = part_id
        self.issues = issues
        self.divisions: Fraction | None = None
        self.where = f"part {part_id!r}"
        # Position from the start of the current measure, in whole notes; None once it
        # can no longer be computed (unknown divisions or an invalid duration).
        self.position: Fraction | None = Fraction(0)
        self.chord_start: Fraction | None = Fraction(0)
        self.last_note: dict[str, Any] | None = None

    # -- helpers -----------------------------------------------------------------------

    def unsupported(self, what: str) -> None:
        self.issues.add(RunWarningCode.SCORE_UNSUPPORTED_ELEMENT, what, self.where)

    def invalid(self, what: str, value: object = None) -> None:
        self.issues.add(RunWarningCode.SCORE_INVALID_VALUE, what, self.where, value)

    def check_children(self, element: ET.Element, handled: set[str], label: str) -> None:
        ignored = IGNORED_ELEMENTS.get(element.tag, frozenset())
        for child in element:
            if child.tag not in handled and child.tag not in ignored:
                self.unsupported(f"<{child.tag}> in <{label}>")

    def integer(self, element: ET.Element | None, label: str) -> int | None:
        value = _text(element)
        if value is None:
            return None
        if _ASCII_SIGNED_INT.fullmatch(value) is not None:
            try:
                return int(value)
            except ValueError:  # more digits than int() converts
                pass
        self.invalid(f"Non-integer {label} was recorded as null", value)
        return None

    def number(self, value: str | None, label: str) -> float | None:
        if value is None:
            return None
        # Same rules (and length cap) as durations, so no value is silently rounded.
        exact = _decimal(value)
        if exact is None:
            self.invalid(f"Invalid or too long {label} was recorded as null", value)
            return None
        return float(exact)

    def staff(self, element: ET.Element) -> int | None:
        staff = self.integer(element.find("staff"), "<staff>")
        if staff is not None and staff < 1:
            self.invalid("<staff> below 1 was recorded as null", staff)
            return None
        return staff

    def whole_notes(
        self,
        element: ET.Element | None,
        label: str,
        consequence: str = "later offsets in the measure are null",
    ) -> Fraction | None:
        """Convert a <duration>/<offset>-style value in divisions to whole notes.

        ``consequence`` ends the missing-divisions warning: what becomes null.
        """
        if element is None:
            return None
        value = _text(element)
        if value is None:
            self.invalid(f"An empty {label} was ignored; {consequence}")
            return None
        amount = _decimal(value)
        if amount is None:
            self.invalid(f"Invalid or too long {label} was ignored", value)
            return None
        if self.divisions is None:
            self.issues.add(
                RunWarningCode.SCORE_DIVISIONS_MISSING,
                f"{label} appears before any <divisions>, so its length is unknown and "
                f"{consequence}",
                self.where,
            )
            return None
        return amount / (self.divisions * 4)

    def advance(self, amount: Fraction | None) -> None:
        if self.position is None:
            return
        if amount is None:
            self.position = None
            return
        position = self.position + amount
        bits = max(position.numerator.bit_length(), position.denominator.bit_length())
        if bits > _MAX_POSITION_BITS:
            # Only possible with many different <divisions> in one measure.
            self.issues.add(
                RunWarningCode.SCORE_TIMING_INCONSISTENT,
                "The position in the measure became too precise to record; later offsets "
                "in the measure are null",
                self.where,
            )
            self.position = None
            return
        # motion() only passes positive lengths, so only a <backup> can go below zero.
        if position < 0:
            self.issues.add(
                RunWarningCode.SCORE_TIMING_INCONSISTENT,
                "<backup> moved before the start of the measure; later offsets in the "
                "measure are null",
                self.where,
            )
            self.position = None
            return
        self.position = position

    def check_navigation(self, sound: ET.Element) -> None:
        for attribute in _NAVIGATION_ATTRIBUTES:
            if sound.get(attribute) is not None:
                self.unsupported(f"<sound {attribute}> (song structure)")

    def motion(self, element: ET.Element) -> Fraction | None:
        """Length of a <backup> or <forward>; None (unknown) unless it is positive."""
        label = f"<{element.tag}> <duration>"
        duration = element.find("duration")
        if duration is None:
            self.invalid(
                f"A <{element.tag}> without <duration> was ignored; later offsets are null"
            )
            return None
        amount = self.whole_notes(duration, label)
        if amount is not None and amount <= 0:
            self.invalid(f"A {label} that is not positive was ignored; later offsets are null")
            return None
        return amount

    def drop_note(self, is_chord: bool) -> None:
        """Forget the previous note when a note is left out, so its <chord/> notes are
        not merged into an earlier, unrelated event."""
        if not is_chord:
            self.last_note = None

    def offset_of(self, element: ET.Element) -> Fraction | None:
        """Position of a <harmony>/<direction>, including its <offset> child."""
        offset_element = element.find("offset")
        if self.position is None:
            return None
        if offset_element is None:
            return self.position
        offset = self.whole_notes(offset_element, "<offset>", "its offset was recorded as null")
        if offset is None:
            return None
        position = self.position + offset
        if position < 0:
            self.issues.add(
                RunWarningCode.SCORE_TIMING_INCONSISTENT,
                f"<{element.tag}> <offset> points before the start of the measure; its "
                "offset was recorded as null",
                self.where,
            )
            return None
        return position

    # -- measures ----------------------------------------------------------------------

    def measure(self, element: ET.Element) -> dict[str, Any]:
        number = element.get("number")
        self.where = f"part {self.part_id!r}, measure {number!r}"
        self.position = Fraction(0)
        self.chord_start = Fraction(0)
        self.last_note = None
        events: list[dict[str, Any]] = []
        barlines: list[dict[str, Any]] = []
        for child in element:
            tag = child.tag
            if tag == "note":
                self.note(child, events)
            elif tag == "backup":
                amount = self.motion(child)
                self.advance(None if amount is None else -amount)
                self.last_note = None
            elif tag == "forward":
                self.advance(self.motion(child))
                self.last_note = None
            elif tag == "attributes":
                self.attributes(child, events)
            elif tag == "harmony":
                self.harmony(child, events)
            elif tag == "direction":
                self.direction(child, events)
            elif tag == "sound":
                self.check_navigation(child)
                self.check_children(child, {"offset"}, "sound")
                tempo = self.tempo_bpm(child)
                if tempo is not None:
                    events.append(self.tempo_event(self.offset_of(child), None, tempo, None, None))
            elif tag == "barline":
                barline = self.barline(child)
                if barline is not None:
                    barlines.append(barline)
            elif tag in IGNORED_ELEMENTS["measure"]:
                continue
            else:
                self.unsupported(f"<{tag}> in <measure>")
        return {
            "number": number,
            "implicit": element.get("implicit") == "yes",
            "barlines": barlines,
            "events": events,
        }

    # -- notes and rests -----------------------------------------------------------------

    def note(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        grace_element = element.find("grace")
        is_chord = element.find("chord") is not None
        duration = None
        if grace_element is None:
            duration_element = element.find("duration")
            if duration_element is None:
                self.invalid("A <note> without <duration> was recorded with a null duration")
            else:
                duration = self.whole_notes(duration_element, "<note> <duration>")
                if duration is not None and duration <= 0:
                    self.invalid("A <note> <duration> that is not positive was ignored")
                    duration = None
        if is_chord:
            offset = self.chord_start
        else:
            offset = self.position
            self.chord_start = self.position
            if grace_element is None:
                self.advance(duration)

        handled = {
            "grace",
            "chord",
            "cue",
            "pitch",
            "unpitched",
            "rest",
            "duration",
            "tie",
            "voice",
            "type",
            "dot",
            "time-modification",
            "staff",
            "notations",
            "lyric",
        }
        self.check_children(element, handled, "note")
        if element.find("cue") is not None:
            self.unsupported("A cue <note>")
            self.drop_note(is_chord)
            return
        if element.find("unpitched") is not None:
            self.unsupported("An unpitched <note>")
            self.drop_note(is_chord)
            return
        # Checked here, not in duration_fields(), so merged <chord/> notes are covered too.
        for notations in element.findall("notations"):
            self.check_children(notations, {"tuplet"}, "notations")

        lyrics = [self.lyric(lyric) for lyric in element.findall("lyric")]
        rest = element.find("rest")
        if rest is not None:
            if is_chord:
                self.invalid("A <rest> marked as <chord/> was converted as a separate rest")
            event = self.duration_fields("rest", element, offset, duration)
            event["measure_rest"] = rest.get("measure") == "yes"
            events.append(event)
            if lyrics:
                self.unsupported("<lyric> on a rest")
            self.last_note = None
            return

        pitch = self.pitch(element)
        if pitch is None:
            self.drop_note(is_chord)
            return
        if is_chord and self.last_note is not None:
            self.merge_chord_note(element, offset, duration, pitch, lyrics)
            return
        if is_chord:
            self.invalid("A <chord/> note without a preceding note was converted on its own")
        event = self.duration_fields("note", element, offset, duration)
        event["grace"] = (
            None if grace_element is None else {"slash": grace_element.get("slash") == "yes"}
        )
        event["pitches"] = [pitch]
        event["lyrics"] = lyrics
        events.append(event)
        self.last_note = event

    def merge_chord_note(
        self,
        element: ET.Element,
        offset: Fraction | None,
        duration: Fraction | None,
        pitch: dict[str, Any],
        lyrics: list[dict[str, Any]],
    ) -> None:
        """Add a <chord/> note to the chord's first note, reporting what cannot merge."""
        assert self.last_note is not None
        member = self.duration_fields("note", element, offset, duration)
        compared = ("voice", "staff", "duration", "type", "dots", "time_modification")
        differing = [name for name in compared if member[name] != self.last_note[name]]
        if differing:
            self.unsupported(
                "A <chord/> note whose " + "/".join(differing) + " differs from the chord's "
                "first note (kept as one chord with the first note's values)"
            )
        self.last_note["pitches"].append(pitch)
        # Exporters may repeat a chord's tuplet bracket on every member; keep it once.
        self.last_note["tuplets"].extend(
            tuplet for tuplet in member["tuplets"] if tuplet not in self.last_note["tuplets"]
        )
        verses = {lyric["number"] for lyric in self.last_note["lyrics"]}
        for lyric in lyrics:
            if lyric["number"] in verses:
                self.unsupported("A second lyric for the same verse on one chord")
            else:
                self.last_note["lyrics"].append(lyric)

    def tempo_bpm(self, sound: ET.Element) -> float | None:
        """<sound tempo> in quarter notes per minute; None (with a warning) unless positive."""
        bpm = self.number(sound.get("tempo"), "<sound tempo>")
        if bpm is not None and bpm <= 0:
            self.invalid("<sound tempo> that is not positive was ignored", bpm)
            return None
        return bpm

    def duration_fields(
        self, kind: str, element: ET.Element, offset: Fraction | None, duration: Fraction | None
    ) -> dict[str, Any]:
        time_modification = None
        modification = element.find("time-modification")
        if modification is not None:
            actual = self.integer(modification.find("actual-notes"), "<actual-notes>")
            normal = self.integer(modification.find("normal-notes"), "<normal-notes>")
            if actual is not None and normal is not None and actual >= 1 and normal >= 1:
                time_modification = {"actual_notes": actual, "normal_notes": normal}
            else:
                self.invalid("An incomplete <time-modification> was recorded as null")
            self.check_children(
                modification,
                {"actual-notes", "normal-notes", "normal-type", "normal-dot"},
                "time-modification",
            )
        tuplets = []
        for notations in element.findall("notations"):
            for tuplet in notations.findall("tuplet"):
                tuplet_type = tuplet.get("type")
                if tuplet_type not in ("start", "stop"):
                    self.invalid("An unknown <tuplet type> was ignored", tuplet_type)
                    continue
                number_text = tuplet.get("number")
                number = _ascii_int(number_text)
                if number_text is not None and (number is None or number < 1):
                    self.invalid("An invalid <tuplet number> was recorded as null", number_text)
                    number = None
                tuplets.append({"type": tuplet_type, "number": number})
        return {
            "kind": kind,
            "offset": _nullable_rational(offset),
            "voice": _text(element.find("voice")),
            "staff": self.staff(element),
            "duration": _nullable_rational(duration),
            "type": _text(element.find("type")),
            "dots": len(element.findall("dot")),
            "time_modification": time_modification,
            "tuplets": tuplets,
        }

    def pitch(self, element: ET.Element) -> dict[str, Any] | None:
        pitch = element.find("pitch")
        if pitch is None:
            self.invalid("A <note> with no <pitch> or <rest> was left out")
            return None
        step = _text(pitch.find("step"))
        octave = self.integer(pitch.find("octave"), "<octave>")
        alter_text = _text(pitch.find("alter"))
        alter = 0 if alter_text is None else self.number(alter_text, "<alter>")
        if step not in _STEPS or octave is None or not 0 <= octave <= 9 or alter is None:
            self.invalid("A <note> with an invalid <pitch> was left out")
            return None
        ties = []
        for tie in element.findall("tie"):
            tie_type = tie.get("type")
            if tie_type in ("start", "stop") and tie_type not in ties:
                ties.append(tie_type)
            elif tie_type not in ("start", "stop"):
                self.invalid("An unknown <tie type> was ignored", tie_type)
        return {
            "step": step,
            "alter": _compact(alter),
            "octave": octave,
            "ties": ties,
        }

    def lyric(self, element: ET.Element) -> dict[str, Any]:
        texts: list[str] = []
        syllabics: list[str] = []
        extend = None
        for child in element:
            if child.tag == "text":
                texts.append(child.text or "")
            elif child.tag == "elision":
                # An empty <elision/> is drawn as the default undertie; keep the boundary.
                texts.append(child.text or "\u203f")
            elif child.tag == "syllabic":
                value = _text(child)
                if value in _SYLLABIC:
                    syllabics.append(value)
                else:
                    self.invalid("An unknown <syllabic> was ignored", value)
            elif child.tag == "extend":
                extend_type = child.get("type")
                if extend_type not in (None, "start", "stop", "continue"):
                    self.invalid("An unknown <extend type> was recorded as null", extend_type)
                    extend_type = None
                extend = {"type": extend_type}
            elif child.tag not in IGNORED_ELEMENTS["lyric"]:
                self.unsupported(f"<{child.tag}> in <lyric>")
        return {
            "number": element.get("number"),
            "name": element.get("name"),
            "syllabic": _combined_syllabic(syllabics),
            "text": "".join(texts) if element.find("text") is not None else None,
            "extend": extend,
        }

    # -- attributes ----------------------------------------------------------------------

    def attributes(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        self.check_children(element, {"divisions", "key", "time", "clef"}, "attributes")
        divisions_element = element.find("divisions")
        if divisions_element is not None:
            value = _text(divisions_element)
            divisions = None if value is None else _decimal(value)
            if divisions is None or divisions <= 0:
                self.invalid("<divisions> that is not a positive number was ignored", value)
            else:
                self.divisions = divisions
        for key in element.findall("key"):
            self.key(key, events)
        for time in element.findall("time"):
            self.time(time, events)
        for clef in element.findall("clef"):
            self.clef(clef, events)

    def staff_attribute(self, element: ET.Element) -> int | None:
        value = element.get("number")
        if value is None:
            return None
        number = _ascii_int(value)
        if number is not None and number >= 1:
            return number
        self.invalid(f"An invalid <{element.tag} number> was recorded as a null staff", value)
        return None

    def key(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        if element.find("fifths") is None:
            self.unsupported("A non-traditional <key> (without <fifths>)")
            return
        self.check_children(element, {"fifths", "mode", "cancel"}, "key")
        fifths = self.integer(element.find("fifths"), "<fifths>")
        if fifths is None or not -11 <= fifths <= 11:
            self.invalid("A <key> with an invalid <fifths> was left out")
            return
        events.append(
            {
                "kind": "key",
                "offset": _nullable_rational(self.position),
                "staff": self.staff_attribute(element),
                "fifths": fifths,
                "mode": _text(element.find("mode")),
            }
        )

    def time(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        if element.find("senza-misura") is not None:
            self.unsupported("<senza-misura> in <time>")
            return
        self.check_children(element, {"beats", "beat-type"}, "time")
        beats = [_text(e) for e in element.findall("beats")]
        beat_types = [_text(e) for e in element.findall("beat-type")]
        if not beats or not beat_types or beats[0] is None or beat_types[0] is None:
            self.invalid("A <time> without <beats> and <beat-type> was left out")
            return
        if len(beats) > 1 or len(beat_types) > 1:
            self.unsupported("A composite <time> (several <beats>/<beat-type> pairs)")
        events.append(
            {
                "kind": "time",
                "offset": _nullable_rational(self.position),
                "staff": self.staff_attribute(element),
                "beats": beats[0],
                "beat_type": beat_types[0],
                "symbol": element.get("symbol"),
            }
        )

    def clef(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        self.check_children(element, {"sign", "line", "clef-octave-change"}, "clef")
        sign = _text(element.find("sign"))
        if sign not in _CLEF_SIGNS:
            self.invalid("A <clef> with an unknown <sign> was left out", sign)
            return
        events.append(
            {
                "kind": "clef",
                "offset": _nullable_rational(self.position),
                "staff": self.staff_attribute(element),
                "sign": sign,
                "line": self.integer(element.find("line"), "<line>"),
                "octave_change": self.integer(
                    element.find("clef-octave-change"), "<clef-octave-change>"
                ),
            }
        )

    # -- harmony, directions, barlines -----------------------------------------------------

    def pitch_class(self, element: ET.Element, prefix: str) -> dict[str, Any] | None:
        step = _text(element.find(f"{prefix}-step"))
        alter_text = _text(element.find(f"{prefix}-alter"))
        alter = 0 if alter_text is None else self.number(alter_text, f"<{prefix}-alter>")
        if step not in _STEPS or alter is None:
            return None
        return {"step": step, "alter": _compact(alter)}

    def harmony(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        roots = element.findall("root")
        if not roots:
            self.unsupported("A <harmony> without <root> (function or numeral)")
            return
        if len(roots) > 1:
            self.unsupported("A second chord in the same <harmony>")
        self.check_children(
            element, {"root", "kind", "bass", "degree", "offset", "staff"}, "harmony"
        )
        root = self.pitch_class(roots[0], "root")
        if root is None:
            self.invalid("A <harmony> with an invalid <root> was left out")
            return
        bass_element = element.find("bass")
        bass = None
        if bass_element is not None:
            bass = self.pitch_class(bass_element, "bass")
            if bass is None:
                self.invalid("An invalid <bass> was recorded as null")
        degrees = []
        for degree in element.findall("degree"):
            value = self.integer(degree.find("degree-value"), "<degree-value>")
            alter = self.number(_text(degree.find("degree-alter")), "<degree-alter>")
            degree_type = _text(degree.find("degree-type"))
            if (
                value is None
                or value < 1
                or alter is None
                or degree_type
                not in (
                    "add",
                    "alter",
                    "subtract",
                )
            ):
                self.invalid("An incomplete <degree> was left out")
                continue
            degrees.append(
                {
                    "value": value,
                    "alter": _compact(alter),
                    "type": degree_type,
                }
            )
        kind = element.find("kind")
        events.append(
            {
                "kind": "harmony",
                "offset": _nullable_rational(self.offset_of(element)),
                "staff": self.staff(element),
                "root": root,
                "kind_value": _text(kind),
                "kind_text": None if kind is None else kind.get("text"),
                "bass": bass,
                "degrees": degrees,
            }
        )

    def tempo_event(
        self,
        offset: Fraction | None,
        staff: int | None,
        bpm: float | None,
        metronome: ET.Element | None,
        words: str | None,
    ) -> dict[str, Any]:
        beat_unit = per_minute = None
        beat_unit_dots = 0
        if metronome is not None:
            if len(metronome.findall("beat-unit")) > 1:
                self.unsupported("A <metronome> with two beat units (metric modulation)")
            beat_unit = _text(metronome.find("beat-unit"))
            beat_unit_dots = _dots_after_first(metronome)
            per_minute = _text(metronome.find("per-minute"))
            if beat_unit is None or per_minute is None:
                self.unsupported("A <metronome> without <beat-unit> and <per-minute>")
        return {
            "kind": "tempo",
            "offset": _nullable_rational(offset),
            "staff": staff,
            "bpm": None if bpm is None else _compact(bpm),
            "beat_unit": beat_unit,
            "beat_unit_dots": beat_unit_dots,
            "per_minute": per_minute,
            "words": words,
        }

    def direction(self, element: ET.Element, events: list[dict[str, Any]]) -> None:
        self.check_children(element, {"direction-type", "offset", "staff", "sound"}, "direction")
        metronome = None
        words: list[str] = []
        others: list[str] = []
        for direction_type in element.findall("direction-type"):
            for child in direction_type:
                if child.tag == "metronome" and metronome is None:
                    metronome = child
                elif child.tag == "words":
                    if child.text and child.text.strip():
                        words.append(child.text.strip())
                else:
                    others.append(child.tag)
        sound = element.find("sound")
        if sound is not None:
            self.check_navigation(sound)
            # Its own <offset> would shift playback from the printed direction; not modelled.
            self.check_children(sound, set(), "sound")
        bpm = None if sound is None else self.tempo_bpm(sound)
        for tag in others:
            self.unsupported(f"<{tag}> in <direction-type>")
        if metronome is None and bpm is None:
            if words:
                self.unsupported("<words> in <direction-type> without a tempo")
            return
        events.append(
            self.tempo_event(
                self.offset_of(element),
                self.staff(element),
                bpm,
                metronome,
                " ".join(words) if words else None,
            )
        )

    def barline(self, element: ET.Element) -> dict[str, Any] | None:
        self.check_children(element, {"bar-style", "repeat", "ending"}, "barline")
        location = element.get("location", "right")
        if location not in ("left", "right", "middle"):
            self.invalid("A <barline> with an unknown location was left out", location)
            return None
        repeat = None
        repeat_element = element.find("repeat")
        if repeat_element is not None:
            direction = repeat_element.get("direction")
            times_text = repeat_element.get("times")
            times = _ascii_int(times_text)
            if times_text is not None and (times is None or times < 1):
                self.invalid("An invalid <repeat times> was recorded as null", times_text)
                times = None
            if direction in ("forward", "backward"):
                repeat = {"direction": direction, "times": times}
            else:
                self.invalid("An unknown <repeat direction> was ignored", direction)
        ending = None
        ending_element = element.find("ending")
        if ending_element is not None:
            number = ending_element.get("number")
            ending_type = ending_element.get("type")
            if number is not None and ending_type in ("start", "stop", "discontinue"):
                ending = {"number": number, "type": ending_type, "text": _text(ending_element)}
            else:
                self.invalid("An <ending> without a valid number and type was ignored")
        return {
            "location": location,
            "style": _text(element.find("bar-style")),
            "repeat": repeat,
            "ending": ending,
        }


def _decimal(value: str) -> Fraction | None:
    """Parse an xs:decimal of at most _MAX_DECIMAL_LENGTH characters.

    No exponent ('1e999999999') and no very long text, so the exact arithmetic on
    the result stays small and its rational strings stay within int/str limits.
    """
    if len(value) > _MAX_DECIMAL_LENGTH or _DECIMAL.fullmatch(value) is None:
        return None
    return Fraction(value)


def _ascii_int(value: str | None) -> int | None:
    """Parse an unsigned ASCII integer attribute; None for anything else (e.g. '²')."""
    if value is None or _ASCII_UNSIGNED_INT.fullmatch(value) is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _compact(value: float) -> int | float:
    """Write whole numbers as int (1, not 1.0) so the JSON matches the source text."""
    return int(value) if float(value).is_integer() else value


def _dots_after_first(metronome: ET.Element) -> int:
    """Count the <beat-unit-dot> elements of the first <beat-unit> only."""
    dots = 0
    seen_unit = False
    for child in metronome:
        if child.tag == "beat-unit":
            if seen_unit:
                break
            seen_unit = True
        elif child.tag == "beat-unit-dot" and seen_unit:
            dots += 1
    return dots


def _combined_syllabic(syllabics: list[str]) -> str | None:
    """Combine the syllabic values of elided syllables into one word position."""
    if not syllabics:
        return None
    starts_word = syllabics[0] in ("single", "begin")
    ends_word = syllabics[-1] in ("single", "end")
    if starts_word:
        return "single" if ends_word else "begin"
    return "end" if ends_word else "middle"


def score_from_musicxml(
    document: MusicXmlDocument, *, source_artifact: str | None = None
) -> ScoreConversion:
    """Convert ``document`` into a ``score`` object (docs/score-model.md).

    ``source_artifact`` is the artifact kind of the MusicXML file in the run document
    (for example ``"musicxml"``), or None when the file is not a recorded artifact.
    Parts are listed in ``document.metadata.parts`` order; a declared part with no
    ``<part>`` element (already reported by the loader) is left out.
    """
    metadata = document.metadata
    issues = _Issues()
    part_elements: dict[str, ET.Element] = {}
    for part in document.root.findall("part"):
        part_id = part.get("id")
        if part_id and part_id not in part_elements:
            part_elements[part_id] = part
    parts = []
    for info in metadata.parts:
        element = part_elements.get(info.id)
        if element is None:
            continue
        converter = _PartConverter(info.id, issues)
        measures = [converter.measure(measure) for measure in element.findall("measure")]
        for child in element:
            if child.tag != "measure":
                issues.add(
                    RunWarningCode.SCORE_UNSUPPORTED_ELEMENT,
                    f"<{child.tag}> in <part>",
                    f"part {info.id!r}",
                )
        parts.append(
            {
                "id": info.id,
                "name": info.name,
                "abbreviation": info.abbreviation,
                "measures": measures,
            }
        )
    score = {
        "source": {"format": "musicxml", "artifact": source_artifact},
        "work_title": metadata.work_title,
        "work_number": metadata.work_number,
        "movement_title": metadata.movement_title,
        "movement_number": metadata.movement_number,
        "creators": [{"type": c.type, "name": c.name} for c in metadata.creators],
        "parts": parts,
    }
    return ScoreConversion(score=score, warnings=issues.warnings())


__all__ = [
    "IGNORED_ELEMENTS",
    "ScoreConversion",
    "ScoreWarning",
    "rational",
    "score_from_musicxml",
]
