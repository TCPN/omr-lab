# Synthetic MusicXML fixtures

All files in this directory were written by hand for omr-lab's tests. They are original,
contain no copyrighted music, and are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). The "music" in them is
meaningless test data, not a transcription of any work.

| File | Purpose |
| --- | --- |
| `valid-partwise.musicxml` | Valid `score-partwise` 4.0 with DOCTYPE, work/movement titles, creators, rights, encoding, two parts (one with an empty name), harmony and two lyric verses. |
| `minimal-partwise.xml` | Smallest accepted document: no `version`, no identification. |
| `malformed.musicxml` | Mismatched closing tag (`</nte>`) on line 8. |
| `wrong-root.xml` | Well-formed XML whose root is not a MusicXML score. |
| `timewise.musicxml` | `score-timewise` document (reported as unsupported). |
| `unknown-elements.musicxml` | Unknown top-level element, a declared part with no content, and an undeclared part. |
| `score-features.musicxml` | One melody part with the constructs of the score model: pickup measure, key/time/clef, tempo, chord symbols with bass and degree, two lyric verses on the same notes (Chinese text, `begin`/`end` syllables, a tied melisma), dotted notes, a grace note, a triplet, a chord, a second voice after `<backup>`, repeats and first/second endings, plus a slur and a dynamic that the model reports as unsupported. |
| `score-features.score.json` | Expected `score` object for `score-features.musicxml` (`source.artifact` `"musicxml"`), produced by the converter and checked by hand against the MusicXML; see `docs/score-model.md`. |

Compressed `.mxl` archives are built from these files inside the tests (`tmp_path`), so
no binary fixtures are committed. A missing file is likewise simulated with a path under
`tmp_path`.
