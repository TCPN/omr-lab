"""Error and warning types shared by the MusicXML loader modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ErrorCode = Literal[
    "musicxml_missing",
    "musicxml_unreadable",
    "musicxml_too_large",
    "musicxml_invalid",
    "musicxml_unsupported",
]

WarningCode = Literal[
    "musicxml_unknown_element",
    "musicxml_unresolved_entity",
    "musicxml_processing_instructions_dropped",
    "musicxml_xlink_namespace_assumed",
    "musicxml_part_missing",
    "musicxml_part_undeclared",
    "musicxml_part_without_id",
    "musicxml_duplicate_part",
    "musicxml_mxl_container_missing",
    "musicxml_mxl_container_invalid",
]


class MusicXmlError(Exception):
    """A MusicXML artifact could not be loaded.

    ``code`` is a stable snake_case identifier suitable for the run document's
    ``error.code``:

    - ``musicxml_missing``: the path does not exist or is not a file.
    - ``musicxml_unreadable``: the file exists but could not be read (permissions,
      I/O error).
    - ``musicxml_too_large``: the file, an archive entry, the number of ``.mxl``
      entries, or the number of XML elements exceeds a limit. Raise ``max_bytes`` or
      ``max_elements``, or reject the input.
    - ``musicxml_invalid``: not well-formed XML, not a MusicXML score, a corrupt or
      empty ``.mxl`` archive, or required structure (``part-list``, ``part``) missing.
    - ``musicxml_unsupported``: well-formed but uses something this loader does not
      handle (``score-timewise``, ``opus`` documents, DTD entity declarations).

    ``line``/``column`` (1-based) are set for XML syntax errors when known;
    ``member`` names the archive entry for ``.mxl`` input.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        path: Path | None = None,
        member: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.member = member
        self.line = line
        self.column = column


@dataclass(frozen=True)
class MusicXmlWarning:
    """A non-fatal problem; same ``code``/``message`` shape as run-document warnings."""

    code: WarningCode
    message: str


_LISTING_LIMIT = 5


def _listing(items: list[str]) -> str:
    """Join the first few items for a message, noting how many were left out."""
    shown = ", ".join(items[:_LISTING_LIMIT])
    hidden = len(items) - _LISTING_LIMIT
    return f"{shown}, and {hidden} more" if hidden > 0 else shown
