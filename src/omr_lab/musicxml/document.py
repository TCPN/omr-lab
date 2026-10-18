"""Load MusicXML artifacts (.musicxml/.xml and compressed .mxl) and extract metadata.

This layer checks that a file is a readable, well-formed ``score-partwise`` MusicXML
document and reports the metadata it declares. It does not interpret notes, lyrics, or
harmony (that is the score content model, issue #19) and a successful load says nothing
about whether the recognized music is correct.
"""

from __future__ import annotations

import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from omr_lab.musicxml.errors import (
    _LISTING_LIMIT,
    MusicXmlError,
    MusicXmlWarning,
    _listing,
)
from omr_lab.musicxml.metadata import MusicXmlMetadata, _extract_metadata
from omr_lab.musicxml.mxl import ZIP_MAGIC, read_mxl_score
from omr_lab.musicxml.safe_xml import (
    DEFAULT_MAX_ELEMENTS,
    XLINK_NAMESPACE,
    ForbiddenXmlConstructError,
    ParsedXml,
    TooManyElementsError,
    UnsupportedEncodingError,
    XmlSyntaxError,
    parse_xml_bytes,
)

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
"""Default size limit for a MusicXML document (after decompression for ``.mxl``)."""

# Child elements of <score-partwise> defined by the MusicXML 4.0 schema.
KNOWN_PARTWISE_CHILDREN = frozenset(
    {
        "work",
        "movement-number",
        "movement-title",
        "identification",
        "defaults",
        "credit",
        "part-list",
        "part",
    }
)


@dataclass(frozen=True)
class MusicXmlDocument:
    """A loaded MusicXML document.

    ``root`` is the parsed ``<score-partwise>`` element (the full element tree, for
    later conversion; comments and processing instructions are not kept). ``container``
    is ``"mxl"`` for compressed input, in which case ``archive_member`` is the path of
    the score inside the archive.
    """

    path: Path | None
    root: ET.Element
    metadata: MusicXmlMetadata
    container: Literal["musicxml", "mxl"]
    archive_member: str | None = None
    doctype_public_id: str | None = None
    warnings: tuple[MusicXmlWarning, ...] = field(default=())


def load_musicxml(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> MusicXmlDocument:
    """Load and validate a MusicXML file.

    Compressed MusicXML is detected by content (zip signature), so an ``.mxl`` saved
    with a ``.musicxml`` extension still loads, and a non-zip ``.mxl`` is reported as
    invalid. ``max_bytes`` limits the file size and, for ``.mxl``, the decompressed size
    of each entry read. ``max_elements`` limits the number of XML elements, which
    bounds the memory of the parsed tree.

    Raises :class:`MusicXmlError` with an actionable message on failure.
    """
    path = Path(path)
    # One stat() inside a try: on Python 3.11/3.12, exists() re-raises errors such as
    # EACCES on a parent directory, and on 3.13+ it reports them as "not found".
    try:
        mode = path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise MusicXmlError(
            "musicxml_missing",
            f"MusicXML file not found: {path}. Check that the engine wrote its output "
            "and that the path is correct.",
            path=path,
        ) from exc
    except OSError as exc:
        raise MusicXmlError(
            "musicxml_unreadable",
            f"Could not access MusicXML file {path}: {exc.strerror or exc}.",
            path=path,
        ) from exc
    if not stat.S_ISREG(mode):
        raise MusicXmlError(
            "musicxml_missing",
            f"MusicXML path is not a regular file: {path}.",
            path=path,
        )
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise MusicXmlError(
                "musicxml_too_large",
                f"MusicXML file {path} is {size} bytes, larger than the {max_bytes}-byte "
                "limit. Raise max_bytes if this file is expected.",
                path=path,
            )
        # The file can grow after stat(); never read more than max_bytes + 1.
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise MusicXmlError(
                "musicxml_too_large",
                f"MusicXML file {path} is larger than the {max_bytes}-byte limit. "
                "Raise max_bytes if this file is expected.",
                path=path,
            )
    except OSError as exc:
        raise MusicXmlError(
            "musicxml_unreadable",
            f"Could not read MusicXML file {path}: {exc.strerror or exc}.",
            path=path,
        ) from exc

    if data.startswith(ZIP_MAGIC) or path.suffix.lower() == ".mxl":
        score = read_mxl_score(data, path=path, max_bytes=max_bytes, max_elements=max_elements)
        return _parse_document(
            score.data,
            path=path,
            container="mxl",
            archive_member=score.member,
            warnings=score.warnings,
            max_elements=max_elements,
        )
    return _parse_document(data, path=path, max_elements=max_elements)


def parse_musicxml_bytes(
    data: bytes, *, path: Path | None = None, max_elements: int = DEFAULT_MAX_ELEMENTS
) -> MusicXmlDocument:
    """Parse uncompressed MusicXML bytes. ``path`` is used only in messages.

    No size limit is applied: callers passing in-memory data must bound it themselves.
    ``max_elements`` limits the number of XML elements, as in :func:`load_musicxml`.
    """
    return _parse_document(data, path=path, max_elements=max_elements)


def _parse_document(
    data: bytes,
    *,
    path: Path | None,
    container: Literal["musicxml", "mxl"] = "musicxml",
    archive_member: str | None = None,
    warnings: list[MusicXmlWarning] | None = None,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
) -> MusicXmlDocument:
    warnings = list(warnings or [])
    where = _describe(path, archive_member)
    parsed = _parse_safely(
        data, where=where, path=path, member=archive_member, max_elements=max_elements
    )
    root = parsed.root

    if root.tag == "score-timewise":
        raise MusicXmlError(
            "musicxml_unsupported",
            f"{where} is a score-timewise MusicXML document, which is not supported yet. "
            "Convert it to score-partwise (for example with the official parttime.xsl "
            "stylesheet or by re-exporting from a notation editor).",
            path=path,
            member=archive_member,
        )
    if root.tag == "opus":
        raise MusicXmlError(
            "musicxml_unsupported",
            f"{where} is a MusicXML opus (a list of linked scores), not a score. "
            "Load the individual score files instead.",
            path=path,
            member=archive_member,
        )
    if root.tag != "score-partwise":
        raise MusicXmlError(
            "musicxml_invalid",
            f"{where} is well-formed XML but not a MusicXML score: root element is "
            f"<{root.tag}>, expected <score-partwise>.",
            path=path,
            member=archive_member,
        )

    if parsed.xlink_namespace_assumed:
        warnings.append(
            MusicXmlWarning(
                "musicxml_xlink_namespace_assumed",
                f"{where} uses the xlink: prefix without declaring it. The MusicXML DTD "
                f"fixes it to {XLINK_NAMESPACE}, so that namespace was assumed.",
            )
        )
    if parsed.processing_instruction_targets:
        targets = _listing(
            [f"<?{t}?> x{n}" for t, n in parsed.processing_instruction_targets.items()]
        )
        if parsed.processing_instruction_targets_truncated:
            targets += "; further targets not recorded"
        warnings.append(
            MusicXmlWarning(
                "musicxml_processing_instructions_dropped",
                f"{where} contains {parsed.processing_instructions} processing "
                f"instruction(s) ({targets}); they are not kept in the parsed tree.",
            )
        )
    if parsed.skipped_entities:
        names = _listing([f"&{name};" for name in parsed.skipped_entities])
        if parsed.skipped_entities_truncated:
            names += "; further names not recorded"
        warnings.append(
            MusicXmlWarning(
                "musicxml_unresolved_entity",
                f"{where} has {parsed.skipped_entity_references} reference(s) to entities "
                f"that are not defined in the document ({names}); external DTDs are "
                "never loaded, so their text was omitted.",
            )
        )

    # One warning per distinct unknown tag, so a document with many repeated unknown
    # elements cannot flood the warnings (or memory).
    unknown_counts: dict[str, int] = {}
    for child in root:
        if isinstance(child.tag, str) and child.tag not in KNOWN_PARTWISE_CHILDREN:
            unknown_counts[child.tag] = unknown_counts.get(child.tag, 0) + 1
    for tag, count in list(unknown_counts.items())[:_LISTING_LIMIT]:
        line = parsed.first_child_lines.get(tag)
        times = f" ({count} times)" if count > 1 else ""
        first = "first " if count > 1 else ""
        at = f" ({first}at line {line})" if line is not None else ""
        warnings.append(
            MusicXmlWarning(
                "musicxml_unknown_element",
                f"Unknown element <{tag}> under <score-partwise>{times}{at} in {where}; "
                "it is kept in the parsed tree but not interpreted.",
            )
        )
    others = list(unknown_counts)[_LISTING_LIMIT:]
    if others:
        warnings.append(
            MusicXmlWarning(
                "musicxml_unknown_element",
                f"{len(others)} more distinct unknown elements under <score-partwise> in "
                f"{where} ({_listing([f'<{tag}>' for tag in others])}); they are kept in the "
                "parsed tree but not interpreted.",
            )
        )

    metadata = _extract_metadata(
        root, where=where, path=path, member=archive_member, warnings=warnings
    )
    return MusicXmlDocument(
        path=path,
        root=root,
        metadata=metadata,
        container=container,
        archive_member=archive_member,
        doctype_public_id=parsed.doctype_public_id,
        warnings=tuple(warnings),
    )


def _describe(path: Path | None, member: str | None) -> str:
    base = str(path) if path is not None else "MusicXML input"
    return f"{base} (archive entry {member!r})" if member else base


def _parse_safely(
    data: bytes, *, where: str, path: Path | None, member: str | None, max_elements: int
) -> ParsedXml:
    try:
        return parse_xml_bytes(data, max_elements=max_elements)
    except UnsupportedEncodingError as exc:
        raise MusicXmlError(
            "musicxml_unsupported",
            f"{where} declares a character encoding the XML parser cannot decode ({exc}). "
            "Multi-byte legacy encodings such as Big5 or GBK are not supported; re-export "
            "the file as UTF-8.",
            path=path,
            member=member,
            line=exc.line,
            column=exc.column,
        ) from exc
    except XmlSyntaxError as exc:
        raise MusicXmlError(
            "musicxml_invalid",
            f"{where} is not well-formed XML: {exc}. The engine output may be truncated "
            "or corrupt.",
            path=path,
            member=member,
            line=exc.line,
            column=exc.column,
        ) from exc
    except ForbiddenXmlConstructError as exc:
        raise MusicXmlError(
            "musicxml_unsupported",
            f"{where}: {exc}",
            path=path,
            member=member,
            line=exc.line,
            column=exc.column,
        ) from exc
    except TooManyElementsError as exc:
        raise MusicXmlError(
            "musicxml_too_large",
            f"{where}: {exc} Raise max_elements if this file is expected.",
            path=path,
            member=member,
            line=exc.line,
            column=exc.column,
        ) from exc
