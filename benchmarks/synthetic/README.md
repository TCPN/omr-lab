# Synthetic corpus specification

[`corpus.json`](corpus.json) is the specification for `omr-lab synthesize` ([docs/synthetic-corpus.md](../../docs/synthetic-corpus.md)): four lead sheets, each rendered clean and with three degradation recipes (a simulated scan, a simulated phone photo, and a phone photo of a folded song book lying on a table), 16 samples in all.

```bash
uv sync --extra synthetic
uv run omr-lab synthesize benchmarks/synthetic/corpus.json -o benchmarks/synthetic/generated/v1
```

Generated corpora go in `generated/`, which Git ignores.

## Sources

The sources resemble the target songs: one melody line in treble clef, chord symbols above the staff and lyrics below, sometimes several verses per line.

| Id | File | Content |
| --- | --- | --- |
| `twinkle` | [`../../tests/fixtures/e2e/twinkle.musicxml`](../../tests/fixtures/e2e/README.md) | The end-to-end fixture: C major, 4/4, 12 measures, one English verse. |
| `ode-to-joy` | [`sources/ode-to-joy.musicxml`](sources/ode-to-joy.musicxml) | D major, 4/4, 16 measures, dotted rhythms and beamed eighths, one German verse with umlauts. |
| `amazing-grace` | [`sources/amazing-grace.musicxml`](sources/amazing-grace.musicxml) | G major, 3/4 with a one-beat pickup, 17 measures, a tie, minor and seventh chords, two English verses. |
| `xiao-yu-di-da` | [`sources/xiao-yu-di-da.musicxml`](sources/xiao-yu-di-da.musicxml) | 小雨滴答: C major, 4/4, 8 measures, two verses of Chinese lyrics, one character per note. |

## Provenance and license

- **Music and words:** *Ode to Joy* is Ludwig van Beethoven's melody (1824) with the first stanza of Friedrich Schiller's *An die Freude* (1785). *Amazing Grace* is the tune *New Britain* (1829) with John Newton's first two verses (1779). 小雨滴答 uses the traditional tune *Frère Jacques*. All are in the public domain.
- **Written for omr-lab:** the chord symbols, the Chinese lyrics of 小雨滴答, and all encodings. They are dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/), like the other synthetic fixtures. No existing edition, scan or lyric translation was copied.
- **Images** made from these sources are CC0 1.0 too. The rendering tools and their licenses are listed in [docs/synthetic-corpus.md](../../docs/synthetic-corpus.md#rendering-dependencies); the music font is Leipzig (SIL OFL 1.1), and text fonts come from the generating machine (Liberation Serif, SIL OFL 1.1; for Chinese, Noto CJK, SIL OFL 1.1, or WenQuanYi Zen Hei, GPL-2.0 with a font embedding exception). Each sample's manifest entry records the exact versions and font.

## Changing a source

A unit test loads `corpus.json` and checks each source with the MusicXML loader. After changing a source, look at a clean rendering before committing; the ground truth must be what the image shows.
