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
  caller can warn instead of losing text silently. Expat drops such references inside
  attribute values without reporting them, so start tags are scanned for them too.
"""

from __future__ import annotations

import re
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


class UnsupportedEncodingError(XmlSyntaxError):
    """The document declares an encoding expat cannot decode (e.g. Big5 or GBK)."""


@dataclass
class ParsedXml:
    """Result of :func:`parse_xml_bytes`."""

    root: ET.Element
    doctype_name: str | None = None
    doctype_public_id: str | None = None
    doctype_system_id: str | None = None
    skipped_entities: list[str] = field(default_factory=list)
    """Distinct undefined entity names, in first-seen order, at most 20."""
    skipped_entity_references: int = 0
    skipped_entities_truncated: bool = False
    """True when more distinct names were seen than ``skipped_entities`` keeps."""
    """Total references to undefined entities, including repeats and names past the cap."""
    xlink_namespace_assumed: bool = False
    """True when ``xmlns:xlink`` was added because the document used it undeclared."""
    processing_instructions: int = 0
    """Total number of dropped processing instructions."""
    processing_instruction_targets_truncated: bool = False
    """True when more distinct targets were seen than ``processing_instruction_targets`` keeps."""
    processing_instruction_targets: dict[str, int] = field(default_factory=dict)
    """Count of dropped ``<?target ...?>`` processing instructions, by target.

    Comments and processing instructions are not kept in the tree.
    """
    first_child_lines: dict[str, int] = field(default_factory=dict)
    """1-based start line of the first direct child of the root with each tag.

    Only the first occurrence is kept, so memory stays bounded by the number of
    distinct tags rather than the number of children."""


_PREDEFINED_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})

# A start tag in an ASCII-compatible encoding, and the named references inside it.
# Quoted values may contain ">", so they are matched as a whole.
_START_TAG = re.compile(rb"""<[^\s/>]+(?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>""")
_MAX_PI_TARGETS = 20
_MAX_SKIPPED_NAMES = 20
_ROOT_NAME = re.compile(rb"<[^\s/>]+")
_ENTITY_REFERENCE = re.compile(rb"&([^\s&;#<>\"']+);")


def _clark(name: str) -> str:
    """Turn expat's ``uri}local`` into ElementTree's ``{uri}local``."""
    return "{" + name if "}" in name else name


XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XLINK_DECLARATION = f' xmlns:xlink="{XLINK_NAMESPACE}"'.encode()


def parse_xml_bytes(data: bytes) -> ParsedXml:
    """Parse ``data`` into an ElementTree, refusing DTD entity declarations.

    The MusicXML DTD declares ``xmlns:xlink`` as a ``#FIXED`` attribute, so a DTD-valid
    score may use ``xlink:href`` without declaring the prefix. The DTD is never loaded,
    so when such a document fails only because of an unbound prefix, it is parsed once
    more with ``xmlns:xlink`` added to the root element, and ``xlink_namespace_assumed``
    is set on the result.

    Raises :class:`XmlSyntaxError` for malformed XML and
    :class:`ForbiddenXmlConstructError` for entity declarations.
    """
    try:
        return _parse_once(data)
    except UnsupportedEncodingError:
        raise
    except XmlSyntaxError as exc:
        if expat.errors.XML_ERROR_UNBOUND_PREFIX not in str(exc) or b"xlink:" not in data:
            raise
        patched = _with_root_xlink_declaration(data)
        if patched is None:
            raise
        try:
            parsed = _parse_once(patched)
        except XmlSyntaxError:
            raise exc from None
        parsed.xlink_namespace_assumed = True
        return parsed


class _RootFound(Exception):
    def __init__(self, offset: int) -> None:
        self.offset = offset


def _with_root_xlink_declaration(data: bytes) -> bytes | None:
    """Return ``data`` with ``xmlns:xlink`` declared on the root, or None if not found.

    Only called after a full parse got past the prolog, so the DOCTYPE has already been
    checked for forbidden constructs; this probe stops at the root start tag.
    """
    probe = expat.ParserCreate()
    probe.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def start(_name: str, _attrs: dict[str, str]) -> None:
        raise _RootFound(probe.CurrentByteIndex)

    probe.StartElementHandler = start
    try:
        probe.Parse(data, True)
    except _RootFound as found:
        # UTF-16 input does not match the byte pattern and is left unpatched.
        name = _ROOT_NAME.match(data, found.offset)
        if name is None:
            return None
        return data[: name.end()] + _XLINK_DECLARATION + data[name.end() :]
    except (expat.ExpatError, ValueError, LookupError):
        return None
    return None


def _parse_once(data: bytes) -> ParsedXml:
    builder = ET.TreeBuilder()
    # Namespace processing makes unbound prefixes a syntax error and yields the same
    # Clark-notation names (``{uri}local``) as ElementTree's own parser.
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    # Report only attributes written in the start tag. Otherwise expat copies DTD
    # <!ATTLIST> defaults into every matching element, which adds data that is not in
    # the file and can grow memory far beyond the input size.
    parser.specified_attributes = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    doctype: dict[str, str | None] = {}
    # Distinct names in first-seen order; a file can repeat one reference many times.
    skipped: dict[str, None] = {}
    skipped_references = 0
    pi_count = 0
    skipped_truncated = False
    pi_truncated = False
    first_child_lines: dict[str, int] = {}
    pi_targets: dict[str, int] = {}
    depth = 0
    in_internal_subset = False

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
        # Without a DOCTYPE an undefined reference is a syntax error, so only a document
        # with one can have references expat dropped from attribute values.
        if attrs and doctype:
            record_attribute_references(parser.CurrentByteIndex)
        tag = _clark(name)
        builder.start(tag, {_clark(key): value for key, value in attrs.items()})
        depth += 1
        if depth == 2:
            first_child_lines.setdefault(tag, parser.CurrentLineNumber)

    def end(name: str) -> None:
        nonlocal depth
        builder.end(_clark(name))
        depth -= 1

    def record_attribute_references(offset: int) -> None:
        # UTF-16 input does not match the byte pattern and is not scanned.
        tag = _START_TAG.match(data, offset)
        if tag is None:
            return
        for reference in _ENTITY_REFERENCE.finditer(tag.group()):
            entity = reference.group(1).decode("utf-8", errors="replace")
            if entity not in _PREDEFINED_ENTITIES:
                record_skipped(entity)

    def start_doctype(
        name: str, system_id: str | None, public_id: str | None, has_internal_subset: int
    ) -> None:
        nonlocal in_internal_subset
        doctype.update(name=name, system_id=system_id, public_id=public_id)
        in_internal_subset = bool(has_internal_subset)

    def end_doctype() -> None:
        nonlocal in_internal_subset
        in_internal_subset = False

    def entity_decl(name: str, is_parameter: int, *_args: object) -> None:
        kind = "Parameter entity" if is_parameter else "Entity"
        forbid(f"{kind} declaration {name!r}")

    def attlist_decl(
        element: str, attribute: str, _type: str | None, default: str | None, _required: int
    ) -> None:
        # Expat walks every declared default on each start tag of that element type,
        # even with specified_attributes, so many defaults times many elements can
        # take hours. Defaults are never applied here, so refusing them loses nothing.
        if default is not None:
            forbid(f"Attribute default for {element}/@{attribute} in an <!ATTLIST> declaration")

    def unparsed_entity_decl(name: str, *_args: object) -> None:
        forbid(f"Unparsed entity declaration {name!r}")

    def external_entity_ref(context: str | None, *_args: object) -> int:
        forbid(f"External entity reference {context!r}")
        return 0  # pragma: no cover - forbid() always raises

    def processing_instruction(target: str, _data: str) -> None:
        # Not kept in the tree; counted (one key per distinct target, capped) so the
        # caller can warn that exporter data such as <?DoletSibelius ...?> was dropped.
        nonlocal pi_count, pi_truncated
        pi_count += 1
        if target in pi_targets or len(pi_targets) < _MAX_PI_TARGETS:
            pi_targets[target] = pi_targets.get(target, 0) + 1
        else:
            pi_truncated = True

    def record_skipped(name: str) -> None:
        # Names are capped like PI targets; the reference count keeps the total.
        nonlocal skipped_references, skipped_truncated
        skipped_references += 1
        if name in skipped or len(skipped) < _MAX_SKIPPED_NAMES:
            skipped.setdefault(name)
        else:
            skipped_truncated = True

    def skipped_entity(name: str, is_parameter: int) -> None:
        if not is_parameter:
            record_skipped(name)

    def not_standalone() -> int:
        # Expat calls this once for an external DTD (normal for MusicXML), before the
        # DOCTYPE start handler, and again for each parameter-entity reference such as
        # "%pe;" inside the internal subset. Such a reference makes expat ignore later
        # entity declarations instead of reporting them, so it is refused like one.
        if in_internal_subset:
            forbid("Parameter entity reference in the internal DTD subset")
        return 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = start_doctype
    parser.EndDoctypeDeclHandler = end_doctype
    parser.EntityDeclHandler = entity_decl
    parser.AttlistDeclHandler = attlist_decl
    parser.UnparsedEntityDeclHandler = unparsed_entity_decl
    parser.ExternalEntityRefHandler = external_entity_ref
    parser.SkippedEntityHandler = skipped_entity
    parser.ProcessingInstructionHandler = processing_instruction
    parser.NotStandaloneHandler = not_standalone

    try:
        parser.Parse(data, True)
    except LookupError as exc:
        # pyexpat looks up a declared encoding through Python codecs: an unknown name
        # raises LookupError.
        raise UnsupportedEncodingError(str(exc), 1, 1) from exc
    except ValueError as exc:
        # pyexpat raises a plain ValueError for declared multi-byte encodings such as
        # Big5 or GBK, which it cannot decode.
        if "encoding" not in str(exc):
            raise
        raise UnsupportedEncodingError(str(exc), 1, 1) from exc
    except expat.ExpatError as exc:
        reason = expat.errors.messages.get(exc.code, str(exc))
        column = exc.offset + 1
        if reason == expat.errors.XML_ERROR_UNKNOWN_ENCODING:
            raise UnsupportedEncodingError(
                f"{reason} (line {exc.lineno}, column {column})", exc.lineno, column
            ) from exc
        raise XmlSyntaxError(
            f"{reason} (line {exc.lineno}, column {column})", exc.lineno, column
        ) from exc

    root = builder.close()
    return ParsedXml(
        root=root,
        doctype_name=doctype.get("name"),
        doctype_public_id=doctype.get("public_id"),
        doctype_system_id=doctype.get("system_id"),
        skipped_entities=list(skipped),
        skipped_entity_references=skipped_references,
        skipped_entities_truncated=skipped_truncated,
        processing_instructions=pi_count,
        processing_instruction_targets_truncated=pi_truncated,
        first_child_lines=first_child_lines,
        processing_instruction_targets=pi_targets,
    )
