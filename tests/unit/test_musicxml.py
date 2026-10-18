import inspect
import json
import os
import socket
import struct
import sys
import unicodedata
import zipfile
from pathlib import Path

import pytest

from omr_lab.musicxml import (
    DEFAULT_MAX_ELEMENTS,
    Creator,
    MusicXmlError,
    PartInfo,
    load_musicxml,
    parse_musicxml_bytes,
)
from omr_lab.musicxml import mxl as mxl_module
from omr_lab.musicxml.safe_xml import TooManyElementsError, parse_xml_bytes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "musicxml"

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container>
  <rootfiles>
    <rootfile full-path="{path}" media-type="application/vnd.recordare.musicxml+xml"/>
    <rootfile full-path="score.pdf" media-type="application/pdf"/>
  </rootfiles>
</container>
"""


def warning_codes(document) -> list[str]:
    return [w.code for w in document.warnings]


def make_mxl(target: Path, entries: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.recordare.musicxml")
        for name, content in entries.items():
            archive.writestr(name, content)
    return target


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


# Valid input


def test_valid_partwise_metadata(no_network: None) -> None:
    document = load_musicxml(FIXTURES / "valid-partwise.musicxml")

    assert document.container == "musicxml"
    assert document.archive_member is None
    assert document.root.tag == "score-partwise"
    assert document.doctype_public_id == "-//Recordare//DTD MusicXML 4.0 Partwise//EN"
    assert document.warnings == ()

    meta = document.metadata
    assert meta.version == "4.0"
    assert meta.work_title == "小星星 Synthetic Test Tune"
    assert meta.work_number == "Op. 0"
    assert meta.movement_title == "Theme"
    assert meta.movement_number == "1"
    assert meta.creators == (
        Creator(name="OMR Lab Fixture", type="composer"),
        Creator(name="OMR Lab Fixture", type="lyricist"),
    )
    assert meta.rights == ("CC0 1.0 — synthetic fixture written for omr-lab tests",)
    assert meta.encoding_software == ("handwritten",)
    assert meta.encoding_date == "2026-10-14"
    assert meta.parts == (
        PartInfo(id="P1", name="Voice", abbreviation="V.", measure_count=2),
        PartInfo(id="P2", name=None, abbreviation=None, measure_count=2),
    )


def test_full_tree_is_kept_for_later_conversion() -> None:
    document = load_musicxml(FIXTURES / "valid-partwise.musicxml")
    lyrics = document.root.findall("part/measure/note/lyric")
    assert [(lyric.get("number"), lyric.findtext("text")) for lyric in lyrics] == [
        ("1", "一"),
        ("2", "二"),
    ]
    assert document.root.find("part/measure/harmony/root/root-step").text == "C"


def test_minimal_document_reports_absent_metadata_as_none() -> None:
    document = load_musicxml(str(FIXTURES / "minimal-partwise.xml"))
    meta = document.metadata
    assert meta.version is None  # absent, not assumed to be "1.0"
    assert meta.work_title is None
    assert meta.movement_title is None
    assert meta.creators == ()
    assert meta.encoding_software == ()
    assert meta.encoding_date is None
    assert meta.parts == (PartInfo(id="P1", name="Melody", abbreviation=None, measure_count=1),)
    assert document.warnings == ()


def test_metadata_to_dict_is_json_serializable() -> None:
    meta = load_musicxml(FIXTURES / "valid-partwise.musicxml").metadata
    data = json.loads(json.dumps(meta.to_dict(), ensure_ascii=False))
    assert data["version"] == "4.0"
    assert data["creators"][0] == {"name": "OMR Lab Fixture", "type": "composer"}
    assert data["parts"][1] == {
        "id": "P2",
        "name": None,
        "abbreviation": None,
        "measure_count": 2,
    }


def test_utf16_document_is_decoded_from_its_declaration() -> None:
    text = (FIXTURES / "minimal-partwise.xml").read_text(encoding="utf-8")
    data = text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
    document = parse_musicxml_bytes(data)
    assert document.metadata.parts[0].name == "Melody"


# Warnings for unsupported or inconsistent constructs


def test_unknown_elements_and_part_mismatches_are_warned() -> None:
    document = load_musicxml(FIXTURES / "unknown-elements.musicxml")
    assert warning_codes(document) == [
        "musicxml_unknown_element",
        "musicxml_part_missing",
        "musicxml_part_undeclared",
    ]
    unknown = document.warnings[0].message
    assert "<engine-debug>" in unknown and "line 7" in unknown
    # The unknown element is preserved, not dropped.
    assert document.root.findtext("engine-debug") == "vendor-specific data"
    assert document.metadata.parts == (
        PartInfo(id="P1", name="Voice", abbreviation=None, measure_count=1),
        PartInfo(id="P2", name="Ghost", abbreviation=None, measure_count=None),
        PartInfo(id="P3", name=None, abbreviation=None, measure_count=1),
    )


def test_repeated_unknown_elements_give_one_warning_per_tag() -> None:
    minimal = (FIXTURES / "minimal-partwise.xml").read_bytes()
    extra = b"\n" + b"<x/>" * 5000 + b"\n<y/>\n<x/>"
    data = minimal.replace(b"</score-partwise>", extra + b"</score-partwise>")
    document = parse_musicxml_bytes(data)
    unknown = [w for w in document.warnings if w.code == "musicxml_unknown_element"]
    assert len(unknown) == 2, unknown
    assert "<x>" in unknown[0].message and "5001 times" in unknown[0].message
    assert "first at line" in unknown[0].message
    assert "<y>" in unknown[1].message and "times" not in unknown[1].message
    assert len(document.root.findall("x")) == 5001  # still kept in the tree


def test_repeated_part_problems_give_one_warning_per_kind() -> None:
    score_parts = "<score-part id='P1'/>" * 1000 + "<score-part/>" * 1000
    score_parts += "".join(f"<score-part id='M{i}'/>" for i in range(1000))
    parts = "<part id='P1'/>" * 1000 + "<part/>" * 1000
    parts += "".join(f"<part id='U{i}'/>" for i in range(1000))
    data = f"<score-partwise><part-list>{score_parts}</part-list>{parts}</score-partwise>"
    document = parse_musicxml_bytes(data.encode())
    assert warning_codes(document) == [
        "musicxml_duplicate_part",
        "musicxml_part_without_id",
        "musicxml_duplicate_part",
        "musicxml_part_without_id",
        "musicxml_part_missing",
        "musicxml_part_undeclared",
    ]
    assert all(len(w.message) < 500 for w in document.warnings)
    assert "'P1' (1000 times)" in document.warnings[0].message
    assert "1000 <part> element(s)" in document.warnings[1].message
    assert "and 995 more" in document.warnings[1].message


def test_duplicate_and_unidentified_parts_are_warned() -> None:
    data = b"""<score-partwise>
      <part-list>
        <score-part id="P1"><part-name>A</part-name></score-part>
        <score-part id="P1"><part-name>B</part-name></score-part>
        <score-part><part-name>C</part-name></score-part>
      </part-list>
      <part id="P1"><measure number="1"/></part>
      <part id="P1"><measure number="1"/><measure number="2"/></part>
      <part><measure number="1"/></part>
    </score-partwise>"""
    document = parse_musicxml_bytes(data)
    assert warning_codes(document) == [
        "musicxml_duplicate_part",
        "musicxml_part_without_id",
        "musicxml_duplicate_part",
        "musicxml_part_without_id",
    ]
    assert document.metadata.parts == (
        PartInfo(id="P1", name="A", abbreviation=None, measure_count=1),
    )


# Missing and unreadable files


def test_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.musicxml"
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(missing)
    error = excinfo.value
    assert error.code == "musicxml_missing"
    assert error.path == missing
    assert str(missing) in str(error)
    assert "not found" in str(error)


def test_directory_is_reported_as_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(tmp_path)
    assert excinfo.value.code == "musicxml_missing"
    assert "not a regular file" in str(excinfo.value)


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions and a non-root user",
)
def test_unreadable_file(tmp_path: Path) -> None:
    target = tmp_path / "locked.musicxml"
    target.write_bytes((FIXTURES / "minimal-partwise.xml").read_bytes())
    target.chmod(0)
    try:
        with pytest.raises(MusicXmlError) as excinfo:
            load_musicxml(target)
    finally:
        target.chmod(0o600)
    assert excinfo.value.code == "musicxml_unreadable"


def test_unreadable_file_via_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "score.musicxml"
    target.write_bytes(b"<score-partwise/>")

    def fail(_self: Path, *args: object, **kwargs: object) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_unreadable"
    assert "Permission denied" in str(excinfo.value)


def test_file_over_size_limit(tmp_path: Path) -> None:
    target = tmp_path / "big.musicxml"
    target.write_bytes((FIXTURES / "minimal-partwise.xml").read_bytes())
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target, max_bytes=10)
    assert excinfo.value.code == "musicxml_too_large"
    assert "max_bytes" in str(excinfo.value)


# Malformed or non-MusicXML input


def test_malformed_xml_reports_line_and_column() -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(FIXTURES / "malformed.musicxml")
    error = excinfo.value
    assert error.code == "musicxml_invalid"
    assert error.line == 8
    assert error.column is not None and error.column > 1
    assert "mismatched tag" in str(error)
    assert "line 8" in str(error)


@pytest.mark.parametrize(
    "data",
    [b"", b"   ", b"<score-partwise>", b"\x00\x01binary garbage"],
    ids=["empty", "whitespace", "truncated", "binary"],
)
def test_empty_or_truncated_input_is_invalid(data: bytes) -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_invalid"
    assert excinfo.value.line is not None


def test_wrong_root_element() -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(FIXTURES / "wrong-root.xml")
    assert excinfo.value.code == "musicxml_invalid"
    assert "<html>" in str(excinfo.value)
    assert "score-partwise" in str(excinfo.value)


def test_timewise_is_unsupported() -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(FIXTURES / "timewise.musicxml")
    assert excinfo.value.code == "musicxml_unsupported"
    assert "score-timewise" in str(excinfo.value)


def test_unbound_namespace_prefix_is_invalid() -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(b"<score-partwise><bad:note/></score-partwise>")
    assert excinfo.value.code == "musicxml_invalid"
    assert "unbound prefix" in str(excinfo.value)


def test_namespaced_names_use_clark_notation() -> None:
    parsed = parse_xml_bytes(
        b'<a xmlns:xlink="http://www.w3.org/1999/xlink" xml:lang="zh"><b xlink:href="x"/></a>'
    )
    assert parsed.root.get("{http://www.w3.org/XML/1998/namespace}lang") == "zh"
    assert parsed.root[0].get("{http://www.w3.org/1999/xlink}href") == "x"


def test_opus_is_unsupported() -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(
            b'<opus xmlns:xlink="http://www.w3.org/1999/xlink">'
            b'<score xlink:href="a.musicxml"/></opus>'
        )
    assert excinfo.value.code == "musicxml_unsupported"


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"<score-partwise><part id='P1'/></score-partwise>", "<part-list>"),
        (b"<score-partwise><part-list/></score-partwise>", "no <part> elements"),
    ],
)
def test_missing_required_structure(data: bytes, fragment: str) -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_invalid"
    assert fragment in str(excinfo.value)


# XML security: no DTD fetching, no external entities, no entity expansion

MINIMAL_BODY = (
    "<score-partwise><part-list><score-part id='P1'><part-name>{name}</part-name>"
    "</score-part></part-list><part id='P1'><measure number='1'/></part></score-partwise>"
)


def test_external_entity_is_refused(tmp_path: Path, no_network: None) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    data = (
        f'<!DOCTYPE score-partwise [<!ENTITY xxe SYSTEM "{secret.as_uri()}">]>'
        + MINIMAL_BODY.format(name="&xxe;")
    ).encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_unsupported"
    assert "xxe" in str(excinfo.value)
    assert "TOP SECRET" not in str(excinfo.value)


def test_entity_expansion_is_refused() -> None:
    data = (
        '<!DOCTYPE score-partwise [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>' + MINIMAL_BODY.format(name="&b;")
    ).encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_unsupported"
    assert excinfo.value.line == 1


def test_parameter_entity_is_refused(tmp_path: Path) -> None:
    dtd = tmp_path / "evil.dtd"
    dtd.write_text('<!ENTITY name "from external dtd">', encoding="utf-8")
    data = (
        f'<!DOCTYPE score-partwise [<!ENTITY % ext SYSTEM "{dtd.as_uri()}"> %ext;]>'
        + MINIMAL_BODY.format(name="&name;")
    ).encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_unsupported"


@pytest.mark.parametrize(
    "doctype",
    [
        b'<!DOCTYPE score-partwise [ %pe; <!ENTITY a "x"> ]>',
        b'<!DOCTYPE score-partwise SYSTEM "partwise.dtd" [ %pe; <!ENTITY a "x"> ]>',
    ],
)
def test_parameter_entity_reference_in_internal_subset_is_refused(doctype: bytes) -> None:
    # Expat would otherwise ignore the later declaration instead of reporting it.
    data = doctype + MINIMAL_BODY.format(name="&a;").encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_unsupported"
    assert "Parameter entity reference" in str(excinfo.value)


@pytest.mark.parametrize(
    "subset",
    [
        b"",
        b"<!-- note -->",
        b"<!ELEMENT x ANY>",
        b"<!ATTLIST x y CDATA #IMPLIED z CDATA #REQUIRED>",
    ],
)
@pytest.mark.parametrize(
    "external_id",
    [
        b'PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        b'"http://www.musicxml.org/dtds/partwise.dtd"',
        b'SYSTEM "partwise.dtd"',
    ],
)
def test_external_dtd_with_harmless_internal_subset_loads(
    external_id: bytes, subset: bytes, no_network: None
) -> None:
    data = (
        b"<!DOCTYPE score-partwise "
        + external_id
        + b" [ "
        + subset
        + b" ]>"
        + MINIMAL_BODY.format(name="Voice").encode()
    )
    document = parse_musicxml_bytes(data)
    assert document.metadata.parts[0].name == "Voice"
    assert document.warnings == ()


XLINK_SCORE = (
    "<score-partwise{decl}><credit page='1'><credit-image xlink:href='a.png' "
    "type='image/png'/></credit><part-list><score-part id='P1'><part-name>Voice"
    "</part-name></score-part></part-list><part id='P1'><measure number='1'/></part>"
    "</score-partwise>"
)


def test_undeclared_xlink_prefix_is_assumed_with_warning() -> None:
    doctype = (
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">'
    )
    data = (doctype + XLINK_SCORE.format(decl=" version='4.0'")).encode()
    document = parse_musicxml_bytes(data)
    image = document.root.find("credit/credit-image")
    assert image is not None
    assert image.get("{http://www.w3.org/1999/xlink}href") == "a.png"
    assert document.metadata.version == "4.0"
    assert warning_codes(document) == ["musicxml_xlink_namespace_assumed"]


def test_declared_xlink_prefix_loads_without_warning() -> None:
    decl = " xmlns:xlink='http://www.w3.org/1999/xlink'"
    document = parse_musicxml_bytes(XLINK_SCORE.format(decl=decl).encode())
    assert document.warnings == ()


def test_undeclared_xlink_next_to_a_local_declaration_is_assumed() -> None:
    local = (
        "<credit page='1' xmlns:xlink='http://www.w3.org/1999/xlink'>"
        "<credit-image xlink:href='b.png' type='image/png'/></credit>"
    )
    data = XLINK_SCORE.format(decl="").replace("<credit page", local + "<credit page", 1)
    document = parse_musicxml_bytes(("<!-- xmlns:xlink -->" + data).encode())
    hrefs = [
        e.get("{http://www.w3.org/1999/xlink}href") for e in document.root.iter("credit-image")
    ]
    assert hrefs == ["b.png", "a.png"]
    assert warning_codes(document) == ["musicxml_xlink_namespace_assumed"]


def test_other_unbound_prefix_is_still_invalid() -> None:
    data = XLINK_SCORE.format(decl="").replace("xlink:href", "foo:href").encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_invalid"
    assert "unbound prefix" in str(excinfo.value)


def test_undeclared_xlink_with_another_error_reports_the_original_error() -> None:
    data = XLINK_SCORE.format(decl="").replace("</score-partwise>", "").encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_invalid"
    assert "unbound prefix" in str(excinfo.value)


@pytest.mark.parametrize(
    "declaration",
    [
        b'<!ATTLIST score-part id CDATA "P1">',
        b"<!ATTLIST measure a1 CDATA 'x' a2 CDATA 'x'>",
        b"<!ATTLIST score-partwise xmlns:xlink CDATA #FIXED 'http://www.w3.org/1999/xlink'>",
    ],
)
def test_attlist_defaults_are_refused(declaration: bytes) -> None:
    data = (
        b"<!DOCTYPE score-partwise ["
        + declaration
        + b"]>"
        + MINIMAL_BODY.format(name="Voice").encode()
    )
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_unsupported"
    assert "<!ATTLIST>" in str(excinfo.value)


def test_processing_instructions_are_dropped_with_a_warning() -> None:
    data = (
        b'<?xml version="1.0"?><!-- exported --><score-partwise><part-list>'
        b"<score-part id='P1'><part-name>Voice</part-name></score-part></part-list>"
        b"<part id='P1'><measure number='1'><?DoletSibelius a?><!-- c -->"
        b"<?DoletSibelius b?></measure></part></score-partwise>"
    )
    document = parse_musicxml_bytes(data)
    measure = document.root.find("part/measure")
    assert measure is not None and len(measure) == 0
    assert warning_codes(document) == ["musicxml_processing_instructions_dropped"]
    assert "<?DoletSibelius?> x2" in document.warnings[0].message
    assert "not recorded" not in document.warnings[0].message


def test_processing_instruction_targets_are_capped() -> None:
    pis = "".join(f"<?t{i} x?>" for i in range(1000))
    data = MINIMAL_BODY.replace("<measure number='1'/>", f"<measure number='1'>{pis}</measure>")
    parsed = parse_xml_bytes(data.format(name="Voice").encode())
    assert len(parsed.processing_instruction_targets) == 20
    assert parsed.processing_instructions == 1000
    assert parsed.processing_instruction_targets_truncated
    document = parse_musicxml_bytes(data.format(name="Voice").encode())
    message = document.warnings[0].message
    assert "1000 processing instruction(s)" in message
    assert "and 15 more; further targets not recorded" in message
    assert len(message) < 500


def test_external_dtd_is_not_loaded(tmp_path: Path, no_network: None) -> None:
    dtd = tmp_path / "partwise.dtd"
    dtd.write_text('<!ENTITY name "from external dtd">', encoding="utf-8")
    data = (
        f'<!DOCTYPE score-partwise SYSTEM "{dtd.as_uri()}">' + MINIMAL_BODY.format(name="X&name;Y")
    ).encode()
    document = parse_musicxml_bytes(data)
    assert "from external dtd" not in document.metadata.parts[0].name
    assert warning_codes(document) == ["musicxml_unresolved_entity"]
    assert "&name;" in document.warnings[0].message


def test_many_distinct_unresolved_entities_give_one_short_warning(no_network: None) -> None:
    refs = "".join(f"&e{i};" for i in range(10_000))
    data = (
        '<!DOCTYPE score-partwise SYSTEM "partwise.dtd">' + MINIMAL_BODY.format(name=refs + "&e1;")
    ).encode()
    parsed = parse_xml_bytes(data)
    assert len(parsed.skipped_entities) == 20
    assert parsed.skipped_entity_references == 10_001
    document = parse_musicxml_bytes(data)
    assert warning_codes(document) == ["musicxml_unresolved_entity"]
    assert len(document.warnings[0].message) < 500
    assert "10001 reference(s)" in document.warnings[0].message
    assert "further names not recorded" in document.warnings[0].message


def test_twenty_or_fewer_unresolved_entities_are_not_marked_truncated(no_network: None) -> None:
    refs = "".join(f"&e{i};" for i in range(20))
    data = '<!DOCTYPE score-partwise SYSTEM "partwise.dtd">' + MINIMAL_BODY.format(name=refs)
    document = parse_musicxml_bytes(data.encode())
    assert "and 15 more" in document.warnings[0].message
    assert "not recorded" not in document.warnings[0].message


def test_many_distinct_unknown_elements_give_bounded_warnings() -> None:
    unknown = "".join(f"<x{i}/>" for i in range(10_000))
    data = MINIMAL_BODY.replace("<part-list>", unknown + "<part-list>")
    document = parse_musicxml_bytes(data.format(name="Voice").encode())
    assert warning_codes(document) == ["musicxml_unknown_element"] * 6
    assert "9995 more distinct unknown elements" in document.warnings[5].message
    assert all(len(w.message) < 500 for w in document.warnings)


def test_unresolved_entity_in_attribute_is_warned() -> None:
    data = (
        b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        b'"http://www.musicxml.org/dtds/partwise.dtd">'
        b'<score-partwise version="4.0" data-x="1 &gt; 0 &amp; &#65;">'
        b'<part-list><score-part id="P1" data-y="a>&nbsp;b">'
        b"<part-name>Voice</part-name></score-part></part-list>"
        b'<part id="P1"><measure number="1"/></part></score-partwise>'
    )
    parsed = parse_xml_bytes(data)
    assert parsed.skipped_entities == ["nbsp"]
    document = parse_musicxml_bytes(data)
    assert warning_codes(document) == ["musicxml_unresolved_entity"]
    assert "&nbsp;" in document.warnings[0].message


def test_repeated_unresolved_entity_is_recorded_once(tmp_path: Path, no_network: None) -> None:
    dtd = tmp_path / "partwise.dtd"
    dtd.write_text("", encoding="utf-8")
    data = (
        f'<!DOCTYPE score-partwise SYSTEM "{dtd.as_uri()}">'
        + MINIMAL_BODY.format(name="&a;" * 5000 + "&b;&a;")
    ).encode()
    parsed = parse_xml_bytes(data)
    assert parsed.skipped_entities == ["a", "b"]
    document = parse_musicxml_bytes(data)
    assert warning_codes(document) == ["musicxml_unresolved_entity"]
    assert document.warnings[0].message.count("&a;") == 1


# Compressed MusicXML (.mxl)


def test_mxl_with_container(tmp_path: Path, no_network: None) -> None:
    score = (FIXTURES / "valid-partwise.musicxml").read_bytes()
    archive = make_mxl(
        tmp_path / "score.mxl",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="music/score.musicxml"),
            "music/score.musicxml": score,
            "aaa-other.xml": "<not-a-score/>",
        },
    )
    document = load_musicxml(archive)
    assert document.container == "mxl"
    assert document.archive_member == "music/score.musicxml"
    assert document.metadata.work_title == "小星星 Synthetic Test Tune"
    assert document.warnings == ()


OCF_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"


@pytest.mark.parametrize(
    "container",
    [
        CONTAINER_XML.replace("<container>", f'<container version="1.0" xmlns="{OCF_NAMESPACE}">'),
        CONTAINER_XML.replace("<container>", f'<ocf:container xmlns:ocf="{OCF_NAMESPACE}">')
        .replace("</container>", "</ocf:container>")
        .replace("rootfiles>", "ocf:rootfiles>")
        .replace("<rootfile ", "<ocf:rootfile "),
    ],
    ids=["default-namespace", "prefixed-namespace"],
)
def test_mxl_with_namespaced_container(tmp_path: Path, container: str) -> None:
    # Real MusicXML containers declare the OCF namespace.
    archive = make_mxl(
        tmp_path / "score.mxl",
        {
            "META-INF/container.xml": container.format(path="music/score.musicxml"),
            "music/score.musicxml": (FIXTURES / "valid-partwise.musicxml").read_bytes(),
            "aaa-other.xml": "<not-a-score/>",
        },
    )
    document = load_musicxml(archive)
    assert document.archive_member == "music/score.musicxml"
    assert document.warnings == ()


def test_mxl_with_too_many_entries_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = make_mxl(
        tmp_path / "many.mxl",
        {f"empty-{index}.txt": b"" for index in range(50)}
        | {"score.xml": (FIXTURES / "minimal-partwise.xml").read_bytes()},
    )
    assert load_musicxml(archive).archive_member == "score.xml"

    monkeypatch.setattr(mxl_module, "MAX_ARCHIVE_ENTRIES", 20)
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive)
    assert excinfo.value.code == "musicxml_too_large"
    assert "entry limit" in str(excinfo.value)


@pytest.mark.parametrize("lie_in_end_record", [False, True], ids=["honest", "zeroed-eocd"])
def test_zip64_archive_with_many_entries_is_refused(
    tmp_path: Path, lie_in_end_record: bool
) -> None:
    # More than 65535 entries forces the ZIP64 end records. The bound counts entry
    # signatures, so it holds whatever end record (or ZIP64 locator offset) zipfile
    # trusts; zeroing the classic record's size and count fields must not bypass it.
    archive = tmp_path / "huge.mxl"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as handle:
        for index in range(70_000):
            handle.writestr(f"{index}", b"")
    data = bytearray(archive.read_bytes())
    assert b"PK\x06\x06" in data[-200:]
    if lie_in_end_record:
        eocd = data.rfind(b"PK\x05\x06")
        data[eocd + 8 : eocd + 16] = bytes(8)  # entry counts and directory size
        archive.write_bytes(bytes(data))

    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive)
    assert excinfo.value.code == "musicxml_too_large"
    assert "entry limit" in str(excinfo.value)


def test_mxl_detected_by_content_not_extension(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "renamed.musicxml",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="score.xml"),
            "score.xml": (FIXTURES / "minimal-partwise.xml").read_bytes(),
        },
    )
    assert load_musicxml(archive).container == "mxl"


def test_mxl_without_container_falls_back_with_warning(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "score.mxl",
        {
            "b.musicxml": (FIXTURES / "minimal-partwise.xml").read_bytes(),
            "a.xml": (FIXTURES / "valid-partwise.musicxml").read_bytes(),
            "META-INF/other.xml": "<x/>",
        },
    )
    document = load_musicxml(archive)
    # .musicxml entries rank before .xml ones; within a suffix, the order is by name.
    assert document.archive_member == "b.musicxml"
    assert warning_codes(document) == ["musicxml_mxl_container_missing"]
    assert "out of 2 candidates" in document.warnings[0].message


def test_mxl_with_unparseable_container_falls_back(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "score.mxl",
        {
            "META-INF/container.xml": "<container><rootfiles>",
            "score.xml": (FIXTURES / "minimal-partwise.xml").read_bytes(),
        },
    )
    document = load_musicxml(archive)
    assert document.archive_member == "score.xml"
    assert warning_codes(document) == ["musicxml_mxl_container_invalid"]
    assert "could not be parsed" in document.warnings[0].message


def test_mxl_rootfile_missing_from_archive(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "score.mxl",
        {"META-INF/container.xml": CONTAINER_XML.format(path="gone.xml")},
    )
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive)
    assert excinfo.value.code == "musicxml_invalid"
    assert "gone.xml" in str(excinfo.value)


def test_mxl_member_errors_name_the_member(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "score.mxl",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="score.xml"),
            "score.xml": (FIXTURES / "malformed.musicxml").read_bytes(),
        },
    )
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive)
    error = excinfo.value
    assert error.code == "musicxml_invalid"
    assert error.member == "score.xml"
    assert error.line == 8
    assert "'score.xml'" in str(error)


def test_mxl_without_any_score(tmp_path: Path) -> None:
    archive = make_mxl(tmp_path / "score.mxl", {"readme.txt": "hello"})
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive)
    assert excinfo.value.code == "musicxml_invalid"
    assert "contains no MusicXML score" in str(excinfo.value)


def test_empty_mxl_archive(tmp_path: Path) -> None:
    target = tmp_path / "empty.mxl"
    with zipfile.ZipFile(target, "w"):
        pass
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"
    assert "empty" in str(excinfo.value)


@pytest.mark.parametrize(
    "content",
    [b"", b"this is not a zip", b"PK\x03\x04truncated"],
    ids=["zero-bytes", "text", "truncated-zip"],
)
def test_corrupt_mxl(tmp_path: Path, content: bytes) -> None:
    target = tmp_path / "broken.mxl"
    target.write_bytes(content)
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"
    assert ".mxl" in str(excinfo.value)


@pytest.mark.parametrize(
    "compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA], ids=["deflate", "lzma"]
)
def test_mxl_with_corrupt_member_data(tmp_path: Path, compression: int) -> None:
    # Headers stay intact; only the member's compressed stream is damaged, as in a
    # truncated download or an engine that crashed while writing.
    target = tmp_path / "damaged.mxl"
    score = (FIXTURES / "valid-partwise.musicxml").read_bytes()
    with zipfile.ZipFile(target, "w", compression=compression) as archive:
        archive.writestr("score.xml", score)
    data = bytearray(target.read_bytes())
    with zipfile.ZipFile(target) as archive:
        info = archive.getinfo("score.xml")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for offset in range(start + 8, start + min(info.compress_size, 64)):
        data[offset] ^= 0xFF
    target.write_bytes(bytes(data))

    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"
    assert excinfo.value.member == "score.xml"


@pytest.mark.parametrize("encoding", ["utf-8", "cp950", "gbk"])
def test_mxl_rootfile_name_without_utf8_flag_is_found(tmp_path: Path, encoding: str) -> None:
    # macOS Archive Utility stores UTF-8 names without the UTF-8 flag; older Windows
    # tools store the local code page. zipfile then decodes the name as cp437.
    name = "月亮代表我的心.musicxml"
    placeholder = "Q" * len(name.encode(encoding))
    target = make_mxl(
        tmp_path / "song.mxl",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path=name),
            placeholder: (FIXTURES / "valid-partwise.musicxml").read_bytes(),
        },
    )
    data = target.read_bytes()
    assert data.count(placeholder.encode()) == 2  # local and central headers
    target.write_bytes(data.replace(placeholder.encode(), name.encode(encoding)))

    document = load_musicxml(target)
    assert document.archive_member == name
    assert document.metadata.work_title == "小星星 Synthetic Test Tune"
    assert document.warnings == ()


def test_mxl_big5_name_with_backslash_trail_byte_is_found_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 許 is B3 5C in Big5; on Windows zipfile rewrites the 0x5C ("\\") in filename.
    name = "許.musicxml"
    encoded = name.encode("cp950")
    assert b"\\" in encoded
    placeholder = "Q" * len(encoded)
    target = make_mxl(
        tmp_path / "song.mxl",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path=name),
            placeholder: (FIXTURES / "valid-partwise.musicxml").read_bytes(),
        },
    )
    target.write_bytes(target.read_bytes().replace(placeholder.encode(), encoded))
    # zipfile.os is the global os module, so this changes os.sep process-wide until the
    # test ends; ZipInfo has no narrower hook on every supported Python version.
    monkeypatch.setattr(zipfile.os, "sep", "\\")

    document = load_musicxml(target)
    assert document.archive_member == name


@pytest.mark.parametrize("utf8_flag", [True, False])
def test_mxl_rootfile_name_in_nfd_is_found(tmp_path: Path, utf8_flag: bool) -> None:
    # The container names the score in NFC; macOS tools may store the entry in NFD.
    nfc = "\u304c\u00e9.musicxml"  # が and é, precomposed
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc
    score = (FIXTURES / "valid-partwise.musicxml").read_bytes()
    if utf8_flag:
        entries: dict[str, bytes | str] = {nfd: score}
    else:
        placeholder = "Q" * len(nfd.encode())
        entries = {placeholder: score}
    entries["META-INF/container.xml"] = CONTAINER_XML.format(path=nfc)
    target = make_mxl(tmp_path / "song.mxl", entries)
    if not utf8_flag:
        data = target.read_bytes()
        target.write_bytes(data.replace(placeholder.encode(), nfd.encode()))

    document = load_musicxml(target)
    assert document.archive_member == nfc
    assert document.warnings == ()


def test_mxl_with_invalid_utf8_name_in_local_header(tmp_path: Path) -> None:
    # The central directory name is plain ASCII, but the local header sets the UTF-8
    # flag on bytes that are not UTF-8; zipfile raises UnicodeDecodeError on open().
    target = tmp_path / "local.mxl"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("score.xml", (FIXTURES / "valid-partwise.musicxml").read_bytes())
    data = bytearray(target.read_bytes())
    assert data[:4] == b"PK\x03\x04"
    data[7] |= 0x08  # high byte of the little-endian flags: bit 11 is the UTF-8 flag
    data[30] = 0xFF  # first byte of the local header's file name
    target.write_bytes(bytes(data))

    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"
    assert excinfo.value.member == "score.xml"


def _with_zip64_header_offset(data: bytes, member: str, offset: int) -> bytes:
    """Point ``member``'s central-directory entry at ``offset`` via a ZIP64 extra field."""
    eocd = data.rindex(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<II", data, eocd + 12)
    directory = data[cd_offset : cd_offset + cd_size]
    rebuilt = b""
    pos = 0
    while pos < len(directory):
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", directory, pos + 28)
        header = bytearray(directory[pos : pos + 46])
        name = directory[pos + 46 : pos + 46 + name_len]
        extra = directory[pos + 46 + name_len : pos + 46 + name_len + extra_len]
        end = pos + 46 + name_len + extra_len + comment_len
        comment = directory[pos + 46 + name_len + extra_len : end]
        if name.decode() == member:
            extra += struct.pack("<HHQ", 1, 8, offset)
            struct.pack_into("<H", header, 30, len(extra))
            struct.pack_into("<I", header, 42, 0xFFFFFFFF)
        rebuilt += bytes(header) + name + extra + comment
        pos = end
    tail = bytearray(data[eocd:])
    struct.pack_into("<I", tail, 12, len(rebuilt))
    return data[:cd_offset] + rebuilt + bytes(tail)


@pytest.mark.parametrize("offset", [2**64 - 1, 2**40])
def test_mxl_with_out_of_range_zip64_offset(tmp_path: Path, offset: int) -> None:
    target = make_mxl(
        tmp_path / "offset.mxl",
        {"score.xml": (FIXTURES / "valid-partwise.musicxml").read_bytes()},
    )
    target.write_bytes(_with_zip64_header_offset(target.read_bytes(), "score.xml", offset))

    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"
    assert excinfo.value.member == "score.xml"


def test_mxl_with_out_of_range_container_offset_falls_back(tmp_path: Path) -> None:
    target = make_mxl(
        tmp_path / "offset.mxl",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path="score.xml"),
            "score.xml": (FIXTURES / "valid-partwise.musicxml").read_bytes(),
        },
    )
    data = _with_zip64_header_offset(target.read_bytes(), "META-INF/container.xml", 2**64 - 1)
    target.write_bytes(data)

    document = load_musicxml(target)
    assert document.archive_member == "score.xml"
    assert warning_codes(document) == ["musicxml_mxl_container_invalid"]


def test_mxl_with_damaged_container_entry_falls_back(tmp_path: Path) -> None:
    target = tmp_path / "damaged-container.mxl"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", CONTAINER_XML.format(path="score.xml"))
        archive.writestr("score.xml", (FIXTURES / "valid-partwise.musicxml").read_bytes())
    data = bytearray(target.read_bytes())
    with zipfile.ZipFile(target) as archive:
        info = archive.getinfo("META-INF/container.xml")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for offset in range(start, start + info.compress_size):
        data[offset] ^= 0xFF
    target.write_bytes(bytes(data))

    document = load_musicxml(target)
    assert document.archive_member == "score.xml"
    assert warning_codes(document) == ["musicxml_mxl_container_invalid"]
    assert "could not be read" in document.warnings[0].message


def test_mxl_member_errors_use_the_container_name(tmp_path: Path) -> None:
    # The entry is matched through its legacy-encoded name; errors must still use the
    # readable name from container.xml, as archive_member does on success.
    name = "月亮代表我的心.musicxml"
    placeholder = "Q" * len(name.encode())
    target = make_mxl(
        tmp_path / "song.mxl",
        {
            "META-INF/container.xml": CONTAINER_XML.format(path=name),
            placeholder: (FIXTURES / "valid-partwise.musicxml").read_bytes(),
        },
    )
    data = bytearray(target.read_bytes().replace(placeholder.encode(), name.encode()))
    with zipfile.ZipFile(target) as archive:
        info = archive.getinfo(placeholder)
    start = info.header_offset + 30 + len(name.encode()) + len(info.extra)
    for offset in range(start + 8, start + min(info.compress_size, 64)):
        data[offset] ^= 0xFF  # damage only the compressed stream
    target.write_bytes(bytes(data))

    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"
    assert excinfo.value.member == name
    assert name in str(excinfo.value)


@pytest.mark.parametrize("other", ["meta-inf/manifest.xml", "a-layout.xml"])
def test_mxl_fallback_skips_lowercase_meta_inf_and_prefers_musicxml(
    tmp_path: Path, other: str
) -> None:
    target = make_mxl(
        tmp_path / "song.mxl",
        {
            other: CONTAINER_XML.format(path="score.musicxml"),
            "score.musicxml": (FIXTURES / "valid-partwise.musicxml").read_bytes(),
        },
    )
    document = load_musicxml(target)
    assert document.archive_member == "score.musicxml"
    assert warning_codes(document) == ["musicxml_mxl_container_missing"]


def test_mxl_with_invalid_utf8_entry_name(tmp_path: Path) -> None:
    target = tmp_path / "names.mxl"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("s\u00e9.xml", "<x/>")  # non-ASCII name sets the UTF-8 flag
    data = target.read_bytes()
    assert data.count("\u00e9".encode()) == 2  # local and central headers
    target.write_bytes(data.replace("\u00e9".encode(), b"\xff\xa9"))

    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_invalid"


def test_inaccessible_path_is_unreadable_not_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # E.g. a parent directory without search permission: stat() fails with EACCES.
    target = tmp_path / "score.musicxml"
    target.write_bytes((FIXTURES / "minimal-partwise.xml").read_bytes())

    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target)
    assert excinfo.value.code == "musicxml_unreadable"


def test_mxl_fallback_skips_macos_appledouble_entries(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "zipped-on-mac.mxl",
        {
            "__MACOSX/song/._score.musicxml": b"\x00\x05\x16\x07 resource fork",
            "song/._score.musicxml": b"\x00\x05\x16\x07 resource fork",
            "song/score.musicxml": (FIXTURES / "minimal-partwise.xml").read_bytes(),
        },
    )
    document = load_musicxml(archive)
    assert document.archive_member == "song/score.musicxml"
    assert warning_codes(document) == ["musicxml_mxl_container_missing"]


@pytest.mark.parametrize("encoding", ["Big5", "GBK", "x-no-such-encoding"])
def test_unsupported_declared_encoding_is_reported(encoding: str) -> None:
    data = f'<?xml version="1.0" encoding="{encoding}"?><score-partwise/>'.encode("ascii")
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert excinfo.value.code == "musicxml_unsupported"
    assert "UTF-8" in str(excinfo.value)


def test_mxl_member_over_size_limit(tmp_path: Path) -> None:
    score = (FIXTURES / "valid-partwise.musicxml").read_bytes()
    archive = make_mxl(
        tmp_path / "score.mxl",
        {"score.xml": score + b"<!--" + b" " * 50_000 + b"-->"},
    )
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive, max_bytes=20_000)
    assert excinfo.value.code == "musicxml_too_large"
    assert excinfo.value.member == "score.xml"


def test_metadata_to_dict_returns_lists_not_tuples() -> None:
    meta = load_musicxml(FIXTURES / "valid-partwise.musicxml").metadata
    data = meta.to_dict()
    assert isinstance(data["parts"], list)
    assert isinstance(data["creators"], list)
    assert data == json.loads(json.dumps(data, ensure_ascii=False))


def test_file_growing_after_stat_is_still_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "grows.musicxml"
    target.write_bytes(b"<a/>")
    real_stat = Path.stat

    def small_stat(self: Path, *args: object, **kwargs: object) -> object:
        result = real_stat(self, *args, **kwargs)
        if self == target:
            target.write_bytes(b"x" * 100)  # grows between stat() and read
        return result

    monkeypatch.setattr(Path, "stat", small_stat)
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(target, max_bytes=10)
    assert excinfo.value.code == "musicxml_too_large"


# Element limit: max_bytes bounds input bytes, not the parsed tree


def _flat_score(children: int) -> bytes:
    return (
        b"<score-partwise><part-list><score-part id='P1'><part-name>x</part-name>"
        b"</score-part></part-list><part id='P1'>"
        + b"<x/>" * children
        + b"</part></score-partwise>"
    )


def test_element_limit_counts_every_element() -> None:
    # score-partwise, part-list, score-part, part-name, part: 5 elements plus children.
    assert len(parse_xml_bytes(_flat_score(5), max_elements=10).root) == 2
    with pytest.raises(TooManyElementsError) as excinfo:
        parse_xml_bytes(_flat_score(6), max_elements=10)
    assert excinfo.value.line == 1


def test_parse_bytes_too_many_elements_is_too_large() -> None:
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(_flat_score(100), max_elements=50)
    assert excinfo.value.code == "musicxml_too_large"
    assert "max_elements" in str(excinfo.value)


def test_element_limit_is_on_by_default() -> None:
    for function in (load_musicxml, parse_musicxml_bytes, parse_xml_bytes):
        default = inspect.signature(function).parameters["max_elements"].default
        assert default == DEFAULT_MAX_ELEMENTS


def test_mxl_element_bomb_is_refused(tmp_path: Path) -> None:
    # Repeated <x/> compresses about 1000:1, so a small .mxl stays far below max_bytes
    # while its tree would not. Parsing must stop at the element limit.
    data = _flat_score(200_000)
    archive = make_mxl(tmp_path / "bomb.mxl", {"score.musicxml": data})
    assert archive.stat().st_size * 100 < len(data)
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(archive, max_elements=1_000)
    assert excinfo.value.code == "musicxml_too_large"
    assert excinfo.value.member == "score.musicxml"


def test_load_musicxml_passes_element_limit(tmp_path: Path) -> None:
    score = tmp_path / "score.musicxml"
    score.write_bytes(_flat_score(10))
    assert load_musicxml(score, max_elements=15).root.tag == "score-partwise"
    with pytest.raises(MusicXmlError) as excinfo:
        load_musicxml(score, max_elements=14)
    assert excinfo.value.code == "musicxml_too_large"


def test_container_uses_the_callers_element_limit(tmp_path: Path) -> None:
    # A valid rootfile, so only the element limit can make the loader fall back.
    container = (
        "<container><rootfiles><rootfile full-path='score.xml'/>"
        + "<x/>" * 20
        + "</rootfiles></container>"
    )
    entries = {
        "META-INF/container.xml": container,
        "score.xml": MINIMAL_BODY.format(name="Main"),
        "fallback.musicxml": MINIMAL_BODY.format(name="Fallback"),
    }
    archive = make_mxl(tmp_path / "score.mxl", entries)
    assert load_musicxml(archive, max_elements=30).archive_member == "score.xml"

    document = load_musicxml(archive, max_elements=10)
    assert document.archive_member == "fallback.musicxml"
    assert warning_codes(document) == ["musicxml_mxl_container_invalid"]
    assert "More than 10 elements" in document.warnings[0].message


def test_lowercase_container_path_is_used(tmp_path: Path) -> None:
    archive = make_mxl(
        tmp_path / "score.mxl",
        {
            "meta-inf/container.xml": CONTAINER_XML.format(path="score.xml"),
            "score.xml": MINIMAL_BODY.format(name="Main"),
            "notes.musicxml": MINIMAL_BODY.format(name="Sidecar"),
        },
    )
    document = load_musicxml(archive)
    assert document.archive_member == "score.xml"
    assert warning_codes(document) == []


def test_attribute_default_error_gives_its_own_reason() -> None:
    data = (
        "<!DOCTYPE score-partwise [<!ATTLIST part-name print-object CDATA 'no'>]>"
        + MINIMAL_BODY.format(name="A")
    ).encode()
    with pytest.raises(MusicXmlError) as excinfo:
        parse_musicxml_bytes(data)
    assert "attribute defaults are never applied" in str(excinfo.value)
    assert "XXE" not in str(excinfo.value)
