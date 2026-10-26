"""Musical-correctness metrics: compare a recognized score with its ground truth.

Both scores are in the score model of docs/score-model.md (the ``score`` field of a run
document). Definitions and a worked example are in docs/metrics.md. Every count comes
from aligning the two scores; nothing is guessed.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, TypeVar

MAX_ALIGNMENT_CELLS = 4_000_000
"""Largest alignment (ground-truth items × predicted items) compared; about a few
seconds in pure Python. Lead sheets have a few hundred notes."""

T = TypeVar("T")


class ComparisonTooLargeError(ValueError):
    """An alignment would exceed MAX_ALIGNMENT_CELLS."""


@dataclass(frozen=True, slots=True)
class _Pitch:
    step: str
    alter: Fraction
    octave: int


@dataclass(frozen=True, slots=True)
class _Item:
    """One note or rest of the compared part, in reading order."""

    kind: str  # "note" or "rest"
    pitches: frozenset[_Pitch] | None
    """Spelled pitches of a note (several for a chord); None for a rest or an unreadable
    pitch."""
    duration: Fraction | None


def compare_scores(
    ground_truth: dict[str, Any], predicted: dict[str, Any] | None
) -> dict[str, Any]:
    """Compare the first part of ``predicted`` with the first part of ``ground_truth``.

    ``predicted`` is None when the run produced no score (it failed, or its MusicXML
    could not be converted): everything in the ground truth then counts as missed.
    Returns the JSON-ready ``correctness`` object of a benchmark report. Raises
    ComparisonTooLargeError when an alignment would exceed MAX_ALIGNMENT_CELLS.
    """
    truth = _part(ground_truth)
    guess = _part(predicted) if predicted is not None else []
    truth_measures = [_measure_items(m) for m in truth]
    guess_measures = [_measure_items(m) for m in guess]
    truth_items = [item for items in truth_measures for item in items]
    guess_items = [item for items in guess_measures for item in items]
    return {
        "prediction": "score" if predicted is not None else "missing",
        "parts": {
            "ground_truth": len(ground_truth["parts"]),
            "predicted": len(predicted["parts"]) if predicted is not None else 0,
        },
        "key_signatures": _signatures(truth, guess, "key", _key_value),
        "time_signatures": _signatures(truth, guess, "time", _time_value),
        "measures": _measures(truth_measures, guess_measures),
        "notes": _notes(truth_items, guess_items),
        "chord_symbols": _chord_symbols(truth, guess),
        "lyrics": _lyrics(truth, guess),
    }


def summarize(correctness: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Totals over several samples' correctness objects; None when there are none.

    Counts are summed first and rates computed from the sums (micro-averaging), so a
    long score weighs more than a short one.
    """
    if not correctness:
        return None
    measures = _sum(c["measures"] for c in correctness)
    notes = {
        key: _sum(c["notes"][key] for c in correctness)
        for key in ("pitch", "duration", "pitch_and_duration")
    }
    rests = _sum(c["notes"]["rests"] for c in correctness)
    chords = _sum(c["chord_symbols"] for c in correctness)
    verses = [verse for c in correctness for verse in c["lyrics"]["verses"]]
    return {
        "samples": len(correctness),
        "predictions_missing": sum(c["prediction"] == "missing" for c in correctness),
        "key_signatures_correct": sum(c["key_signatures"]["correct"] for c in correctness),
        "time_signatures_correct": sum(c["time_signatures"]["correct"] for c in correctness),
        "measures": {**measures, "accuracy": _ratio(measures["exact"], _measure_base(measures))},
        "notes": {
            "ground_truth": sum(c["notes"]["ground_truth"] for c in correctness),
            "predicted": sum(c["notes"]["predicted"] for c in correctness),
            **{key: _prf(counts) for key, counts in notes.items()},
            "rests": _prf(rests),
        },
        "chord_symbols": _prf(chords),
        "lyrics": _lyric_totals(verses),
    }


# --- Reading the score model --------------------------------------------------------


def _part(score: dict[str, Any]) -> list[dict[str, Any]]:
    """Measures of the first part, or none."""
    parts = score["parts"]
    return parts[0]["measures"] if parts else []


def _events(measures: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for measure in measures for event in measure["events"] if event["kind"] == kind]


def _measure_items(measure: dict[str, Any]) -> tuple[_Item, ...]:
    """Notes and rests in reading order; grace notes and hidden rests are left out,
    because they take no time and are not printed, respectively."""
    items = []
    for event in measure["events"]:
        if event["kind"] == "note" and event["grace"] is None:
            items.append(
                _Item(kind="note", pitches=_pitches(event), duration=_fraction(event["duration"]))
            )
        elif event["kind"] == "rest" and not event["hidden"]:
            items.append(_Item(kind="rest", pitches=None, duration=_fraction(event["duration"])))
    return tuple(items)


def _pitches(event: dict[str, Any]) -> frozenset[_Pitch] | None:
    pitches = frozenset(
        _Pitch(step=p["step"], alter=Fraction(str(p["alter"])), octave=p["octave"])
        for p in event["pitches"]
        if p["step"] is not None and p["alter"] is not None and p["octave"] is not None
    )
    return pitches or None


def _fraction(value: str | None) -> Fraction | None:
    return Fraction(value) if value is not None else None


def _key_value(event: dict[str, Any]) -> int | None:
    return event["fifths"]


def _time_value(event: dict[str, Any]) -> str | None:
    if event["beats"] is None or event["beat_type"] is None:
        return None
    return f"{event['beats']}/{event['beat_type']}"


# --- Metrics -----------------------------------------------------------------------


def _signatures(
    truth: list[dict[str, Any]],
    guess: list[dict[str, Any]],
    kind: str,
    value: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    truth_values = _changes(_initial(truth, kind) + [value(e) for e in _events(truth, kind)])
    guess_values = _changes(_initial(guess, kind) + [value(e) for e in _events(guess, kind)])
    return {
        "ground_truth": truth_values,
        "predicted": guess_values,
        "correct": truth_values == guess_values,
    }


def _initial(measures: list[dict[str, Any]], kind: str) -> list[Any]:
    """A score whose first measure prints no key signature is in 0 fifths: that is what
    no key signature means, and engines leave the empty <key> out (C major written with
    <fifths>0</fifths> and without any <key> must compare equal). A missing time
    signature has no such meaning, so nothing is assumed for it."""
    if kind != "key" or not measures:
        return []
    starts_with_key = any(event["kind"] == "key" for event in measures[0]["events"])
    return [] if starts_with_key else [0]


def _changes(values: Any) -> list[Any]:
    """The values in order, without repeats of the value before (a key restated at
    every system is one key)."""
    result: list[Any] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def _measures(truth: list[tuple[_Item, ...]], guess: list[tuple[_Item, ...]]) -> dict[str, Any]:
    pairs = _align(truth, guess, lambda a, b: 0.0 if a == b else 1.0)
    exact = sum(1 for a, b in pairs if a is not None and b is not None and a == b)
    counts = {"ground_truth": len(truth), "predicted": len(guess), "exact": exact}
    return {**counts, "accuracy": _ratio(exact, _measure_base(counts))}


def _measure_base(counts: dict[str, int]) -> int:
    return max(counts["ground_truth"], counts["predicted"])


def _item_cost(a: _Item, b: _Item) -> float:
    if a.kind != b.kind:
        return 1.0
    same_pitch = a.pitches is not None and a.pitches == b.pitches
    same_duration = a.duration is not None and a.duration == b.duration
    if a.kind == "rest":
        return 0.0 if same_duration else 0.5
    return 1.0 - 0.5 * same_pitch - 0.5 * same_duration


def _notes(truth: list[_Item], guess: list[_Item]) -> dict[str, Any]:
    pairs = _align(truth, guess, _item_cost)
    truth_notes = sum(item.kind == "note" for item in truth)
    guess_notes = sum(item.kind == "note" for item in guess)
    matched = {"pitch": 0, "duration": 0, "pitch_and_duration": 0}
    rests_matched = 0
    for a, b in pairs:
        if a is None or b is None or a.kind != b.kind:
            continue
        same_duration = a.duration is not None and a.duration == b.duration
        if a.kind == "rest":
            rests_matched += same_duration
            continue
        same_pitch = a.pitches is not None and a.pitches == b.pitches
        matched["pitch"] += same_pitch
        matched["duration"] += same_duration
        matched["pitch_and_duration"] += same_pitch and same_duration
    rests = {
        "ground_truth": len(truth) - truth_notes,
        "predicted": len(guess) - guess_notes,
        "matched": rests_matched,
    }
    return {
        "ground_truth": truth_notes,
        "predicted": guess_notes,
        **{
            key: _prf({"ground_truth": truth_notes, "predicted": guess_notes, "matched": n})
            for key, n in matched.items()
        },
        "rests": _prf(rests),
    }


def _chord_symbols(truth: list[dict[str, Any]], guess: list[dict[str, Any]]) -> dict[str, Any]:
    truth_chords = [_chord(e) for e in _events(truth, "harmony")]
    guess_chords = [_chord(e) for e in _events(guess, "harmony")]
    pairs = _align(truth_chords, guess_chords, lambda a, b: 0.0 if a == b else 1.0)
    matched = sum(1 for a, b in pairs if a is not None and b is not None and a == b)
    return _prf(
        {"ground_truth": len(truth_chords), "predicted": len(guess_chords), "matched": matched}
    )


def _chord(event: dict[str, Any]) -> tuple[Any, ...]:
    """What a chord symbol means: root, kind, bass and degrees; not how it is printed
    (``chord_kind_text``)."""
    root = event["root"]
    bass = event["bass"]
    return (
        root["step"],
        root["alter"],
        event["chord_kind"],
        (bass["step"], bass["alter"]) if bass is not None else None,
        tuple((d["value"], d["alter"], d["type"]) for d in event["degrees"]),
    )


def _lyrics(truth: list[dict[str, Any]], guess: list[dict[str, Any]]) -> dict[str, Any]:
    truth_verses = _verses(truth)
    guess_verses = _verses(guess)
    verses = []
    for number, syllables in truth_verses.items():
        predicted = guess_verses.get(number, [])
        truth_text = "".join(syllables)
        guess_text = "".join(predicted)
        verses.append(
            {
                "number": number,
                "ground_truth_syllables": len(syllables),
                "predicted_syllables": len(predicted),
                "syllable_errors": _edit_distance(syllables, predicted),
                "ground_truth_characters": len(truth_text),
                "predicted_characters": len(guess_text),
                "character_errors": _edit_distance(list(truth_text), list(guess_text)),
            }
        )
    for verse in verses:
        _add_lyric_rates(verse)
    return {
        "verses": verses,
        "extra_predicted_verses": [n for n in guess_verses if n not in truth_verses],
    }


def _verses(measures: list[dict[str, Any]]) -> dict[str | None, list[str]]:
    """Syllable texts per lyric ``number``, in note order. Extender-only lyrics (no
    text) are left out; texts are NFC-normalized and stripped."""
    verses: dict[str | None, list[str]] = {}
    for event in _events(measures, "note"):
        for lyric in event["lyrics"]:
            text = lyric["text"]
            if text is None:
                continue
            text = unicodedata.normalize("NFC", text).strip()
            if text:
                verses.setdefault(lyric["number"], []).append(text)
    return verses


def _add_lyric_rates(verse: dict[str, Any]) -> None:
    verse["syllable_error_rate"] = _ratio(verse["syllable_errors"], verse["ground_truth_syllables"])
    verse["character_error_rate"] = _ratio(
        verse["character_errors"], verse["ground_truth_characters"]
    )


def _lyric_totals(verses: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "ground_truth_syllables",
        "predicted_syllables",
        "syllable_errors",
        "ground_truth_characters",
        "predicted_characters",
        "character_errors",
    )
    totals: dict[str, Any] = {"verses": len(verses), **{k: sum(v[k] for v in verses) for k in keys}}
    _add_lyric_rates(totals)
    return totals


# --- Counting helpers ---------------------------------------------------------------


def _prf(counts: dict[str, int]) -> dict[str, Any]:
    precision = _ratio(counts["matched"], counts["predicted"])
    recall = _ratio(counts["matched"], counts["ground_truth"])
    # The harmonic mean of precision and recall, computed from the counts so that it is
    # also defined (0) when nothing was predicted.
    f1 = _ratio(2 * counts["matched"], counts["ground_truth"] + counts["predicted"])
    return {**counts, "precision": precision, "recall": recall, "f1": f1}


def _sum(items: Any) -> dict[str, int]:
    totals = {"ground_truth": 0, "predicted": 0}
    for item in items:
        for key, value in item.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


# --- Alignment ----------------------------------------------------------------------


def _edit_distance(truth: Sequence[T], guess: Sequence[T]) -> int:
    pairs = _align(truth, guess, lambda a, b: 0.0 if a == b else 1.0)
    return sum(1 for a, b in pairs if a is None or b is None or a != b)


def _align(
    truth: Sequence[T], guess: Sequence[T], cost: Callable[[T, T], float]
) -> list[tuple[T | None, T | None]]:
    """Minimum-cost alignment (insertions and deletions cost 1, substitutions ``cost``).

    Returns the aligned pairs in order; None marks a missed or an extra item. Ties
    prefer a substitution, then a missed ground-truth item, so the result is
    deterministic.
    """
    n, m = len(truth), len(guess)
    if n * m > MAX_ALIGNMENT_CELLS:
        raise ComparisonTooLargeError(
            f"Aligning {n} ground-truth items with {m} predicted items exceeds "
            f"{MAX_ALIGNMENT_CELLS} cells."
        )
    # Costs are multiples of 0.5, so float sums are exact.
    table = [[0.0] * (m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        table[0][j] = float(j)
    for i in range(1, n + 1):
        row, above = table[i], table[i - 1]
        row[0] = float(i)
        a = truth[i - 1]
        for j in range(1, m + 1):
            row[j] = min(above[j - 1] + cost(a, guess[j - 1]), above[j] + 1.0, row[j - 1] + 1.0)
    pairs: list[tuple[T | None, T | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if (
            i > 0
            and j > 0
            and table[i][j] == table[i - 1][j - 1] + cost(truth[i - 1], guess[j - 1])
        ):
            pairs.append((truth[i - 1], guess[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and table[i][j] == table[i - 1][j] + 1.0:
            pairs.append((truth[i - 1], None))
            i -= 1
        else:
            pairs.append((None, guess[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


__all__ = [
    "MAX_ALIGNMENT_CELLS",
    "ComparisonTooLargeError",
    "compare_scores",
    "summarize",
]
