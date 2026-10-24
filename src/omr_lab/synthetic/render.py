"""Render one MusicXML page to a grayscale image with Verovio and cairosvg.

The layout matches scripts/render_e2e_sample.py: A4 at 300 dpi, 2480 px wide, the page
height trimmed to the music. Verovio sets all text (title, chord symbols, lyrics) in
one font family on the SVG root; it is replaced with the chosen text font, because
cairosvg draws text with that one font and has no per-glyph fallback, so Times would
leave Chinese lyrics blank. The chosen font must therefore be installed and contain
every character of the score's text, which fontconfig checks before rasterizing.

Verovio also sets music symbols inside text (chord-symbol accidentals, metronome notes)
in its music font, Leipzig, on their own ``<tspan font-family="Leipzig">``. cairosvg
ignores the font that Verovio embeds in the SVG's CSS and would draw those symbols as
placeholder boxes, so the same font is extracted from Verovio's data and registered
with fontconfig for this process.
"""

from __future__ import annotations

import atexit
import base64
import ctypes
import ctypes.util
import functools
import io
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
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
_VEROVIO_FONT_FAMILY = "Times, serif"
_VEROVIO_FONT_ATTRIBUTE = f'font-family="{_VEROVIO_FONT_FAMILY}"'
_FONTCONFIG_TIMEOUT_SECONDS = 30.0
MUSIC_FONT = "Leipzig"
"""Verovio's default music font, also used for music symbols inside text."""
_FONT_FAMILY_ATTRIBUTE = re.compile(r'font-family="([^"]*)"')


class RenderError(RuntimeError):
    """A source score cannot be rendered (not loadable, or more than one page)."""


class TextFontUnavailableError(RuntimeError):
    """No text font of a source is installed and has every character of its text, or
    the music font cannot be made available to cairosvg."""


@dataclass(frozen=True, slots=True)
class TextFont:
    family: str
    """A family fontconfig has installed under exactly this name, with every character
    of the text it is used for."""


def renderer_description(font: TextFont) -> str:
    """The tools and font that made an image, for the manifest's ``renderer`` field."""
    return (
        f"Verovio {verovio.toolkit().getVersion()}, cairosvg {version('cairosvg')}, "
        f"Pillow {PIL.__version__}, NumPy {numpy.__version__}; "
        f"text font {font.family}"
    )


def choose_text_font(
    candidates: tuple[str, ...], characters: frozenset[str] = frozenset()
) -> TextFont:
    """Return the first candidate that fontconfig has installed under that exact family
    name and that has a glyph for each of ``characters``.

    An unchecked font is never used: cairosvg would silently draw text in a missing
    font, or characters the font lacks, as blanks, while the ground truth still has
    every lyric.
    """
    fc_match, fc_list = shutil.which("fc-match"), shutil.which("fc-list")
    if fc_match is None or fc_list is None:
        raise TextFontUnavailableError(
            "fc-match and fc-list (from fontconfig) are needed to check that the text "
            f"fonts {', '.join(candidates)} are installed. Install them, for example with "
            "`sudo apt install fontconfig`."
        )
    lacking: dict[str, list[str]] = {}
    for family in candidates:
        if not _is_installed(fc_match, family):
            continue
        if _covers(fc_list, family, characters):
            return TextFont(family=family)
        lacking[family] = [c for c in sorted(characters) if not _covers(fc_list, family, {c})]
    if lacking:
        details = "; ".join(
            f"{family} lacks {_describe(missing)}" for family, missing in lacking.items()
        )
        raise TextFontUnavailableError(
            f"No installed text font has every character of the score's text ({details}). "
            "List a font that has them in text_fonts (for example Noto Sans CJK TC from "
            "fonts-noto-cjk for Chinese lyrics)."
        )
    raise TextFontUnavailableError(
        f"None of the text fonts {', '.join(candidates)} is installed. Install one of them "
        "(for example `sudo apt install fonts-liberation`, or fonts-noto-cjk for Chinese "
        "lyrics) or list an installed font in the specification's text_fonts."
    )


def _is_installed(fc_match: str, family: str) -> bool:
    result = _fontconfig(fc_match, "--format=%{family}", _fontconfig_literal(family))
    # fontconfig lists a font's names in several languages, comma-separated, and
    # falls back to another font when the family is missing.
    return family.casefold() in {name.strip().casefold() for name in result.split(",")}


def _covers(fc_list: str, family: str, characters: frozenset[str] | set[str]) -> bool:
    """Whether the family has a font with a glyph for every character."""
    if not characters:
        return True
    charset = " ".join(f"{ord(c):x}" for c in sorted(characters))
    pattern = f":family={_fontconfig_literal(family)}:charset={charset}"
    # fc-list prints the fonts that match the whole pattern, and nothing otherwise.
    return bool(_fontconfig(fc_list, "--format=%{family}\n", pattern).strip())


def _fontconfig(program: str, *args: str) -> str:
    result = run_command([program, *args], timeout_seconds=_FONTCONFIG_TIMEOUT_SECONDS)
    if not result.succeeded:
        raise TextFontUnavailableError(
            f"{program} {' '.join(args)} failed: {result.error or result.stderr.strip()}"
        )
    return result.stdout


def _describe(characters: list[str], limit: int = 10) -> str:
    shown = ", ".join(f"{c} (U+{ord(c):04X})" for c in characters[:limit])
    more = len(characters) - limit
    return shown + (f" and {more} more" if more > 0 else "")


def text_characters(svg: str) -> frozenset[str]:
    """The characters the text font draws (title, chord symbols, lyrics).

    Text inside an element that sets another font family than the one the text font
    replaces, such as Verovio's music-font tspans, is left out: the text font never
    draws it.
    """
    characters: set[str] = set()

    def collect(element: ET.Element) -> None:
        if element.get("font-family", _VEROVIO_FONT_FAMILY) != _VEROVIO_FONT_FAMILY:
            return
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in ("text", "tspan"):
            characters.update(element.text or "")
        for child in element:
            collect(child)
            if tag in ("text", "tspan"):
                characters.update(child.tail or "")

    collect(ET.fromstring(svg))
    return frozenset(c for c in characters if not c.isspace())


@functools.cache
def register_music_font() -> Path:
    """Make Verovio's music font available to cairosvg through fontconfig.

    The font is the WOFF2 file that Verovio embeds in its SVG output, taken from
    Verovio's data directory and added to fontconfig's application fonts for this
    process. Raises TextFontUnavailableError when that is not possible.
    """
    css = Path(verovio.__file__).parent / "data" / f"{MUSIC_FONT}.css"
    try:
        match = re.search(r"base64,([A-Za-z0-9+/=]+)", css.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TextFontUnavailableError(f"Cannot read Verovio's {MUSIC_FONT} font: {exc}") from exc
    if match is None:
        raise TextFontUnavailableError(
            f"{css} has no embedded {MUSIC_FONT} font; update omr_lab.synthetic.render."
        )
    library = ctypes.util.find_library("fontconfig")
    if library is None:
        raise TextFontUnavailableError(
            "The fontconfig library is needed to give cairosvg Verovio's music font. "
            "Install it, for example with `sudo apt install fontconfig`."
        )
    # Kept for the life of the process, as fontconfig reads the file when text is drawn.
    directory = Path(tempfile.mkdtemp(prefix="omr-lab-fonts-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    path = directory / f"{MUSIC_FONT}.woff2"
    path.write_bytes(base64.b64decode(match.group(1)))
    add_file = ctypes.CDLL(library).FcConfigAppFontAddFile
    add_file.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    add_file.restype = ctypes.c_int
    if not add_file(None, bytes(path)):
        raise TextFontUnavailableError(
            f"fontconfig could not load Verovio's {MUSIC_FONT} font from {path}."
        )
    return path


def _fontconfig_literal(family: str) -> str:
    """Escape a family name for fc-match, which reads its argument as a pattern:
    ``-`` starts a size, ``:`` a property, and ``\\`` escapes."""
    return re.sub(r"([\\:-])", r"\\\1", family)


def render_svg(musicxml: str, name: str) -> str:
    """Lay out the MusicXML text (one page) as SVG; ``name`` is used in error messages."""
    toolkit = verovio.toolkit()
    toolkit.setOptions(VEROVIO_OPTIONS)
    if not toolkit.loadData(musicxml):
        raise RenderError(f"Verovio could not load {name}.")
    pages = toolkit.getPageCount()
    if pages != 1:
        raise RenderError(f"{name} renders to {pages} pages; a synthetic source must fit on one.")
    svg = toolkit.renderToSVG(1)
    if _VEROVIO_FONT_ATTRIBUTE not in svg:
        raise RenderError(
            f"Verovio {toolkit.getVersion()} no longer writes {_VEROVIO_FONT_ATTRIBUTE}; "
            "the text font cannot be set. Update omr_lab.synthetic.render."
        )
    others = set(_FONT_FAMILY_ATTRIBUTE.findall(svg)) - {_VEROVIO_FONT_FAMILY, MUSIC_FONT}
    if others:
        raise RenderError(
            f"{name} uses fonts {', '.join(sorted(others))}, which cairosvg cannot draw; "
            f"only Verovio's text font and {MUSIC_FONT} are supported."
        )
    return svg


def rasterize(svg: str, font: TextFont) -> Image.Image:
    """Draw the SVG in the text font as an 8-bit grayscale image on white.

    Call register_music_font first, or music symbols inside text come out as boxes.
    """
    svg = svg.replace(_VEROVIO_FONT_ATTRIBUTE, f'font-family="{font.family}"', 1)
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"), output_width=OUTPUT_WIDTH_PX, background_color="white"
    )
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("L")
