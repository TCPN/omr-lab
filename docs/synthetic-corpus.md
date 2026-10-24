# Synthetic Corpus

`omr-lab synthesize` builds a benchmark set whose ground truth is known exactly (issue #23, OMR-022). It renders MusicXML scores with Verovio, applies image degradations with seeded random parameters, and writes a [benchmark manifest](benchmark.md) that records the source, license and every parameter of each image. The result runs with `omr-lab benchmark` like any other manifest.

```bash
uv sync --extra synthetic
uv run omr-lab synthesize benchmarks/synthetic/corpus.json -o benchmarks/synthetic/generated/v1
uv run omr-lab benchmark benchmarks/synthetic/generated/v1/manifest.json \
  -o benchmarks/reports/generated/synthetic-v1-audiveris --engine audiveris
```

The committed specification and source scores are in [`benchmarks/synthetic/`](../benchmarks/synthetic/README.md), with their provenance and licenses. Generated corpora are not committed: `benchmarks/synthetic/generated/` is ignored by Git, and anyone can rebuild a corpus from the specification.

## Rendering dependencies

Rendering needs the optional `synthetic` extra, which the rest of omr-lab does not use:

| Package | License | Use |
| --- | --- | --- |
| [Verovio](https://www.verovio.org/) | LGPL-3.0 | MusicXML to SVG; its default music font, Leipzig, is under the SIL OFL 1.1 |
| [cairosvg](https://cairosvg.org/) | LGPL-3.0 | SVG to PNG; needs the cairo C library (`libcairo2`) |
| [Pillow](https://python-pillow.org/) | MIT-CMU | Image handling, blur, rotation, perspective, JPEG |
| [NumPy](https://numpy.org/) | BSD-3-Clause | Random numbers, noise, curvature, lighting |

They are used as unmodified libraries. Their licenses place no conditions on the images they produce. Distributing omr-lab together with them would bring the LGPL's notice and relinking terms for Verovio and cairosvg; that is not done today.

Without the extra, `synthesize` exits with code 4 and says what to install. Checking a specification (`omr_lab.synthetic.load_corpus_spec`) needs only the base package.

## Specification

A UTF-8 JSON file validated against `src/omr_lab/schemas/synthetic-corpus-0.1.0.schema.json`. Paths are relative to the specification's directory.

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | `"0.1.0"` |
| `name` | yes | Corpus name; also the manifest's `name`. |
| `description` | no | Copied into the manifest's description. |
| `seed` | yes | Corpus seed, an integer from 0 to 2^53 − 1. `--seed` replaces it. |
| `text_fonts` | no | Default text fonts, in order of preference (default: Liberation Serif, Times New Roman). |
| `sources` | yes | Scores to render: `id`, `musicxml`, `source`, `license`, optional `work` and `text_fonts`. |
| `variants` | yes | Image conditions: `id`, `condition` (the manifest's condition text) and `degradations`, applied in order. A variant with no degradations is the clean rendering. |

Every source is rendered once and combined with every variant. Sample ids are `<source id>-<variant id>`, so ids are 1 to 30 lowercase letters, digits or `-`, and must be unique within sources and within variants.

A source must be uncompressed, UTF-8 encoded MusicXML (a byte-order mark is allowed) that the [MusicXML loader](musicxml.md) accepts and that fits on one page. The file is copied unchanged into the corpus as the ground truth of all its samples, so clean and degraded images of one source share one ground truth.

### Degradations

Each degradation has a `kind` and the parameters below, all required. A parameter is a fixed number, or `{"min": a, "max": b}` to draw it uniformly for each sample. The allowed ranges are sanity limits against typos, not claims about realistic conditions.

| Kind | Parameters | Effect |
| --- | --- | --- |
| `rotate` | `degrees` (−45 to 45) | Counter-clockwise rotation; the canvas grows so the page stays whole. |
| `perspective` | `strength` (0 to 0.25) | Each page corner moves inward by a random fraction up to `strength` of the width and height, like a photo taken at an angle. |
| `curvature` | `amplitude` (−0.1 to 0.1) | Columns shift vertically along a half sine, up to `amplitude` × height in the middle, like a page that does not lie flat; the canvas grows by that much so nothing is cut off. |
| `blur` | `radius` (0 to 20) | Gaussian blur, radius in pixels. |
| `noise` | `sigma` (0 to 128) | Gaussian noise, standard deviation in grey levels. |
| `lighting` | `strength` (0 to 0.9), `angle` (0 to 360) | Linear darkening across the page, up to `strength` darker towards `angle` degrees (0 = right, 90 = down). |
| `jpeg` | `quality` (1 to 95, rounded to an integer) | JPEG compression artifacts; the image is still saved as PNG. |

Areas uncovered by a geometric change are white. Images are 8-bit grayscale PNG, 2480 px wide at 300 dpi before rotation (A4 width), with the height trimmed to the music.

## Reproducibility

- Each sample has its own seed, derived from the corpus seed and its source and variant ids (`omr_lab.synthetic.sample_seed`). Adding a source or variant does not change the other samples. Sample seeds and noise seeds stay below 2^53, so the numbers survive JSON readers that parse them as doubles.
- All random choices are drawn first and recorded as plain numbers in the manifest, including the `corner_insets` of `perspective` and a `noise_seed` for `noise`. Applying the recorded degradations to the clean image (`omr_lab.synthetic.degradations.apply`) reproduces the degraded image, without the seed.
- The same specification, seed, package versions and fonts give byte-identical images; a unit test checks this. Other versions of Verovio, cairosvg, Pillow, NumPy or the text font can change pixels, so the manifest records the versions and the font in each sample's `renderer`, and the checksums of the files that were actually written.

## Text fonts

Verovio sets the title, chord symbols and lyrics in one font family, and cairosvg draws all text with that one font, without falling back to another font for missing characters. Chinese lyrics therefore need a CJK font, set with `text_fonts` on the source. The generator lays out each source first, collects the characters of its text, and picks the first listed font that fontconfig has installed under exactly that name (`fc-match`) and that has a glyph for every one of those characters (`fc-list` with a `charset`). It exits with code 4 when no listed font qualifies, naming the characters each installed font lacks, or when `fc-match` or `fc-list` is missing (`sudo apt install fontconfig`): a font that cannot be checked is never used, because a missing font or glyph would render the lyrics blank while the ground truth keeps them.

The text font changes the image, so corpora generated on machines with different fonts differ. The committed specification prefers Noto CJK fonts for Chinese (`sudo apt install fonts-noto-cjk`) and falls back to WenQuanYi Zen Hei.

## Output

```
<output_dir>/
  manifest.json                  benchmark manifest, schema 0.2.0
  images/<source>-<variant>.png
  ground-truth/<source>.musicxml copy of the source score
```

`<output_dir>` must be missing or empty. All sources are checked and rendered before anything is written; the manifest is written last, after it validates.

Each sample has the usual manifest fields (`source`, `license` and `work` from the source, `condition` from the variant, `renderer`) and a `synthesis` object:

| Field | Meaning |
| --- | --- |
| `corpus` | Specification name. |
| `source_id`, `variant_id` | Which source and variant made the sample. |
| `seed` | The sample's seed. |
| `degradations` | `[{"kind": ..., "params": {...}}]` in the order applied, with resolved values only; empty for a clean image. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The corpus and manifest were written. |
| 2 | Invalid command line (for example a negative `--seed`). |
| 3 | The specification cannot be read or is invalid, or a source is not valid MusicXML, is compressed, is not UTF-8, or renders to more than one page. |
| 4 | A rendering package, the cairo library, `fc-match` or `fc-list` is missing, or no listed text font is installed with every character of a source's text. |
| 6 | The output directory cannot be used, or writing failed; no manifest was written. |
| 130 | Interrupted; no manifest was written. |

## Limits

- One page per source, one part per score in the committed sources. Multi-page scores and several parts are not covered.
- The degradations are simple models. They are not a substitute for real phone photos and scans, which [benchmark-protocol.md](benchmark-protocol.md) still asks for.
- Verovio's layout differs from the engraving of printed song books; a corpus from one renderer can favour engines tuned to similar layouts.
- The corpus measures recognition against exact ground truth, but this command does not score anything. Metrics are issue #24.
