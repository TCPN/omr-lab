"""Checked-in JSON Schemas for OMR Lab documents and helpers to validate against them.

The run document schema is described in docs/json-schema.md.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from omr_lab.schemas.codes import RunErrorCode, RunWarningCode

CURRENT_RUN_SCHEMA_VERSION = "0.2.0"
"""Version of the run document schema that this package writes."""

SUPPORTED_RUN_SCHEMA_VERSIONS: tuple[str, ...] = ("0.1.0", "0.2.0")
"""Run document schema versions that can be validated; older files stay packaged."""


class RunDocumentValidationError(ValueError):
    """Raised when a run document does not conform to the run schema.

    ``errors`` lists one human-readable message per violation, each prefixed with
    the JSON path of the offending value.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "; ".join(errors)
        super().__init__(f"Run document is invalid ({len(errors)} error(s)): {summary}")


def _check_supported(version: str) -> None:
    if version not in SUPPORTED_RUN_SCHEMA_VERSIONS:
        supported = ", ".join(SUPPORTED_RUN_SCHEMA_VERSIONS)
        raise ValueError(
            f"Unsupported run schema version {version!r}; supported versions: {supported}."
        )


def load_run_schema(version: str = CURRENT_RUN_SCHEMA_VERSION) -> dict[str, Any]:
    """Return a fresh copy of the packaged run document JSON Schema for ``version``.

    Not cached, so a caller that mutates the result cannot change the schema
    used by validate_run_document(). Raises ValueError for an unsupported version.
    """
    _check_supported(version)
    resource = files(__name__).joinpath(f"run-{version}.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


@cache
def _run_validator_for(version: str) -> Draft202012Validator:
    schema = load_run_schema(version)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _path_sort_key(error: ValidationError) -> tuple[list[tuple[int, int, str]], str]:
    # absolute_path mixes array indexes (int) and object keys (str); tag each part so
    # indexes compare numerically ([2] before [10]) and never against a key.
    parts = [
        (0, part, "") if isinstance(part, int) else (1, 0, str(part))
        for part in error.absolute_path
    ]
    return parts, error.message


def _message(error: ValidationError) -> str:
    # Subschemas that carry a "description" (such as the artifact path rules) explain
    # the rule in words instead of echoing a regular expression.
    if error.validator == "not" and isinstance(error.schema, dict):
        description = error.schema.get("description")
        if description:
            return f"{error.instance!r}: {description}"
    return error.message


@cache
def _artifact_path_validator(version: str) -> Draft202012Validator:
    schema = load_run_schema(version)
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/relativePosixPath"})


def artifact_path_violation(path: str, *, version: str = CURRENT_RUN_SCHEMA_VERSION) -> str | None:
    """Return the first artifact path rule ``path`` breaks, e.g. "must not ...", or None.

    The rules are the schema's ``relativePosixPath`` definition, so callers that check
    a path before writing it (the pipeline) cannot drift from the schema. Among the
    path-pattern rules, the control-character rule is checked first.
    """
    _check_supported(version)
    error = next(_artifact_path_validator(version).iter_errors(path), None)
    if error is None:
        return None
    # Only the "not" rules carry a rule description; for type or minLength errors the
    # schema's description would describe the definition, not the broken rule.
    if error.validator == "not":
        return error.schema.get("description") or error.message
    return error.message


def validate_run_document(document: Any, *, version: str | None = None) -> None:
    """Validate a decoded JSON run document against the run schema.

    With ``version=None`` the document's own ``schema_version`` selects the schema
    when it is a supported version; otherwise the current schema is used, which
    reports the missing or unknown ``schema_version`` as a violation. An explicit
    unsupported ``version`` raises ValueError.

    Raises RunDocumentValidationError listing every violation, ordered by location.
    Format keywords (such as ``date-time``) are annotations only and are not enforced.
    """
    if version is None:
        declared = document.get("schema_version") if isinstance(document, dict) else None
        version = (
            declared if declared in SUPPORTED_RUN_SCHEMA_VERSIONS else CURRENT_RUN_SCHEMA_VERSION
        )
    else:
        _check_supported(version)
    errors = sorted(_run_validator_for(version).iter_errors(document), key=_path_sort_key)
    if errors:
        raise RunDocumentValidationError(
            [f"{error.json_path}: {_message(error)}" for error in errors]
        )


__all__ = [
    "CURRENT_RUN_SCHEMA_VERSION",
    "SUPPORTED_RUN_SCHEMA_VERSIONS",
    "RunDocumentValidationError",
    "RunErrorCode",
    "RunWarningCode",
    "artifact_path_violation",
    "load_run_schema",
    "validate_run_document",
]
