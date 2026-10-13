"""Checked-in JSON Schemas for OMR Lab documents and helpers to validate against them.

The run document schema is described in docs/json-schema.md.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

RUN_SCHEMA_VERSION = "0.1.0"
"""Version of the run document schema produced by this package."""

_RUN_SCHEMA_RESOURCE = f"run-{RUN_SCHEMA_VERSION}.schema.json"


class RunDocumentValidationError(ValueError):
    """Raised when a run document does not conform to the run schema.

    ``errors`` lists one human-readable message per violation, each prefixed with
    the JSON path of the offending value.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "; ".join(errors)
        super().__init__(f"Run document is invalid ({len(errors)} error(s)): {summary}")


def load_run_schema() -> dict[str, Any]:
    """Return a fresh copy of the packaged run document JSON Schema.

    Not cached, so a caller that mutates the result cannot change the schema
    used by validate_run_document().
    """
    text = files(__name__).joinpath(_RUN_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


@cache
def _run_validator() -> Draft202012Validator:
    schema = load_run_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_run_document(document: Any) -> None:
    """Validate a decoded JSON run document against the run schema.

    Raises RunDocumentValidationError listing every violation. Format keywords
    (such as ``date-time``) are annotations only and are not enforced.
    """
    errors = sorted(
        _run_validator().iter_errors(document),
        key=lambda error: (error.json_path, error.message),
    )
    if errors:
        raise RunDocumentValidationError(
            [f"{error.json_path}: {error.message}" for error in errors]
        )


__all__ = [
    "RUN_SCHEMA_VERSION",
    "RunDocumentValidationError",
    "load_run_schema",
    "validate_run_document",
]
