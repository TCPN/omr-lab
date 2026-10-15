"""Hardened XML parsing for untrusted engine output, using only the standard library.

ElementTree's own parser cannot be configured to reject entity declarations, so this
module drives ``pyexpat`` directly and feeds an ``ElementTree.TreeBuilder``:

- Entity declarations of any kind (internal, external, parameter, unparsed) are
  rejected. This blocks external-entity (XXE) file/URL reads and entity-expansion
  ("billion laughs") attacks. MusicXML never needs entity declarations.
- Parameter-entity parsing is disabled, so the external DTD subset named in a
  ``<!DOCTYPE ... SYSTEM "http://...">`` declaration is never loaded or fetched.
- An external-entity reference handler that raises is installed as a second guard.
- References to entities that are not defined in the document (for example ``&nbsp;``
  from an unread external DTD) are skipped by expat; their names are recorded so the
  caller can warn instead of losing text silently.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.parsers import expat


class ForbiddenXmlConstructError(Exception):
    """The document uses an XML construct that is refused for safety reasons."""

    def __init__(self, message: str, line: int | None, column: int | None) -> None:
        super().__init__(message)
        self.line = line
        self.column = column


class XmlSyntaxError(Exception):
    """The document is not well-formed XML. ``line``/``column`` are 1-based."""

    def __init__(self, message: str, line: int | None, column: int | None) -> None:
        super().__init__(message)
        self.line = line
        self.column = column


@dataclass
class ParsedXml:
    """Result of :func:`parse_xml_bytes`."""

    root: ET.Element
    doctype_name: str | None = None
    doctype_public_id: str | None = None
    doctype_system_id: str | None = None
    skipped_entities: list[str] = field(default_factory=list)
    first_child_lines: dict[str, int] = field(default_factory=dict)
    """1-based start line of the first direct child of the root with each tag.

    Only the first occurrence is kept, so memory stays bounded by the number of
    distinct tags rather than the number of children."""


def _clark(name: str) -> str:
    """Turn expat's ``uri}local`` into ElementTree's ``{uri}local``."""
    return "{" + name if "}" in name else name


def parse_xml_bytes(data: bytes) -> ParsedXml:
    """Parse ``data`` into an ElementTree, refusing DTD entity declarations.

    Raises :class:`XmlSyntaxError` for malformed XML and
    :class:`ForbiddenXmlConstructError` for entity declarations.
    """
    builder = ET.TreeBuilder()
    # Namespace processing makes unbound prefixes a syntax error and yields the same
    # Clark-notation names (``{uri}local``) as ElementTree's own parser.
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    doctype: dict[str, str | None] = {}
    skipped: list[str] = []
    first_child_lines: dict[str, int] = {}
    depth = 0

    def position() -> tuple[int, int]:
        return parser.CurrentLineNumber, parser.CurrentColumnNumber + 1

    def forbid(what: str) -> None:
        line, column = position()
        raise ForbiddenXmlConstructError(
            f"{what} at line {line}, column {column} is not allowed: entity declarations "
            "and external entities are refused to prevent external-entity (XXE) and "
            "entity-expansion attacks. MusicXML does not need them.",
            line,
            column,
        )

    def start(name: str, attrs: dict[str, str]) -> None:
        nonlocal depth
        tag = _clark(name)
        builder.start(tag, {_clark(key): value for key, value in attrs.items()})
        depth += 1
        if depth == 2:
            first_child_lines.setdefault(tag, parser.CurrentLineNumber)

    def end(name: str) -> None:
        nonlocal depth
        builder.end(_clark(name))
        depth -= 1

    def start_doctype(
        name: str, system_id: str | None, public_id: str | None, _has_internal_subset: int
    ) -> None:
        doctype.update(name=name, system_id=system_id, public_id=public_id)

    def entity_decl(name: str, is_parameter: int, *_args: object) -> None:
        kind = "Parameter entity" if is_parameter else "Entity"
        forbid(f"{kind} declaration {name!r}")

    def unparsed_entity_decl(name: str, *_args: object) -> None:
        forbid(f"Unparsed entity declaration {name!r}")

    def external_entity_ref(context: str | None, *_args: object) -> int:
        forbid(f"External entity reference {context!r}")
        return 0  # pragma: no cover - forbid() always raises

    def skipped_entity(name: str, is_parameter: int) -> None:
        if not is_parameter:
            skipped.append(name)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = start_doctype
    parser.EntityDeclHandler = entity_decl
    parser.UnparsedEntityDeclHandler = unparsed_entity_decl
    parser.ExternalEntityRefHandler = external_entity_ref
    parser.SkippedEntityHandler = skipped_entity

    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        reason = expat.errors.messages.get(exc.code, str(exc))
        column = exc.offset + 1
        raise XmlSyntaxError(
            f"{reason} (line {exc.lineno}, column {column})", exc.lineno, column
        ) from exc

    root = builder.close()
    return ParsedXml(
        root=root,
        doctype_name=doctype.get("name"),
        doctype_public_id=doctype.get("public_id"),
        doctype_system_id=doctype.get("system_id"),
        skipped_entities=skipped,
        first_child_lines=first_child_lines,
    )
