# Correctness Metrics

`omr-lab benchmark` measures how well each recognized score matches the sample's ground truth (issue #24, OMR-023). The metrics work on the [score model](score-model.md): the ground-truth MusicXML is converted with the same converter as the engine's output, so formatting differences between MusicXML writers do not count. The code is `omr_lab.metrics` (`compare_scores` for one sample, `summarize` for totals). The results are written as the `correctness` field of each report sample and `summary.correctness` ([benchmark.md](benchmark.md#report)).

The metrics are about the music that a lead sheet needs: key and time signatures, notes (pitch and duration), measures, chord symbols and lyrics. They do not judge layout, beaming, stems, slurs, dynamics or anything else the score model leaves out.

## Which samples are measured

- A sample is measured when it has a `ground_truth` and its run **succeeded or failed**.
- A failed run, or a run whose MusicXML could not be converted to a score, is compared as a **missing prediction** (`prediction: "missing"`). Everything in the ground truth then counts as missed, and its key and time signatures are never `correct`, so a failed run lowers the totals instead of disappearing from them.
- Samples that were excluded or not run, and samples without a ground truth, have `correctness: null`.
- `correctness_problem` says when a sample with a ground truth could not be measured normally:
  - `ground_truth_invalid`: the ground truth is not loadable MusicXML. `correctness` is `null`, and the sample is left out of the totals.
  - `comparison_too_large`: an alignment would need more than 4,000,000 cells, which is roughly 2,000 × 2,000 notes. Lead sheets are far below that, so this means the engine invented far too much. `correctness` is then computed without alignment: every ground-truth item counts as missed and every predicted item as extra. The sample still lowers the totals.
- A ground truth that converts with warnings is still measured, and `ground_truth_warning_codes` lists the warnings. What the converter dropped or left unknown (a `null` duration, for example) never matches, so check these before blaming the engine.

## What is compared

- **First part only.** Both scores are reduced to their first part, and `parts` records how many parts each score has. The target songs have one melody part; multi-part scores are a limitation.
- **Notes and rests in reading order.** Measures are taken in order, and events within a measure in source order. All voices and staves of the part go into one sequence.
  - A chord is one note whose pitch is the set of its pitches.
  - Grace notes are left out, because they take no time. Their lyrics still count in the lyric metrics.
  - Hidden rests (`print-object="no"`) are left out, because they are not printed.
- **Pitches as spelled.** A pitch is its step, alteration and octave. F♯4 and G♭4 are different, and so is F4 when the score says F♯4. This is deliberate: OMR reads what is printed, and song_helper stores the spelling.
- **Durations** are the exact sounding lengths in whole notes (`"3/8"` for a dotted quarter). Engine-specific `<divisions>` do not matter.

## Alignment

Recognized scores lose, add and change notes. To avoid shifting every later note after one mistake, the two sequences are aligned before counting. The alignment is the minimum-cost edit alignment (Levenshtein) with these costs:

| Operation | Cost |
| --- | --- |
| Ground-truth item missed (deletion) | 1 |
| Extra predicted item (insertion) | 1 |
| Note against note | 0 if pitch and duration both match, 0.5 if one of them matches, 1 if neither does |
| Rest against rest | 0 if the duration matches, 0.5 otherwise |
| Note against rest | 1 |

When several alignments have the same cost, a substitution is preferred, then a missed ground-truth item, so the result is deterministic. Measures, chord symbols, syllables and characters are aligned the same way, with cost 0 for equal items and 1 otherwise.

## Metrics

| Field | Definition |
| --- | --- |
| `key_signatures` | The key signatures (`fifths`) of the part in order, with consecutive repeats removed. When the first measure has no key event, the score starts in 0 fifths, because no key signature means no sharps or flats. (Engines leave out an empty `<key>`; C major must still match.) `correct` is true when the two lists are equal. `mode` is not compared. |
| `time_signatures` | The same for `beats/beat_type`, for example `"3/4"`. A missing time signature is not assumed to be anything. |
| `measures` | The measure sequences are aligned. A measure is `exact` when its notes and rests (pitches and durations, in order) are identical. `compared = max(ground_truth, predicted)` and `accuracy = exact / compared`, so extra measures count as errors too. |
| `notes.pitch`, `notes.duration`, `notes.pitch_and_duration` | Among aligned note pairs, the ones whose pitch, duration, or both match. `precision = matched / predicted notes`, `recall = matched / ground-truth notes`, `f1 = 2 × matched / (ground_truth + predicted)`. |
| `notes.rests` | The same for rests whose duration matches. |
| `chord_symbols` | Harmony events in order; a match needs the same root, `chord_kind`, bass and degrees (in any order). `chord_kind_text` (how the kind is printed, for example `"m7"` or `"-7"`) is not compared. Precision, recall and F1 as for notes. |
| `lyrics.verses[]` | One entry per ground-truth lyric `number` (verse), compared with the predicted lyrics of the same number. Syllables are the lyric texts of the notes in order, NFC-normalized and stripped; extender-only lyrics (no text) are skipped. `syllable_errors` is the edit distance between the syllable lists, and `character_errors` is the edit distance between the characters of the joined syllables. The error rates divide by the ground-truth length, so they are 1.0 for a verse that was not recognized at all. They can exceed 1 when the prediction has many extra syllables. |
| `lyrics.extra_predicted_verses[]` | One entry per predicted lyric number that the ground truth does not have, with the same fields as `verses[]`, compared with an empty ground truth: every syllable and character it has is an error, and its own rates are `null`. A verse recognized under the wrong number counts as missed and also appears here. |

Rates are rounded to 4 decimals. A rate is `null` when its denominator is 0; for example, precision is `null` when nothing was predicted. When a sample's ground truth has no lyrics but the prediction has some, the lyric error rate is `null`, and report.md shows the invented characters instead (`– (+N chars)`).

### Totals

`summary.correctness` adds up the counts of all measured samples and computes the rates from the sums (micro-averaging), so a long song weighs more than a short one. Lyric totals add the extra predicted verses' errors, so invented lyrics raise the error rates; `lyrics.verses` and `lyrics.extra_predicted_verses` count the verses of each kind. Measure accuracy divides by the sum of `compared`, so extra measures in one sample do not cancel missing measures in another. `key_signatures_correct` and `time_signatures_correct` count samples. `predictions_missing` counts failed runs and runs without a score.

## Worked example

Ground truth: one measure in G major, 4/4, with a chord symbol and one verse.

```
K: 1 fifth   M: 4/4   "C"   C4 (1/4) 小 · D4 (1/4) 雨 · E4 (1/4) 滴 · F♯4 (1/4) 答
```

The engine read the key signature, but missed the D, read F♯ as F (natural), and read the lyric 雨 as 水 under the wrong note. Its prediction:

```
K: 1 fifth   M: 4/4   (no chord)   C4 (1/4) 小 · E4 (1/4) 水 · F4 (1/4) 答
```

1. **Alignment.**
   - C4 matches C4 (cost 0).
   - D4 has no partner and is missed (cost 1).
   - E4 matches E4 (cost 0).
   - F♯4 is aligned with F4. The duration matches but the pitch does not, so the cost is 0.5.
2. **Notes.** The ground truth has 4 notes and the prediction 3.
   - Pitch: C and E match, so 2 matched. Precision is 2/3 = 0.6667, recall 2/4 = 0.5, and F1 = 2 × 2 / (4 + 3) = 0.5714.
   - Duration: all 3 aligned notes match, so 3 matched. Precision is 1.0, recall 0.75, and F1 = 6/7 = 0.8571.
   - Pitch and duration together: 2 matched, the same as pitch.
3. **Measures.** The single measure differs, so `exact` is 0 and accuracy is 0/1 = 0.
4. **Key and time signatures.** Ground truth `[1]` against prediction `[1]` is correct. `["4/4"]` against `["4/4"]` is correct.
5. **Chord symbols.** The ground truth has 1 chord and the prediction 0. That gives recall 0, precision `null` (nothing predicted) and F1 0.
6. **Lyrics, verse "1".**
   - Syllables: 小 雨 滴 答 against 小 水 答. The best alignment substitutes 雨 → 水 and deletes 滴, so `syllable_errors` is 2 and the syllable error rate is 2/4 = 0.5.
   - Characters: the same here, because each syllable is one character. `character_errors` is 2 and the character error rate is 0.5.

The unit tests in `tests/unit/test_metrics.py` build pairs like this one by hand and check each number.

## Choice of implementation

These tools were looked at before writing this module:

- [musicdiff](https://pypi.org/project/musicdiff/) (MIT) computes an edit distance between two scores, including OMR-NED. It is built on music21 and reports one combined distance, not separate measures for pitch, duration, chords and each verse's lyrics.
- [music21](https://pypi.org/project/music21/) (BSD-3-Clause) is a large toolkit with its own MusicXML parser. Using it would add a second, different reading of the same files next to the score model.
- [MV2H](https://github.com/apmcleod/MV2H) is a Java tool for multi-pitch, voice, meter, value and harmony. It needs its own input format and does not cover lyrics or chord symbols as printed.
- [mir_eval](https://pypi.org/project/mir-eval/) (MIT) targets audio transcription (onset times in seconds), not notation.

The score model already holds exactly the fields to compare, and the metrics need no more than an edit-distance alignment. So this module adds no dependency. musicdiff's OMR-NED can be added later as a second, comparable number if needed.

## Limitations

- Only the first part is compared, and all its voices form one sequence. Piano scores and multi-voice parts are not measured meaningfully.
- Notes are compared in reading order, not by onset time. A note in the right place but in the wrong measure still matches if the order is the same, and the measure metric shows the misplacement.
- Repeats, endings and navigation are not expanded. Barlines are not compared.
- Ties, tuplet brackets, beams, clefs, tempo and dynamics are not compared. Durations already reflect tuplets.
- Lyrics are matched by verse number. An engine that numbers verses differently (issue #28) scores 1.0 error on the verse, and its text is counted again as an extra predicted verse, so the error rate is about 2.0.
- Chord symbols are compared as encoded. Two encodings of the same chord (for example `dominant` versus `major` with an added minor seventh) do not match.
- A metric is only as good as the ground truth. Synthetic images (issue #23) have exact ground truth; real photos need a hand-checked MusicXML.
