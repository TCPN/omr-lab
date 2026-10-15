# MusicXML Loading and Validation

`omr_lab.musicxml` loads a MusicXML artifact written by an engine, checks that it is a
readable, well-formed `score-partwise` document, and extracts the metadata it declares.
It is engine-independent and does not interpret notes, lyrics, or harmony; mapping the
musical content to the normalized score model is issue #19.

**A document that loads without errors is not evidence of correct recognition.** It only
means the file is parseable and structurally plausible MusicXML. Wrong pitches, missing
measures, or garbled lyrics all load fine. Recognition accuracy is measured separately
(see `docs/benchmark-protocol.md`).

## API

```python
from omr_lab.musicxml import MusicXmlError, load_musicxml

try:
    document = load_musicxml("runs/001/score.mxl")
except MusicXmlError as error:
    print(error.code, error.message, error.line, error.column)
else:
    print(document.metadata.to_dict())
    for warning in document.warnings:
        print(warning.code, warning.message)
```

- `load_musicxml(path, *, max_bytes=DEFAULT_MAX_BYTES) -> MusicXmlDocument`
- `parse_musicxml_bytes(data, *, path=None) -> MusicXmlDocument` for in-memory data.
- `MusicXmlDocument`: `path`, `root` (the full parsed `ElementTree.Element`, unmodified),
  `metadata`, `container` (`"musicxml"` or `"mxl"`), `archive_member` (score entry inside
  an `.mxl`), `doctype_public_id`, and `warnings`.
- `MusicXmlMetadata`: `version`, `work_title`, `work_number`, `movement_title`,
  `movement_number`, `creators` (`Creator(name, type)`), `rights`, `encoding_software`,
  `encoding_date`, and `parts` (`PartInfo(id, name, abbreviation, measure_count)`).
  `to_dict()` returns a JSON-serializable dict.

Metadata is reported only where present. Absent or empty elements become `None` or an
empty tuple; nothing is guessed. For example, a missing `version` attribute is `None`
even though the MusicXML specification defines the default as 1.0, and a part declared
in `<part-list>` without a `<part>` element has `measure_count=None`. Text is stripped of
surrounding whitespace.

## Input formats

- Uncompressed `.musicxml` / `.xml`: any encoding expat supports via the XML declaration
  or BOM (UTF-8, UTF-16, ...).
- Compressed `.mxl`: detected by the zip signature, so an `.mxl` with the wrong extension
  still loads. The score is the first `<rootfile full-path>` in `META-INF/container.xml`.
  If the container is absent or unusable, the loader falls back to the first `.xml` /
  `.musicxml` entry outside `META-INF/` (sorted by name) and warns.
- `max_bytes` (default 64 MiB) caps the file size and the decompressed size of each
  archive entry read, which bounds memory use for zip bombs.
- An `.mxl` that may declare more than 10,000 entries (counted by central-directory
  entry signatures) is refused before `zipfile` opens it, because `zipfile` creates an
  object per entry up front; a MusicXML archive has only a few entries.
- XML is parsed with namespace processing, as ElementTree does: an unbound prefix is a
  syntax error, and namespaced names use Clark notation (`{http://www.w3.org/1999/xlink}href`).
  MusicXML score elements are not in a namespace, so their tags stay plain.

## Errors

`MusicXmlError` carries a stable snake_case `code`, an actionable `message`, and, when
known, `path`, `member` (archive entry), and 1-based `line`/`column`.

| Code | Meaning |
| --- | --- |
| `musicxml_missing` | Path does not exist or is not a regular file. |
| `musicxml_unreadable` | File could not be read (permissions, I/O), exceeds `max_bytes`, or is an `.mxl` with too many entries. |
| `musicxml_invalid` | Not well-formed XML, including unbound namespace prefixes (with line/column), root is not a MusicXML element, corrupt/empty/encrypted `.mxl`, container rootfile missing from the archive, or no `<part-list>` / `<part>`. |
| `musicxml_unsupported` | Well-formed but not handled: `score-timewise`, `opus`, or DTD entity declarations. |

The run document (issue #5) currently lists only `musicxml_invalid`; the pipeline
(issue #6) decides whether to record these finer codes or map them to it.

## Warnings

Non-fatal problems are returned as `MusicXmlWarning(code, message)`, the same shape as
run-document warnings. Constructs are warned about rather than silently dropped, and the
full tree stays available in `document.root`.

| Code | Meaning |
| --- | --- |
| `musicxml_unknown_element` | A direct child of `<score-partwise>` not in the MusicXML 4.0 schema (kept in the tree). |
| `musicxml_unresolved_entity` | An entity reference (e.g. `&nbsp;`) not defined in the document; its text is omitted because external DTDs are never loaded. |
| `musicxml_part_missing` | A `<score-part>` with no matching `<part>`. |
| `musicxml_part_undeclared` | A `<part>` not declared in `<part-list>`. |
| `musicxml_part_without_id` | A `<part>` or `<score-part>` without an `id`. |
| `musicxml_duplicate_part` | A part id declared or used more than once (first one wins). |
| `musicxml_mxl_container_missing` | `.mxl` without `META-INF/container.xml`; fallback entry used. |
| `musicxml_mxl_container_invalid` | `container.xml` unparseable or without a rootfile; fallback entry used. |

Only the top level is checked for unknown elements. Element-level coverage of notes,
lyrics, and harmony belongs to the score model conversion (issue #19).

## XML safety

Engine output is untrusted input. The loader uses only the standard library: it drives
`pyexpat` directly and builds an `ElementTree` (see `src/omr_lab/musicxml/safe_xml.py`).

- The DTD named in `<!DOCTYPE score-partwise PUBLIC ... "http://www.musicxml.org/...">`
  is never loaded or fetched: parameter-entity parsing is disabled
  (`XML_PARAM_ENTITY_PARSING_NEVER`), and expat performs no network I/O.
- Any entity declaration (internal, external, parameter, or unparsed) is refused with
  `musicxml_unsupported`, which blocks external-entity file reads (XXE) and entity
  expansion ("billion laughs"). An external-entity handler that raises is installed as
  a second guard. MusicXML does not need entity declarations.
- `META-INF/container.xml` inside an `.mxl` is parsed with the same parser. Archive
  entries are read into memory by name and never extracted to disk.

`defusedxml` was not added: the stdlib parser configured as above covers the same
attacks for this use, keeping the runtime dependency list empty.

## Limitations

- No XSD/DTD schema validation; only the structure listed above is checked.
- `score-timewise` is rejected rather than converted.
- Only the first rootfile of an `.mxl` is loaded.
- Element `sourceline` information is not kept on the tree (only for top-level warnings).
