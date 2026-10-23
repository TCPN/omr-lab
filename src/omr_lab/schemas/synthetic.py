"""JSON Schema for synthetic corpus specifications (docs/synthetic-corpus.md)."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from omr_lab.schemas import _message, _path_sort_key

SYNTHETIC_CORPUS_SCHEMA_VERSION = "0.1.0"
"""Version of the synthetic corpus specification schema that this package reads."""


class SyntheticCorpusSpecValidationError(ValueError):
    """A corpus specification does not conform to its schema.

    ``errors`` lists one message per violation, prefixed with the JSON path.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        summary = "; ".join(errors)
        super().__init__(f"Corpus specification is invalid ({len(errors)} error(s)): {summary}")


def load_synthetic_corpus_schema() -> dict[str, Any]:
    resource = files("omr_lab.schemas").joinpath(
        f"synthetic-corpus-{SYNTHETIC_CORPUS_SCHEMA_VERSION}.schema.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


@cache
def _validator() -> Draft202012Validator:
    schema = load_synthetic_corpus_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_synthetic_corpus_spec(document: Any) -> None:
    """Validate a decoded specification; raise SyntheticCorpusSpecValidationError.

    Only the schema is checked here; ``omr_lab.synthetic.load_corpus_spec`` also checks
    unique ids and each degradation's parameter names and ranges.
    """
    errors = sorted(_validator().iter_errors(document), key=_path_sort_key)
    if errors:
        raise SyntheticCorpusSpecValidationError(
            [f"{error.json_path}: {_message(error)}" for error in errors]
        )


__all__ = [
    "SYNTHETIC_CORPUS_SCHEMA_VERSION",
    "SyntheticCorpusSpecValidationError",
    "load_synthetic_corpus_schema",
    "validate_synthetic_corpus_spec",
]
