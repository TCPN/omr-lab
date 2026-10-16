"""Engine-independent loading and validation of MusicXML artifacts.

A successfully loaded document is well-formed, structurally plausible MusicXML. That is
not evidence that the recognized music is correct.
"""

from omr_lab.musicxml.document import (
    DEFAULT_MAX_BYTES,
    Creator,
    ErrorCode,
    MusicXmlDocument,
    MusicXmlError,
    MusicXmlMetadata,
    MusicXmlWarning,
    PartInfo,
    WarningCode,
    load_musicxml,
    parse_musicxml_bytes,
)

__all__ = [
    "DEFAULT_MAX_BYTES",
    "Creator",
    "ErrorCode",
    "MusicXmlDocument",
    "MusicXmlError",
    "MusicXmlMetadata",
    "MusicXmlWarning",
    "PartInfo",
    "WarningCode",
    "load_musicxml",
    "parse_musicxml_bytes",
]
