"""JSON Schemas for benchmark manifests and reports (docs/benchmark.md)."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from omr_lab.schemas import _message, _path_sort_key

BENCHMARK_MANIFEST_SCHEMA_VERSION = "0.2.0"
"""Version of the benchmark manifest schema that this package writes (``synthesize``)."""

SUPPORTED_BENCHMARK_MANIFEST_SCHEMA_VERSIONS: tuple[str, ...] = ("0.1.0", "0.2.0")
"""Manifest schema versions that this package reads."""

BENCHMARK_REPORT_SCHEMA_VERSION = "0.1.0"
"""Version of the benchmark report schema that this package writes."""


class BenchmarkDocumentValidationError(ValueError):
    """A manifest or report does not conform to its schema.

    ``errors`` lists one message per violation, prefixed with the JSON path.
    """

    def __init__(self, kind: str, errors: list[str]) -> None:
        self.errors = errors
        summary = "; ".join(errors)
        super().__init__(f"Benchmark {kind} is invalid ({len(errors)} error(s)): {summary}")


def _load(name: str, version: str) -> dict[str, Any]:
    resource = files("omr_lab.schemas").joinpath(f"{name}-{version}.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def load_benchmark_manifest_schema(
    version: str = BENCHMARK_MANIFEST_SCHEMA_VERSION,
) -> dict[str, Any]:
    if version not in SUPPORTED_BENCHMARK_MANIFEST_SCHEMA_VERSIONS:
        supported = ", ".join(SUPPORTED_BENCHMARK_MANIFEST_SCHEMA_VERSIONS)
        raise ValueError(
            f"Unsupported benchmark manifest schema version {version!r}; "
            f"supported versions: {supported}."
        )
    return _load("benchmark-manifest", version)


def load_benchmark_report_schema() -> dict[str, Any]:
    return _load("benchmark-report", BENCHMARK_REPORT_SCHEMA_VERSION)


@cache
def _validator(name: str, version: str) -> Draft202012Validator:
    schema = _load(name, version)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate(name: str, version: str, kind: str, document: Any) -> None:
    errors = sorted(_validator(name, version).iter_errors(document), key=_path_sort_key)
    if errors:
        raise BenchmarkDocumentValidationError(
            kind, [f"{error.json_path}: {_message(error)}" for error in errors]
        )


def validate_benchmark_manifest(document: Any) -> None:
    """Validate a decoded manifest; raise BenchmarkDocumentValidationError.

    The schema is chosen by the document's ``schema_version`` when it is a supported
    version, and is the current one otherwise (which then reports the version).
    Only the schema is checked here; unique sample ids are checked by
    ``omr_lab.benchmark.load_manifest``.
    """
    version = document.get("schema_version") if isinstance(document, dict) else None
    if version not in SUPPORTED_BENCHMARK_MANIFEST_SCHEMA_VERSIONS:
        version = BENCHMARK_MANIFEST_SCHEMA_VERSION
    _validate("benchmark-manifest", version, "manifest", document)


def validate_benchmark_report(document: Any) -> None:
    """Validate a decoded report; raise BenchmarkDocumentValidationError."""
    _validate("benchmark-report", BENCHMARK_REPORT_SCHEMA_VERSION, "report", document)


__all__ = [
    "BENCHMARK_MANIFEST_SCHEMA_VERSION",
    "BENCHMARK_REPORT_SCHEMA_VERSION",
    "SUPPORTED_BENCHMARK_MANIFEST_SCHEMA_VERSIONS",
    "BenchmarkDocumentValidationError",
    "load_benchmark_manifest_schema",
    "load_benchmark_report_schema",
    "validate_benchmark_manifest",
    "validate_benchmark_report",
]
