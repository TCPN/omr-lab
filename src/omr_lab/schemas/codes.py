"""Error and warning codes of the run document (the code table in docs/json-schema.md).

They live next to the schema, not in the pipeline, so any module that records a
run failure (for example MusicXML validation) can use them without importing the
pipeline. The schemas check only the snake_case pattern; a unit test keeps
these enums and the documented table in step.
"""

from __future__ import annotations

from enum import StrEnum


class RunErrorCode(StrEnum):
    """``error.code`` values of a run document (docs/json-schema.md)."""

    INVALID_INPUT = "invalid_input"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ENGINE_FAILED = "engine_failed"
    INVALID_OPTION = "invalid_option"
    ENGINE_TIMEOUT = "engine_timeout"
    """Reserved: not produced until EngineResult can report a timeout (issue #48)."""
    MUSICXML_INVALID = "musicxml_invalid"
    PREPROCESSING_FAILED = "preprocessing_failed"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class RunWarningCode(StrEnum):
    """``warnings[].code`` values of a run document (docs/json-schema.md)."""

    SCORE_NOT_NORMALIZED = "score_not_normalized"
    """No longer produced: a succeeded run always has a score (issue #50). Kept for old records."""
    ARTIFACT_NOT_RECORDED = "artifact_not_recorded"
    ENGINE_DIAGNOSTIC_NOT_RECORDED = "engine_diagnostic_not_recorded"
    UNEXPECTED_ERROR = "unexpected_error"
    RUN_DOCUMENT_INCOMPLETE = "run_document_incomplete"
    SCORE_UNSUPPORTED_ELEMENT = "score_unsupported_element"
    SCORE_INVALID_VALUE = "score_invalid_value"
    SCORE_DIVISIONS_MISSING = "score_divisions_missing"
    SCORE_TIMING_INCONSISTENT = "score_timing_inconsistent"
    # Warnings of the MusicXML loader (omr_lab.musicxml.WarningCode), copied as they are.
    MUSICXML_UNKNOWN_ELEMENT = "musicxml_unknown_element"
    MUSICXML_UNRESOLVED_ENTITY = "musicxml_unresolved_entity"
    MUSICXML_PROCESSING_INSTRUCTIONS_DROPPED = "musicxml_processing_instructions_dropped"
    MUSICXML_XLINK_NAMESPACE_ASSUMED = "musicxml_xlink_namespace_assumed"
    MUSICXML_PART_MISSING = "musicxml_part_missing"
    MUSICXML_PART_UNDECLARED = "musicxml_part_undeclared"
    MUSICXML_PART_WITHOUT_ID = "musicxml_part_without_id"
    MUSICXML_DUPLICATE_PART = "musicxml_duplicate_part"
    MUSICXML_MXL_CONTAINER_MISSING = "musicxml_mxl_container_missing"
    MUSICXML_MXL_CONTAINER_INVALID = "musicxml_mxl_container_invalid"


__all__ = ["RunErrorCode", "RunWarningCode"]
