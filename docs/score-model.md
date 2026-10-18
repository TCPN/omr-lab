# Score Model (v0.1, run schema 0.2.0)

The `score` field of a run document holds the music read from one source artifact in a small, engine-independent shape. It is aimed at what the downstream consumer ([TCPN/song_helper](https://github.com/TCPN/song_helper)) stores as ABC: single melody lines with chord symbols and lyrics, often several verses under the same notes.

- Schema (source of truth): `$defs/score` and the definitions it references in [`src/omr_lab/schemas/run-0.2.0.schema.json`](../src/omr_lab/schemas/run-0.2.0.schema.json). The score model is versioned with the run document; there is no separate schema file.
- Converter: `omr_lab.score.score_from_musicxml(document, *, source_artifact=None)` takes a `MusicXmlDocument` from `omr_lab.musicxml` and returns a `ScoreConversion` with `score` (a dict ready for the run document) and `warnings` (`ScoreWarning` objects with `code`, `message` and `to_dict()`).
- Examples: [`tests/fixtures/musicxml/score-features.musicxml`](../tests/fixtures/musicxml/score-features.musicxml) and its expected conversion [`score-features.score.json`](../tests/fixtures/musicxml/score-features.score.json); a full run document with a score is [`tests/fixtures/run-documents/valid/succeeded-with-score.json`](../tests/fixtures/run-documents/valid/succeeded-with-score.json).

The pipeline does not fill `score` yet: it still writes `score: null` with a `score_not_normalized` warning. A converted score records what the MusicXML says; it is not evidence that the recognition was correct.

## Principles

- Every value comes from the source. Absent values are `null` (or `[]`), never guessed. There are no coordinates or confidence values, because MusicXML from the engines does not carry them (page geometry is ADR 0002, issue #20).
- Exceptions where the MusicXML default is the meaning, not a guess: `pitch.alter` and `pitchClass.alter` are `0` when `<alter>`/`<root-alter>`/`<bass-alter>` is absent (MusicXML defines absence as no alteration); `measure.implicit` is `false` unless `implicit="yes"`; `dots` is the number of `<dot/>` elements; a barline without `location` is `right` (the MusicXML default).
- Constructs the model cannot hold produce warnings; they are never dropped silently. Pure layout and playback elements are skipped on purpose (see [Ignored elements](#ignored-elements)).
- Notes stay in source order. Nothing is re-spelled, transposed, re-beamed or quantized.

## Time values

Offsets and durations are exact rationals in **whole notes**, written as strings `"n"` or `"n/d"`: a quarter note is `"1/4"`, a dotted quarter `"3/8"`, a triplet eighth `"1/12"`. The converter writes lowest terms; the schema checks only the syntax. They are computed as `<duration> / (4 × <divisions>)`, so they do not depend on the file's `<divisions>`.

- `offset` is the position from the start of the measure. `<backup>` and `<forward>` move the position; grace notes and `<chord/>` notes do not advance it. `<harmony>` and `<direction>` add their own `<offset>` child.
- `duration` is the sounding length. It is `null` for grace notes.
- When `<divisions>` has not appeared yet, or a duration is invalid, that `duration` is `null` and every later `offset` in the measure is `null` (`score_divisions_missing` or `score_invalid_value`). A `<backup>` past the start of the measure makes later offsets `null` (`score_timing_inconsistent`).

## Shape

```text
score
├── source {format: "musicxml", artifact}
├── work_title, work_number, movement_title, movement_number
├── creators[] {type, name}
└── parts[] {id, name, abbreviation}
    └── measures[] {number, implicit}
        ├── barlines[] {location, style, repeat, ending}
        └── events[]   (kind: note | rest | harmony | tempo | clef | key | time)
```

`source.artifact` is the artifact kind of the MusicXML file in the same run document (usually `"musicxml"`), or `null` when the file is not a recorded artifact. Parts follow the loader's part order (`document.metadata.parts`): declared parts first, then undeclared ones. A declared part without a `<part>` element is left out; the loader already warns about it.

Events of a measure are in source order. Every event has `kind` and `offset`; most have `staff` (1-based `<staff>` or the `number` attribute of `<clef>`/`<key>`/`<time>`, `null` when not given).

| `kind` | Fields |
| --- | --- |
| `note` | `voice`, `staff`, `duration`, `type`, `dots`, `time_modification` `{actual_notes, normal_notes}` \| `null`, `tuplets[]` `{type: start\|stop, number}`, `grace` `{slash}` \| `null`, `pitches[]` `{step, alter, octave, ties[]}`, `lyrics[]` |
| `rest` | `voice`, `staff`, `duration`, `type`, `dots`, `time_modification`, `tuplets[]`, `measure_rest` |
| `harmony` | `staff`, `root` `{step, alter}`, `kind_value`, `kind_text`, `bass` `{step, alter}` \| `null`, `degrees[]` `{value, alter, type}` |
| `tempo` | `staff`, `bpm`, `beat_unit`, `beat_unit_dots`, `per_minute`, `words` |
| `clef` | `staff`, `sign`, `line`, `octave_change` |
| `key` | `staff`, `fifths`, `mode` |
| `time` | `staff`, `beats`, `beat_type`, `symbol` |

`harmony.kind_text` is the `text` attribute of `<kind>` as written: `null` when absent, and `""` when the source says to print no kind text (for example a major chord shown as the root alone).

A chord is one `note` event with several `pitches`: each MusicXML `<chord/>` note is merged into the note before it, and its lyrics are appended to that event's `lyrics`.

### Lyrics and verses

Each `<lyric>` becomes one entry in the note's `lyrics`, in source order, with:

- `number`: the `number` attribute as written (usually the verse: `"1"`, `"2"`), or `null`;
- `name`: the `name` attribute (e.g. `"verse"`, `"chorus"`), or `null`;
- `syllabic`: `single`, `begin`, `middle`, `end` or `null`;
- `text`: the syllable, or `null` for a lyric with no `<text>` (an extender-only lyric continuing a melisma);
- `extend`: `{type}` from `<extend>` (`type` is `null` when not given), or `null`.

Several verses under the same notes stay separate: read verse *n* by taking the lyric with `number == n` from each note. Elided syllables (`<text>`, `<elision>`, `<text>`) become one lyric whose text joins the parts with the elision text (`"a‿b"`; an empty `<elision/>` is written as `‿`, U+203F, the undertie it is drawn as); its `syllabic` combines the first and last parts (`end` + `begin` gives `middle`). How lyric numbering varies between engines and how to map it to ABC verses is studied in issue #28.

## Example

The first notes of `score-features.musicxml` (pickup measure; the key, time, clef and tempo events are omitted here):

```json
{
  "number": "0",
  "implicit": true,
  "barlines": [],
  "events": [
    {"kind": "harmony", "offset": "0", "staff": null, "root": {"step": "G", "alter": 0},
     "kind_value": "major", "kind_text": null, "bass": null, "degrees": []},
    {"kind": "note", "offset": "0", "voice": "1", "staff": null, "duration": "1/4",
     "type": "quarter", "dots": 0, "time_modification": null, "tuplets": [], "grace": null,
     "pitches": [{"step": "D", "alter": 0, "octave": 4, "ties": []}],
     "lyrics": [
       {"number": "1", "name": null, "syllabic": "single", "text": "我", "extend": null},
       {"number": "2", "name": null, "syllabic": "single", "text": "你", "extend": null}
     ]}
  ]
}
```

## Coverage: score ↔ MusicXML ↔ ABC

The ABC column names the construct a later ABC export would use (ABC 2.1 as rendered by abcjs). No ABC exporter exists yet.

| Score field | MusicXML | ABC |
| --- | --- | --- |
| `work_title`, `movement_title` | `<work><work-title>`, `<movement-title>` | `T:` |
| `work_number`, `movement_number` | `<work><work-number>`, `<movement-number>` | `T:` or `N:` (no dedicated field) |
| `creators[]` | `<identification><creator type>` | `C:` (composer), `Z:` or `N:` for other roles |
| `parts[]` `id`, `name`, `abbreviation` | `<score-part id>`, `<part-name>`, `<part-abbreviation>` | `V:` with `name=` / `subname=` |
| `measures[].number`, `implicit` | `<measure number implicit>` | not written; an `implicit` first measure is a pickup (anacrusis) |
| `barlines[].style` | `<barline><bar-style>` | `\|`, `\|\|`, `\|]`, `[\|` |
| `barlines[].repeat` | `<repeat direction times>` | `\|:`, `:\|` |
| `barlines[].ending` | `<ending number type>` | `[1`, `[2`, `[1,2` |
| `note.pitches[]` `step`, `octave` | `<pitch><step>`, `<octave>` | note letter with `,` / `'` (C4 = `C`, C5 = `c`) |
| `note.pitches[].alter` | `<pitch><alter>` | `^`, `_`, `=` relative to the key signature |
| `note.pitches[].ties` | `<tie type>` | `-` after the note |
| several `note.pitches` | `<chord/>` notes | `[CEG]` |
| `duration`, `type`, `dots` | `<duration>`/`<divisions>`, `<type>`, `<dot/>` | length multiple of `L:` (e.g. `3/2`, `>` broken rhythm) |
| `time_modification`, `tuplets[]` | `<time-modification>`, `<notations><tuplet>` | `(3` / `(p:q:r` |
| `grace` | `<grace slash>` | `{g}`, `{/g}` (acciaccatura) |
| `rest`, `measure_rest` | `<rest>`, `<rest measure="yes">` | `z`, `Z` |
| `voice`, `staff` | `<voice>`, `<staff>` | `V:` per voice or staff, `&` overlay |
| `note.lyrics[]` by `number` | `<lyric number>` | one `w:` line per verse under the music line |
| `lyrics[].syllabic`, `extend` | `<syllabic>`, `<extend>` | `-` between syllables, `_` for a melisma, `*` to skip a note |
| `harmony` `root`, `kind_value`/`kind_text`, `bass`, `degrees` | `<harmony><root>`, `<kind text>`, `<bass>`, `<degree>` | `"Am7"`, `"D/F#"` chord symbol before the note |
| `tempo` `bpm`, `beat_unit`, `per_minute`, `words` | `<sound tempo>`, `<metronome>`, `<words>` | `Q:1/4=96 "Moderato"` |
| `clef` | `<clef><sign><line><clef-octave-change>` | `clef=treble`, `bass`, `alto`, `treble-8` in `K:`/`V:` |
| `key` `fifths`, `mode` | `<key><fifths><mode>` | `K:G`, `K:Em` |
| `time` `beats`, `beat_type`, `symbol` | `<time><beats><beat-type>`, `symbol` | `M:3/4`, `M:C`, `M:C\|` |

## Warnings

The converter adds these run-document warnings (see the code table in [json-schema.md](json-schema.md)). Each warning is reported once per construct with a count and the first part and measure where it occurred, so a large score cannot flood the list.

| Code | When |
| --- | --- |
| `score_unsupported_element` | The source uses something the model has no field for, for example slurs, articulations, fermatas, dynamics, ornaments, cue or unpitched notes, `<harmony>` without `<root>` (Roman numerals, functions), non-traditional keys, `<senza-misura>`, composite time signatures (only the first `<beats>`/`<beat-type>` pair is kept), metric modulation in `<metronome>`, `<words>` without a tempo, `<transpose>`, `<measure-style>`, `<figured-bass>`, lyrics on rests, `<end-line>`/`<humming>` in lyrics, and any unknown element. |
| `score_invalid_value` | A value is malformed or out of range (a step other than A–G, an octave outside 0–9, non-numeric numbers, `<fifths>` outside −11..11, a `<backup>` or `<forward>` that is not positive, decimal text longer than 32 characters). The field becomes `null`, or the element is left out when it cannot exist without that value (a note without a valid pitch keeps its time but is not listed). |
| `score_divisions_missing` | A duration or offset appears before any `<divisions>`. |
| `score_timing_inconsistent` | A `<backup>` or `<offset>` points before the start of the measure. |

### Ignored elements

These are skipped without a warning because they describe layout or playback rather than the music (`omr_lab.score.IGNORED_ELEMENTS`):

- in `<note>`: `<stem>`, `<beam>`, `<notehead>`, `<notehead-text>`, `<accidental>` (the printed accidental; the pitch comes from `<alter>`), `<instrument>`, `<play>`, `<listen>`;
- in `<notations>`: `<tied>` (the sounding `<tie>` is recorded instead);
- in `<attributes>`: `<staves>` (every event carries its own staff number), `<instruments>`;
- in `<measure>`: `<print>`;
- in `<direction>`: `<voice>`, `<listening>`; in `<harmony>`: `<frame>` (fretboard diagram);
- everywhere: `<footnote>`, `<level>`, all attributes not named in this document (positions, colors, fonts, `print-object`), and `<sound>` playback attributes such as `dynamics` or `pizzicato`. The `<sound>` navigation attributes (`segno`, `dalsegno`, `coda`, `tocoda`, `dacapo`, `fine`, `forward-repeat`) change the playing order, so they produce `score_unsupported_element`. `<time-modification>` `<normal-type>`/`<normal-dot>` are also skipped.

## Limitations

- Only `score-partwise` MusicXML is converted (the loader rejects `score-timewise`).
- Notes, rests and other events are not grouped into ABC lines or systems; `<print new-system>` is ignored.
- Key, time and clef changes are events in the measure where they occur; multi-staff parts keep the staff number on each event, not a staff list.
- The model does not check that a measure's durations add up to its time signature.
