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
    """Reserved for MusicXML validation in the pipeline; not produced yet."""
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class RunWarningCode(StrEnum):
    """``warnings[].code`` values of a run document (docs/json-schema.md)."""

    SCORE_NOT_NORMALIZED = "score_not_normalized"
    ARTIFACT_NOT_RECORDED = "artifact_not_recorded"
    ENGINE_DIAGNOSTIC_NOT_RECORDED = "engine_diagnostic_not_recorded"
    UNEXPECTED_ERROR = "unexpected_error"
    RUN_DOCUMENT_INCOMPLETE = "run_document_incomplete"
    SCORE_UNSUPPORTED_ELEMENT = "score_unsupported_element"
    SCORE_INVALID_VALUE = "score_invalid_value"
    SCORE_DIVISIONS_MISSING = "score_divisions_missing"
    SCORE_TIMING_INCONSISTENT = "score_timing_inconsistent"


__all__ = ["RunErrorCode", "RunWarningCode"]
