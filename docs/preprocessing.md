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

Inputs: PNG, JPEG, TIFF (first page) and BMP that OpenCV can decode, 8- or 16-bit; transparency is composited onto white and 16-bit images are scaled to 8 bits. PDF inputs fail with `preprocessing_failed`: convert the pages to images first. The output is always a PNG.

## Record

The run document's `preprocessing` field ([json-schema.md](json-schema.md#preprocessing)) lists the requested steps and, per step, its parameters, whether it changed the image, what it measured and how long it took, plus the output path and its SHA-256. The output is also the `preprocessed_image` artifact. Detected corners and angles are measurements on the image, not ground truth. A benchmark report records the requested steps and, per sample, which steps changed the image ([benchmark.md](benchmark.md#report)).

If preprocessing cannot run (a PDF, an undecodable image, the extra not installed), the run fails with `preprocessing_failed` and the engine is not called. The CLI checks for the extra first and exits with 4 before creating a run directory.

## Measured effect

RESULTS

## Limitations

- `page` needs a page that is clearly brighter than a visible background. It does not find a page that fills the frame or lies on a white table; it then does nothing, which is safe but no help.
- `page` fits one quadrilateral. Curved or folded pages are only straightened at their outline; the content inside stays bent (dewarping is a separate, later transform, issue #66 and the roadmap's Phase 4).
- `deskew` measures the dominant line angle. Skew beyond `max_degrees` is not corrected and can be misjudged; perspective (lines at different angles) cannot be fixed by a rotation, so use `page` for photos.
- The parameters are fixed. They were chosen on the synthetic corpus and may need tuning for real photos; changing them would be a code change, recorded in `params`.
- No step makes up content: transforms only resample or binarize pixels that are there.
