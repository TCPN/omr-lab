# Image Preprocessing

Optional image transforms applied to a **copy** of the input before recognition (issue #58). The original file is never written; the engine reads `preprocessed/input.png` in the run directory instead. Preprocessing does not always help: measure it with the same engine and settings on the same samples, with and without it ([benchmark-protocol.md](benchmark-protocol.md#preprocessing)).

```bash
uv sync --extra preprocess                                   # OpenCV and NumPy
uv run omr-lab run photo.jpg -o runs/0001 --preprocess page
uv run omr-lab benchmark manifest.json -o reports/page --preprocess page
```

```python
from omr_lab.pipeline import run_pipeline
from omr_lab.preprocess import preprocess_image

run_pipeline("photo.jpg", "runs/0001", engine, preprocess=["page"])
preprocess_image("photo.jpg", "photo-page.png", ["page", "deskew"])  # without the pipeline
```

## Steps

Steps run in the order given; each may appear once. Every step works on a grayscale copy to measure, and the geometric steps transform the image as it is (colour survives unless `grayscale` ran). Pixels brought in from outside the image are white. A step that finds nothing to correct, or no reliable evidence, leaves the image unchanged (`applied: false`) and says why in `message`; it never guesses.

| Step | What it does | Recorded `result` | Leaves the image unchanged when |
| --- | --- | --- | --- |
| `grayscale` | Converts colour to 8-bit grayscale. | – | The image is already grayscale. |
| `page` | Finds the sheet on a darker background, maps its four corners to a rectangle (perspective and rotation correction) and crops the background. The page outline is found from edges (the background is flooded from the image border and stops at the page edge, which stays strong even where a fold or shadow darkens the page). | `corners` (top-left, top-right, bottom-right, bottom-left, in pixels of the step's input image), `contrast` (median page minus median background grey level), `output_size` | No enclosed region covers at least `min_page_fraction` of the image; the page fills the image (less than `min_background_fraction` background); or the page is less than `min_contrast` grey levels brighter than its surroundings, as in a photo of a page that fills the frame. |
| `deskew` | Rotates the image so that its long horizontal lines (staff lines) are level: the angle that makes the row-sum profile of the ink sharpest, searched coarse to fine (1°, 0.1°, 0.02°) within ±`max_degrees`. The canvas grows so nothing is cut off. | `angle_degrees` (counter-clockwise rotation applied, or measured) | The angle is below `min_degrees`, or it is at the search limit (the real skew is probably larger, so the estimate is not trusted). |
| `threshold` | Binarizes with a local Gaussian-weighted threshold, so uneven lighting does not turn whole regions black or white. | `block_size` (pixels, odd) | Never. |

Parameters are fixed per step and recorded with every run (`params`):

| Step | Parameter | Value | Meaning |
| --- | --- | --- | --- |
| `page` | `detection_size` | 1000 | Longer side, in pixels, of the downscaled copy used for detection. |
| | `min_contrast` | 60 | Grey levels the page must be brighter than its surroundings. |
| | `min_page_fraction` | 0.2 | Smallest page area as a fraction of the image. |
| | `min_background_fraction` | 0.01 | Smallest background area as a fraction of the image. |
| `deskew` | `detection_size` | 1200 | As for `page`. |
| | `max_degrees` | 15 | Search range, ± degrees. |
| | `min_degrees` | 0.1 | Smaller angles are left alone. |
| `threshold` | `block_fraction` | 1/30 | Window size as a fraction of the shorter side. |
| | `offset` | 15 | Grey levels below the local mean that count as ink. |

Inputs: PNG, JPEG, TIFF (first page) and BMP that OpenCV can decode, 8- or 16-bit; the EXIF orientation tag is applied, also to transparent images (a phone photo stored sideways with a tag comes out upright, and the PNG written afterwards has no tag), and `orientation_applied` records whether it changed the image. For a transparent image the tag is matched through the colour channels: one whose turn cannot be matched unambiguously (uniform colour with the drawing only in alpha, under a 90° or mirror tag) fails with `preprocessing_failed` (flatten it onto a background or remove the tag), and a tag that leaves symmetric colour unchanged (such as 180°) is treated as no tag; transparency is composited onto white and 16-bit images are scaled to 8 bits. PDF inputs fail with `preprocessing_failed`: convert the pages to images first. The output is always a PNG.

## Record

The run document's `preprocessing` field ([json-schema.md](json-schema.md#preprocessing)) lists the requested steps and, per step, its parameters, whether it changed the image, what it measured and how long it took, plus the output path and its SHA-256. The output is also the `preprocessed_image` artifact. Detected corners and angles are measurements on the image, not ground truth. A benchmark report records the requested steps and, per sample, which steps changed the image ([benchmark.md](benchmark.md#report)).

If preprocessing cannot run (a PDF, an undecodable image, the extra not installed), the run fails with `preprocessing_failed` and the engine is not called. The CLI checks for the extra first and exits with 4 before creating a run directory.

## Measured effect

Audiveris 5.11.0 on the synthetic corpus (`benchmarks/synthetic/corpus.json`, 4 sources × 5 conditions, seed 20261023), same engine, default options and timeout for every column; generated 2026-10-31 in a Linux container without OCR languages, so chord symbols and lyrics are not read and only notes and measures are compared. Notes are pitch-and-duration recall over the condition's 4 samples ([metrics.md](metrics.md)); "failed" counts runs that did not succeed.

| Condition | none | `page` | `page,deskew` | `deskew` | `page,deskew,threshold` |
| --- | --- | --- | --- | --- | --- |
| clean | 0.90 | 0.90 | 0.90 | 0.90 | 0.88 |
| scan (rotated 1–2°) | 0.97 | 0.97 | 0.98 | 0.98 | 0.65 |
| photo (perspective, curvature, no background) | 0.97 | 0.97 | 0.97 | 0.97 | 0.20 (1 failed) |
| phone (page on a background, fold) | 0.69 | **0.97** | 0.95 | 0.69 | 0.72 (1 failed) |
| phone-tilted (also rotated up to 8° and tilted) | 0.20 (1 failed) | **0.85** | 0.74 | 0.55 (1 failed) | 0.74 |
| all 20: note precision / recall | 0.90 / 0.75 | 0.95 / 0.93 | 0.92 / 0.91 | 0.90 / 0.82 | 0.90 / 0.64 |
| all 20: measures exactly right | 63% | 85% | 83% | 70% | 40% |

What this shows, on this small synthetic corpus and this engine only:

- `page` is the step that helps: phone photos go from 0.69 to 0.97 and tilted phone photos from 0.20 to 0.85, with no change on the other conditions (it correctly does nothing there: no background, or a page as bright as its surroundings).
- `deskew` alone gives a small gain on scans and partly rescues tilted photos, but after `page` it made two tilted photos worse (a fold shifts the dominant line angle, and the rotation adds resampling). Use `page` alone for phone photos.
- `threshold` hurts: Audiveris binarizes by itself, and a fixed local threshold breaks thin staff lines on soft or shaded images. It is kept for engines that need binary input, not recommended for Audiveris.
- One sample (amazing-grace, phone-tilted) stays poor with every combination; its fold crosses the staves, which a four-corner correction cannot straighten (issue #66, dewarping).

With 4 samples per condition, single samples move the averages a lot; treat these as a direction to confirm on more and real photos, not as a general result.

## Limitations

- `page` needs a page that is clearly brighter than a visible background. It does not find a page that fills the frame or lies on a white table; it then does nothing, which is safe but no help.
- `page` fits one quadrilateral. Curved or folded pages are only straightened at their outline; the content inside stays bent (dewarping is a separate, later transform, issue #66 and the roadmap's Phase 4).
- `deskew` measures the dominant line angle. Skew beyond `max_degrees` is not corrected and can be misjudged; perspective (lines at different angles) cannot be fixed by a rotation, so use `page` for photos.
- The parameters are fixed. They were chosen on the synthetic corpus and may need tuning for real photos; changing them would be a code change, recorded in `params`.
- No step makes up content: transforms only resample or binarize pixels that are there.
