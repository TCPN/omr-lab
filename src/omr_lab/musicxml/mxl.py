"""Compressed MusicXML (.mxl): zip limits, container lookup and score entry reading."""

from __future__ import annotations

import io
import posixpath
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from omr_lab.musicxml.errors import MusicXmlError, MusicXmlWarning
from omr_lab.musicxml.safe_xml import (
    ForbiddenXmlConstructError,
    TooManyElementsError,
    XmlSyntaxError,
    parse_xml_bytes,
)

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


@dataclass
class MxlScore:
    """The score entry read from an .mxl archive, with any fallback warnings."""

    data: bytes
    member: str
    warnings: list[MusicXmlWarning] = field(default_factory=list)


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    path: Path,
    max_bytes: int,
    name: str | None = None,
) -> bytes:
    """Read one entry. ``name`` is how messages refer to it (default: zipfile's name)."""
    name = name or info.filename
    if info.file_size > max_bytes:
        raise MusicXmlError(
            "musicxml_too_large",
            f"Archive entry {name!r} in {path} is {info.file_size} bytes "
            f"uncompressed, larger than the {max_bytes}-byte limit.",
            path=path,
            member=name,
        )
    try:
        with archive.open(info) as handle:
            # Read one byte past the limit so a lying size header is still caught.
            data = handle.read(max_bytes + 1)
    except _EXTRACT_ERRORS as exc:
        raise MusicXmlError(
            "musicxml_invalid",
            f"Could not extract {name!r} from {path}: {exc}. The .mxl archive "
            "may be corrupt, encrypted, or use an unsupported compression method.",
            path=path,
            member=name,
        ) from exc
    if len(data) > max_bytes:
        raise MusicXmlError(
            "musicxml_too_large",
            f"Archive entry {name!r} in {path} exceeds the {max_bytes}-byte "
            "limit when decompressed.",
            path=path,
            member=name,
        )
    return data


def read_mxl_score(data: bytes, *, path: Path, max_bytes: int, max_elements: int) -> MxlScore:
    """Find and read the score entry of a compressed MusicXML (.mxl) archive.

    Raises :class:`MusicXmlError` when the archive is unusable or has no score.
    """
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

        member, problem = _rootfile_from_container(
            archive, entries, path=path, max_bytes=max_bytes, max_elements=max_elements
        )
        if member is None:
            # Some zip tools write "meta-inf/", so the prefix check ignores case.
            # .musicxml entries rank before .xml ones, which may be sidecar files.
            candidates = sorted(
                (
                    name
                    for name in entries
                    if not name.upper().startswith(("META-INF/", "__MACOSX/"))
                    # macOS AppleDouble resource forks ("._score.musicxml") are not scores.
                    and not posixpath.basename(name).startswith("._")
                    and posixpath.splitext(name)[1].lower() in {".xml", ".musicxml"}
                ),
                key=lambda name: (posixpath.splitext(name)[1].lower() != ".musicxml", name),
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
                if _container_entry(entries) is None
                else "musicxml_mxl_container_invalid"
            )
            others = f" out of {len(candidates)} candidates" if len(candidates) > 1 else ""
            warnings.append(
                MusicXmlWarning(
                    code,
                    f"{path}: {problem}; loaded {member!r}, the first entry outside "
                    f"META-INF/ by name, preferring .musicxml over .xml{others}.",
                )
            )
        info = _find_entry(entries, member)
        assert info is not None  # the rootfile lookup and the fallback both checked it
        score_bytes = _read_member(archive, info, path=path, max_bytes=max_bytes, name=member)

    return MxlScore(data=score_bytes, member=member, warnings=warnings)


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
        # orig_filename: on Windows, zipfile turns "\\" into "/" in filename, which
        # corrupts Big5 names whose trail byte is 0x5C (e.g. 許 is B3 5C).
        raw = candidate.orig_filename.encode("cp437", errors="replace")
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


def _container_entry(entries: dict[str, zipfile.ZipInfo]) -> zipfile.ZipInfo | None:
    """Return META-INF/container.xml, matched without regard to case if not exact.

    Some zip tools write ``meta-inf/``; the fallback skips that prefix in any case too.
    """
    info = entries.get(CONTAINER_PATH)
    if info is not None:
        return info
    wanted = CONTAINER_PATH.casefold()
    return next((i for name, i in entries.items() if name.casefold() == wanted), None)


def _rootfile_from_container(
    archive: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    *,
    path: Path,
    max_bytes: int,
    max_elements: int,
) -> tuple[str | None, str | None]:
    """Return ``(member, None)`` for the first rootfile in META-INF/container.xml.

    Returns ``(None, reason)`` when the container is absent or unusable so the caller can
    fall back and warn. A rootfile that names a missing entry is an error.
    """
    info = _container_entry(entries)
    if info is None:
        return None, f"no {CONTAINER_PATH}"
    try:
        raw = _read_member(archive, info, path=path, max_bytes=max_bytes)
    except MusicXmlError as exc:
        # A damaged or oversized container must not hide an intact score.
        return None, f"{CONTAINER_PATH} could not be read ({exc})"
    try:
        container = parse_xml_bytes(raw, max_elements=max_elements).root
    except (XmlSyntaxError, ForbiddenXmlConstructError, TooManyElementsError) as exc:
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
