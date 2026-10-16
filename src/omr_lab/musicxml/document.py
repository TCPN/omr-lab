"""Load MusicXML artifacts (.musicxml/.xml and compressed .mxl) and extract metadata.

This layer checks that a file is a readable, well-formed ``score-partwise`` MusicXML
document and reports the metadata it declares. It does not interpret notes, lyrics, or
harmony (that is the score content model, issue #19) and a successful load says nothing
about whether the recognized music is correct.
"""

from __future__ import annotations

import io
import posixpath
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from omr_lab.musicxml.safe_xml import (
    XLINK_NAMESPACE,
    ForbiddenXmlConstructError,
    ParsedXml,
    UnsupportedEncodingError,
    XmlSyntaxError,
    parse_xml_bytes,
)

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
"""Default size limit for a MusicXML document (after decompression for ``.mxl``)."""

ZIP_MAGIC = b"PK\x03\x04"

try:  # lzma can be missing from a Python build; zipfile then cannot read LZMA entries.
    from lzma import LZMAError as _LZMAError
except ImportError:  # pragma: no cover - depends on the Python build
    _LZMAError = OSError

# Errors raised while decompressing a damaged member: zlib and lzma errors are not
# OSError subclasses, so they are listed explicitly. ValueError covers the
# UnicodeDecodeError from a local header that sets the UTF-8 flag on a non-UTF-8 name;
# OverflowError a ZIP64 header offset too large to seek to.
_EXTRACT_ERRORS = (
    zipfile.BadZipFile,
    ValueError,
    OverflowError,
    RuntimeError,
    OSError,
    EOFError,
    NotImplementedError,
    zlib.error,
    _LZMAError,
)

MAX_ARCHIVE_ENTRIES = 10_000
"""Limit on the entries an .mxl may declare. ``zipfile`` builds a ZipInfo for every
central-directory entry before any member size check runs, so an archive packed with
empty entries could exhaust memory. A MusicXML archive has a handful of entries."""

_CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
CONTAINER_PATH = "META-INF/container.xml"

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
    - ``musicxml_too_large``: the file, an archive entry, or the number of ``.mxl``
      entries exceeds a limit. Raise ``max_bytes`` or reject the input.
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


@dataclass(frozen=True)
class Creator:
    """An ``<identification><creator>`` entry. ``type`` is ``None`` when not given."""

    name: str
    type: str | None = None


@dataclass(frozen=True)
class PartInfo:
    """A part declared in ``<part-list>`` and/or present as ``<part>``.

    ``name``/``abbreviation`` are ``None`` when absent or empty. ``measure_count`` is
    the number of ``<measure>`` children of the matching ``<part>``, or ``None`` when the
    part list declares a part that has no ``<part>`` element.
    """

    id: str
    name: str | None
    abbreviation: str | None
    measure_count: int | None


def _lists_from_tuples(value: Any) -> Any:
    """Recursively turn tuples (as left by ``dataclasses.asdict``) into lists."""
    if isinstance(value, dict):
        return {key: _lists_from_tuples(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_lists_from_tuples(item) for item in value]
    return value


@dataclass(frozen=True)
class MusicXmlMetadata:
    """Metadata present in the document. Absent values are ``None`` or empty, never guessed.

    ``version`` is the root ``version`` attribute verbatim. Per the MusicXML spec an
    absent attribute means 1.0, but it is reported as ``None`` because the file did not
    say so.
    """

    version: str | None = None
    work_title: str | None = None
    work_number: str | None = None
    movement_title: str | None = None
    movement_number: str | None = None
    creators: tuple[Creator, ...] = ()
    rights: tuple[str, ...] = ()
    encoding_software: tuple[str, ...] = ()
    encoding_date: str | None = None
    parts: tuple[PartInfo, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (tuples become lists)."""
        return _lists_from_tuples(asdict(self))


@dataclass(frozen=True)
class MusicXmlDocument:
    """A loaded MusicXML document.

    ``root`` is the parsed ``<score-partwise>`` element (the full tree, unmodified, for
    later conversion). ``container`` is ``"mxl"`` for compressed input, in which case
    ``archive_member`` is the path of the score inside the archive.
    """

    path: Path | None
    root: ET.Element
    metadata: MusicXmlMetadata
    container: Literal["musicxml", "mxl"]
    archive_member: str | None = None
    doctype_public_id: str | None = None
    warnings: tuple[MusicXmlWarning, ...] = field(default=())


def load_musicxml(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> MusicXmlDocument:
    """Load and validate a MusicXML file.

    Compressed MusicXML is detected by content (zip signature), so an ``.mxl`` saved
    with a ``.musicxml`` extension still loads, and a non-zip ``.mxl`` is reported as
    invalid. ``max_bytes`` limits the file size and, for ``.mxl``, the decompressed size
    of each entry read.

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
        return _load_mxl(data, path=path, max_bytes=max_bytes)
    return _parse_document(data, path=path)


def parse_musicxml_bytes(data: bytes, *, path: Path | None = None) -> MusicXmlDocument:
    """Parse uncompressed MusicXML bytes. ``path`` is used only in messages.

    No size limit is applied: callers passing in-memory data must bound it themselves.
    """
    return _parse_document(data, path=path)


def _parse_document(
    data: bytes,
    *,
    path: Path | None,
    container: Literal["musicxml", "mxl"] = "musicxml",
    archive_member: str | None = None,
    warnings: list[MusicXmlWarning] | None = None,
) -> MusicXmlDocument:
    warnings = list(warnings or [])
    where = _describe(path, archive_member)
    parsed = _parse_safely(data, where=where, path=path, member=archive_member)
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
    if parsed.skipped_entities:
        names = ", ".join(f"&{name};" for name in parsed.skipped_entities)
        warnings.append(
            MusicXmlWarning(
                "musicxml_unresolved_entity",
                f"{where} references entities that are not defined in the document "
                f"({names}); external DTDs are never loaded, so their text was omitted.",
            )
        )

    # One warning per distinct unknown tag, so a document with many repeated unknown
    # elements cannot flood the warnings (or memory).
    unknown_counts: dict[str, int] = {}
    for child in root:
        if isinstance(child.tag, str) and child.tag not in KNOWN_PARTWISE_CHILDREN:
            unknown_counts[child.tag] = unknown_counts.get(child.tag, 0) + 1
    for tag, count in unknown_counts.items():
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


def _parse_safely(data: bytes, *, where: str, path: Path | None, member: str | None) -> ParsedXml:
    try:
        return parse_xml_bytes(data)
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


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _extract_metadata(
    root: ET.Element,
    *,
    where: str,
    path: Path | None,
    member: str | None,
    warnings: list[MusicXmlWarning],
) -> MusicXmlMetadata:
    part_list = root.find("part-list")
    if part_list is None:
        raise MusicXmlError(
            "musicxml_invalid",
            f"{where} has no <part-list>; every score-partwise document must declare its parts.",
            path=path,
            member=member,
        )
    part_elements = root.findall("part")
    if not part_elements:
        raise MusicXmlError(
            "musicxml_invalid",
            f"{where} has no <part> elements; the score contains no music.",
            path=path,
            member=member,
        )

    identification = root.find("identification")
    creators: list[Creator] = []
    rights: list[str] = []
    software: list[str] = []
    encoding_date: str | None = None
    if identification is not None:
        for creator in identification.findall("creator"):
            name = _text(creator)
            if name is not None:
                creators.append(Creator(name=name, type=creator.get("type")))
        rights = [r for r in (_text(e) for e in identification.findall("rights")) if r]
        encoding = identification.find("encoding")
        if encoding is not None:
            software = [s for s in (_text(e) for e in encoding.findall("software")) if s]
            encoding_date = _text(encoding.find("encoding-date"))

    measure_counts: dict[str, int] = {}
    for index, part in enumerate(part_elements, start=1):
        part_id = part.get("id")
        if not part_id:
            warnings.append(
                MusicXmlWarning(
                    "musicxml_part_without_id",
                    f"<part> number {index} in {where} has no id attribute; it cannot be "
                    "matched to the part list.",
                )
            )
            continue
        if part_id in measure_counts:
            warnings.append(
                MusicXmlWarning(
                    "musicxml_duplicate_part",
                    f"<part id={part_id!r}> appears more than once in {where}; only the "
                    "first is counted.",
                )
            )
            continue
        measure_counts[part_id] = len(part.findall("measure"))

    parts: list[PartInfo] = []
    declared: set[str] = set()
    for score_part in part_list.findall("score-part"):
        part_id = score_part.get("id")
        if not part_id:
            warnings.append(
                MusicXmlWarning(
                    "musicxml_part_without_id",
                    f"A <score-part> in {where} has no id attribute and is skipped.",
                )
            )
            continue
        if part_id in declared:
            warnings.append(
                MusicXmlWarning(
                    "musicxml_duplicate_part",
                    f"<score-part id={part_id!r}> is declared more than once in {where}; "
                    "only the first is reported.",
                )
            )
            continue
        declared.add(part_id)
        if part_id not in measure_counts:
            warnings.append(
                MusicXmlWarning(
                    "musicxml_part_missing",
                    f"Part {part_id!r} is declared in <part-list> of {where} but has no "
                    "<part> element.",
                )
            )
        parts.append(
            PartInfo(
                id=part_id,
                name=_text(score_part.find("part-name")),
                abbreviation=_text(score_part.find("part-abbreviation")),
                measure_count=measure_counts.get(part_id),
            )
        )
    for part_id in measure_counts:
        if part_id not in declared:
            warnings.append(
                MusicXmlWarning(
                    "musicxml_part_undeclared",
                    f"<part id={part_id!r}> in {where} is not declared in <part-list>; "
                    "it is reported without a name.",
                )
            )
            parts.append(
                PartInfo(
                    id=part_id,
                    name=None,
                    abbreviation=None,
                    measure_count=measure_counts[part_id],
                )
            )

    return MusicXmlMetadata(
        version=root.get("version"),
        work_title=_text(root.find("work/work-title")),
        work_number=_text(root.find("work/work-number")),
        movement_title=_text(root.find("movement-title")),
        movement_number=_text(root.find("movement-number")),
        creators=tuple(creators),
        rights=tuple(rights),
        encoding_software=tuple(software),
        encoding_date=encoding_date,
        parts=tuple(parts),
    )


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, path: Path, max_bytes: int
) -> bytes:
    if info.file_size > max_bytes:
        raise MusicXmlError(
            "musicxml_too_large",
            f"Archive entry {info.filename!r} in {path} is {info.file_size} bytes "
            f"uncompressed, larger than the {max_bytes}-byte limit.",
            path=path,
            member=info.filename,
        )
    try:
        with archive.open(info) as handle:
            # Read one byte past the limit so a lying size header is still caught.
            data = handle.read(max_bytes + 1)
    except _EXTRACT_ERRORS as exc:
        raise MusicXmlError(
            "musicxml_invalid",
            f"Could not extract {info.filename!r} from {path}: {exc}. The .mxl archive "
            "may be corrupt, encrypted, or use an unsupported compression method.",
            path=path,
            member=info.filename,
        ) from exc
    if len(data) > max_bytes:
        raise MusicXmlError(
            "musicxml_too_large",
            f"Archive entry {info.filename!r} in {path} exceeds the {max_bytes}-byte "
            "limit when decompressed.",
            path=path,
            member=info.filename,
        )
    return data


def _load_mxl(data: bytes, *, path: Path, max_bytes: int) -> MusicXmlDocument:
    # zipfile requires every central-directory entry to start with this signature, so
    # its count bounds the ZipInfo objects zipfile can create, whatever end-record or
    # ZIP64 layout it reads. Counting avoids mirroring zipfile's private record lookup.
    entry_bound = data.count(_CENTRAL_DIRECTORY_SIGNATURE)
    if entry_bound > MAX_ARCHIVE_ENTRIES:
        raise MusicXmlError(
            "musicxml_too_large",
            f"The .mxl archive {path} may declare up to {entry_bound} entries, more than "
            f"the {MAX_ARCHIVE_ENTRIES}-entry limit; a MusicXML archive needs only a few.",
            path=path,
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        # ValueError covers UnicodeDecodeError from an entry flagged as UTF-8 whose
        # name is not valid UTF-8 (some tools write legacy-encoded CJK names).
        raise MusicXmlError(
            "musicxml_invalid",
            f"{path} is not a valid compressed MusicXML (.mxl) file: {exc}. An .mxl file "
            "must be a zip archive.",
            path=path,
        ) from exc

    warnings: list[MusicXmlWarning] = []
    with archive:
        entries = {info.filename: info for info in archive.infolist() if not info.is_dir()}
        if not entries:
            raise MusicXmlError("musicxml_invalid", f"The .mxl archive {path} is empty.", path=path)

        member, problem = _rootfile_from_container(archive, entries, path=path, max_bytes=max_bytes)
        if member is None:
            candidates = sorted(
                name
                for name in entries
                if not name.startswith(("META-INF/", "__MACOSX/"))
                # macOS AppleDouble resource forks ("._score.musicxml") are not scores.
                and not posixpath.basename(name).startswith("._")
                and posixpath.splitext(name)[1].lower() in {".xml", ".musicxml"}
            )
            if not candidates:
                raise MusicXmlError(
                    "musicxml_invalid",
                    f"The .mxl archive {path} contains no MusicXML score: no usable "
                    f"{CONTAINER_PATH} rootfile and no .xml/.musicxml entry outside "
                    "META-INF/.",
                    path=path,
                )
            member = candidates[0]
            code = (
                "musicxml_mxl_container_missing"
                if CONTAINER_PATH not in entries
                else "musicxml_mxl_container_invalid"
            )
            others = f" out of {len(candidates)} candidates" if len(candidates) > 1 else ""
            warnings.append(
                MusicXmlWarning(
                    code,
                    f"{path}: {problem}; loaded {member!r}, the first .xml/.musicxml entry "
                    f"outside META-INF/ by name{others}.",
                )
            )
        info = _find_entry(entries, member)
        assert info is not None  # the rootfile lookup and the fallback both checked it
        score_bytes = _read_member(archive, info, path=path, max_bytes=max_bytes)

    return _parse_document(
        score_bytes, path=path, container="mxl", archive_member=member, warnings=warnings
    )


# Encodings tried, in order, for entry names stored without the UTF-8 flag. macOS
# Archive Utility writes UTF-8 names without setting the flag; older Windows tools
# write the local code page (Big5/CP950 or GBK for Chinese titles).
_LEGACY_NAME_ENCODINGS = ("utf-8", "cp950", "gbk")


def _find_entry(entries: dict[str, zipfile.ZipInfo], name: str) -> zipfile.ZipInfo | None:
    """Return the archive entry for ``name`` as written in META-INF/container.xml.

    zipfile decodes names without the UTF-8 flag (bit 0x800) as cp437, so a Chinese
    file name re-zipped on macOS or older Windows does not match its container entry
    exactly. Such names are re-decoded from their raw bytes before giving up. Names
    are compared in Unicode NFC, because macOS tools may store decomposed (NFD) names.
    """
    info = entries.get(name)
    if info is not None:
        return info
    wanted = unicodedata.normalize("NFC", name)
    for candidate in entries.values():
        if unicodedata.normalize("NFC", candidate.filename) == wanted:
            return candidate
        if candidate.flag_bits & 0x800:
            continue
        raw = candidate.filename.encode("cp437", errors="replace")
        for encoding in _LEGACY_NAME_ENCODINGS:
            try:
                decoded = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            if unicodedata.normalize("NFC", decoded) == wanted:
                return candidate
    return None


def _local_name(tag: str) -> str:
    """Strip the ``{namespace}`` qualifier from an element tag."""
    return tag.rsplit("}", 1)[-1]


def _rootfile_from_container(
    archive: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    *,
    path: Path,
    max_bytes: int,
) -> tuple[str | None, str | None]:
    """Return ``(member, None)`` for the first rootfile in META-INF/container.xml.

    Returns ``(None, reason)`` when the container is absent or unusable so the caller can
    fall back and warn. A rootfile that names a missing entry is an error.
    """
    info = entries.get(CONTAINER_PATH)
    if info is None:
        return None, f"no {CONTAINER_PATH}"
    try:
        raw = _read_member(archive, info, path=path, max_bytes=max_bytes)
    except MusicXmlError as exc:
        # A damaged or oversized container must not hide an intact score.
        return None, f"{CONTAINER_PATH} could not be read ({exc})"
    try:
        container = parse_xml_bytes(raw).root
    except (XmlSyntaxError, ForbiddenXmlConstructError) as exc:
        return None, f"{CONTAINER_PATH} could not be parsed ({exc})"

    # Match local names: containers normally declare the OCF namespace, either as the
    # default namespace or with a prefix, so tags arrive as ``{urn:...}rootfile``.
    rootfiles = [
        rootfile
        for group in container
        if _local_name(group.tag) == "rootfiles"
        for rootfile in group
        if _local_name(rootfile.tag) == "rootfile"
    ]
    # MusicXML 4.0: the first rootfile is the main score; others (e.g. PDF renderings)
    # are ignored.
    member = rootfiles[0].get("full-path") if rootfiles else None
    if not member:
        return None, f"{CONTAINER_PATH} names no rootfile with a full-path"
    if _find_entry(entries, member) is None:
        raise MusicXmlError(
            "musicxml_invalid",
            f"{CONTAINER_PATH} in {path} points to {member!r}, which is not in the "
            "archive. The .mxl file is incomplete or corrupt.",
            path=path,
            member=member,
        )
    return member, None
