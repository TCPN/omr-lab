"""Metadata extraction from a parsed ``<score-partwise>`` element."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from omr_lab.musicxml.errors import MusicXmlError, MusicXmlWarning, _listing


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


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _counted(counts: dict[str, int]) -> list[str]:
    return [f"{part_id!r} ({count} times)" for part_id, count in counts.items()]


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

    # Problems are collected first and reported once per kind, so a document with many
    # id-less or repeated parts cannot flood the warnings (or memory).
    measure_counts: dict[str, int] = {}
    parts_without_id: list[int] = []
    repeated_parts: dict[str, int] = {}
    for index, part in enumerate(part_elements, start=1):
        part_id = part.get("id")
        if not part_id:
            parts_without_id.append(index)
        elif part_id in measure_counts:
            repeated_parts[part_id] = repeated_parts.get(part_id, 1) + 1
        else:
            measure_counts[part_id] = len(part.findall("measure"))

    parts: list[PartInfo] = []
    declared: set[str] = set()
    score_parts_without_id = 0
    repeated_score_parts: dict[str, int] = {}
    missing: list[str] = []
    for score_part in part_list.findall("score-part"):
        part_id = score_part.get("id")
        if not part_id:
            score_parts_without_id += 1
            continue
        if part_id in declared:
            repeated_score_parts[part_id] = repeated_score_parts.get(part_id, 1) + 1
            continue
        declared.add(part_id)
        if part_id not in measure_counts:
            missing.append(part_id)
        parts.append(
            PartInfo(
                id=part_id,
                name=_text(score_part.find("part-name")),
                abbreviation=_text(score_part.find("part-abbreviation")),
                measure_count=measure_counts.get(part_id),
            )
        )
    undeclared = [part_id for part_id in measure_counts if part_id not in declared]
    for part_id in undeclared:
        parts.append(
            PartInfo(
                id=part_id,
                name=None,
                abbreviation=None,
                measure_count=measure_counts[part_id],
            )
        )

    if repeated_parts:
        warnings.append(
            MusicXmlWarning(
                "musicxml_duplicate_part",
                f"<part> ids used more than once in {where}: "
                f"{_listing(_counted(repeated_parts))}; only the first of each is counted.",
            )
        )
    if parts_without_id:
        numbers = _listing([str(n) for n in parts_without_id])
        warnings.append(
            MusicXmlWarning(
                "musicxml_part_without_id",
                f"{len(parts_without_id)} <part> element(s) in {where} have no id attribute "
                f"(at positions {numbers}); they cannot be matched to the part list.",
            )
        )
    if repeated_score_parts:
        warnings.append(
            MusicXmlWarning(
                "musicxml_duplicate_part",
                f"<score-part> ids declared more than once in {where}: "
                f"{_listing(_counted(repeated_score_parts))}; only the first of each is "
                "reported.",
            )
        )
    if score_parts_without_id:
        warnings.append(
            MusicXmlWarning(
                "musicxml_part_without_id",
                f"{score_parts_without_id} <score-part> element(s) in {where} have no id "
                "attribute and are skipped.",
            )
        )
    if missing:
        warnings.append(
            MusicXmlWarning(
                "musicxml_part_missing",
                f"{len(missing)} part(s) declared in <part-list> of {where} have no <part> "
                f"element: {_listing([repr(i) for i in missing])}.",
            )
        )
    if undeclared:
        warnings.append(
            MusicXmlWarning(
                "musicxml_part_undeclared",
                f"{len(undeclared)} <part> id(s) in {where} are not declared in <part-list> "
                f"and are reported without a name: {_listing([repr(i) for i in undeclared])}.",
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
