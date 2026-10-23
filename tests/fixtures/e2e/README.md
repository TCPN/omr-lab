# End-to-end sample

One redistributable score for the end-to-end test (issue #10): an image to recognize and the MusicXML it was rendered from, which is the exact ground truth.

| File | Content |
| --- | --- |
| `twinkle.musicxml` | Ground truth. *Twinkle, Twinkle, Little Star*: 12 measures, one melody part in C major, 4/4, treble clef, 42 notes (quarters and halves), 22 chord symbols, one lyric verse. |
| `twinkle.png` | The score rendered from `twinkle.musicxml`: one page, 2480 × 1235 px at 300 dpi, 8-bit grayscale, white background, no degradation. |
| `manifest.json` | A benchmark manifest ([docs/benchmark.md](../../../docs/benchmark.md)): source, license, condition, renderer and SHA-256 checksums of both files. A unit test checks the checksums, so a changed file must come with an updated manifest. |

## Provenance and license

- **Music and words:** the melody is the traditional French tune *Ah ! vous dirai-je, maman* (18th century). The lyrics are the first stanza of Jane Taylor's poem *The Star* (1806), repeated for the last four measures. Both are in the public domain.
- **Chord symbols:** a plain I–IV–V harmonization written for this fixture.
- **Encoding and image:** `twinkle.musicxml` was written for omr-lab and `twinkle.png` was rendered from it. Both are dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/), like the other synthetic fixtures. No existing edition or scan was copied.
- **Rendering tools:** [Verovio](https://www.verovio.org/) 6.3.0 (LGPL-3.0) with its default Leipzig music font (SIL OFL 1.1), [cairosvg](https://cairosvg.org/) 2.9.1 (LGPL-3.0) and Pillow 13.0.0 (MIT-CMU). The text font came from the rendering machine's `Times` fallback, Liberation Serif (SIL OFL 1.1). None of these tools is a dependency of omr-lab, and their licenses place no conditions on the rendered image.

## Regenerating the image

```bash
uv run --with verovio==6.3.0 --with cairosvg==2.9.1 --with pillow==13.0.0 \
  python scripts/render_e2e_sample.py
```

The output is deterministic on one machine (the same checksum on repeated runs), but a machine with a different `Times` fallback font renders the title, chord symbols and lyrics differently. Update `image_sha256` in `manifest.json` after an intended change.

## What the sample does not cover

It is a clean digital rendering, so it says nothing about phone photos, scans, skew or page curvature (issue #23 builds a degraded corpus). The lyrics are English; the target songs have Chinese lyrics, often several verses per line.
