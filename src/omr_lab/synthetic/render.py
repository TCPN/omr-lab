"""Render one MusicXML page to a grayscale image with Verovio and cairosvg.

The layout matches scripts/render_e2e_sample.py: A4 at 300 dpi, 2480 px wide, the page
height trimmed to the music. Verovio sets all text (title, chord symbols, lyrics) in
one font family on the SVG root; it is replaced with the chosen text font, because
cairosvg draws text with that one font and has no per-glyph fallback, so Times would
leave Chinese lyrics blank.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import cairosvg
import numpy
import PIL
import verovio
from PIL import Image

from omr_lab.engines.base import run_command

# Verovio's page size and margins are in tenths of a millimetre.
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
DPI = 300
_VEROVIO_FONT_ATTRIBUTE = 'font-family="Times, serif"'
_FC_MATCH_TIMEOUT_SECONDS = 30.0


class RenderError(RuntimeError):
    """A source score cannot be rendered (not loadable, or more than one page)."""


class TextFontUnavailableError(RuntimeError):
    """None of a source's text fonts is installed."""


@dataclass(frozen=True, slots=True)
class TextFont:
    family: str
    verified: bool
    """False when fontconfig's fc-match is not available to confirm the font is installed."""


def renderer_description(font: TextFont) -> str:
    """The tools and font that made an image, for the manifest's ``renderer`` field."""
    unverified = " (not checked: fc-match unavailable)" if not font.verified else ""
    return (
        f"Verovio {verovio.toolkit().getVersion()}, cairosvg {version('cairosvg')}, "
        f"Pillow {PIL.__version__}, NumPy {numpy.__version__}; "
        f"text font {font.family}{unverified}"
    )


def choose_text_font(candidates: tuple[str, ...]) -> TextFont:
    """Return the first candidate fontconfig has installed under that exact family name."""
    fc_match = shutil.which("fc-match")
    if fc_match is None:
        return TextFont(family=candidates[0], verified=False)
    for family in candidates:
        result = run_command(
            [fc_match, "--format=%{family}", family], timeout_seconds=_FC_MATCH_TIMEOUT_SECONDS
        )
        if not result.succeeded:
            raise TextFontUnavailableError(
                f"fc-match failed for {family!r}: {result.error or result.stderr.strip()}"
            )
        # fontconfig lists a font's names in several languages, comma-separated, and
        # falls back to another font when the family is missing.
        names = {name.strip().casefold() for name in result.stdout.split(",")}
        if family.casefold() in names:
            return TextFont(family=family, verified=True)
    raise TextFontUnavailableError(
        f"None of the text fonts {', '.join(candidates)} is installed. Install one of them "
        "(for example `sudo apt install fonts-liberation`, or fonts-noto-cjk for Chinese "
        "lyrics) or list an installed font in the specification's text_fonts."
    )


def render_page(musicxml: Path, font: TextFont) -> Image.Image:
    """Render ``musicxml`` (one page) to an 8-bit grayscale image on white."""
    toolkit = verovio.toolkit()
    toolkit.setOptions(VEROVIO_OPTIONS)
    if not toolkit.loadData(musicxml.read_text(encoding="utf-8")):
        raise RenderError(f"Verovio could not load {musicxml.name}.")
    pages = toolkit.getPageCount()
    if pages != 1:
        raise RenderError(
            f"{musicxml.name} renders to {pages} pages; a synthetic source must fit on one."
        )
    svg = toolkit.renderToSVG(1)
    if _VEROVIO_FONT_ATTRIBUTE not in svg:
        raise RenderError(
            f"Verovio {toolkit.getVersion()} no longer writes {_VEROVIO_FONT_ATTRIBUTE}; "
            "the text font cannot be set. Update omr_lab.synthetic.render."
        )
    svg = svg.replace(_VEROVIO_FONT_ATTRIBUTE, f'font-family="{font.family}"', 1)
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"), output_width=OUTPUT_WIDTH_PX, background_color="white"
    )
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("L")
