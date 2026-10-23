"""Render the end-to-end sample score image from its ground-truth MusicXML.

This is a fixture generator, not part of the package. Its rendering libraries are
not project dependencies; run it with them added for this one command:

    uv run --with verovio==6.3.0 --with cairosvg==2.9.1 --with pillow==13.0.0 \\
        python scripts/render_e2e_sample.py

It reads tests/fixtures/e2e/twinkle.musicxml and writes tests/fixtures/e2e/twinkle.png
(one page, 8-bit grayscale, white background). See tests/fixtures/e2e/README.md.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import cairosvg
import verovio
from PIL import Image

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "e2e"
DEFAULT_SOURCE = FIXTURE_DIR / "twinkle.musicxml"
DEFAULT_OUTPUT = FIXTURE_DIR / "twinkle.png"

# A4 at 300 dpi is 2480 x 3508 px; Verovio's page size is in tenths of a millimetre.
VEROVIO_OPTIONS = {
    "pageWidth": 2100,
    "pageHeight": 2970,
    "pageMarginLeft": 150,
    "pageMarginRight": 150,
    "pageMarginTop": 150,
    "pageMarginBottom": 150,
    "scale": 50,
    "adjustPageHeight": True,
    "breaks": "auto",
    "footer": "none",
}
OUTPUT_WIDTH_PX = 2480


def render(source: Path, output: Path) -> None:
    toolkit = verovio.toolkit()
    toolkit.setOptions(VEROVIO_OPTIONS)
    if not toolkit.loadData(source.read_text(encoding="utf-8")):
        raise SystemExit(f"Verovio could not load {source}")
    if toolkit.getPageCount() != 1:
        raise SystemExit(f"Expected one page, Verovio produced {toolkit.getPageCount()}")
    png = cairosvg.svg2png(
        bytestring=toolkit.renderToSVG(1).encode("utf-8"),
        output_width=OUTPUT_WIDTH_PX,
        background_color="white",
    )
    image = Image.open(io.BytesIO(png)).convert("L")
    image.save(output, format="PNG", dpi=(300, 300), optimize=True)
    print(f"Verovio {toolkit.getVersion()}: wrote {output} ({image.width} x {image.height} px)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    render(args.source, args.output)


if __name__ == "__main__":
    main()
