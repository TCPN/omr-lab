import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from omr_lab.schemas import (
    CURRENT_RUN_SCHEMA_VERSION,
    SUPPORTED_RUN_SCHEMA_VERSIONS,
    RunDocumentValidationError,
    load_run_schema,
    validate_run_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "run-documents"
VALID = sorted((FIXTURES / "valid").glob("*.json"))

# Each invalid fixture contains exactly one violation, reported at this JSON path.
INVALID_EXPECTED_PATH = {
    "missing-schema-version": "$",
    "wrong-schema-version": "$.schema_version",
    "missing-engine": "$",
    "missing-engine-version": "$.engine",
    "empty-engine-name": "$.engine.name",
    "unknown-status": "$.status",
    "failed-with-null-error": "$.error",
    "succeeded-with-error": "$.error",
    "error-missing-message": "$.error",
    "missing-score": "$",
    "fabricated-score-content": "$.score",
    "missing-options": "$",
    "absolute-artifact-path": "$.artifacts.musicxml",
    "parent-artifact-path": "$.artifacts.musicxml",
    "trailing-newline-artifact-path": "$.artifacts.musicxml",
    "negative-duration": "$.timing.duration_seconds",
    "warning-as-plain-string": "$.warnings[0]",
    "input-filename-with-directory": "$.input.filename",
    "unknown-top-level-field": "$",
}


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(load_run_schema())


def test_schema_version_matches_constant():
    schema = load_run_schema()
    assert schema["properties"]["schema_version"]["const"] == CURRENT_RUN_SCHEMA_VERSION
    assert schema["$id"].endswith(CURRENT_RUN_SCHEMA_VERSION)


def test_invalid_fixture_set_matches_expectations():
    names = {path.stem for path in (FIXTURES / "invalid").glob("*.json")}
    assert names == set(INVALID_EXPECTED_PATH)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_documents_pass(path):
    validate_run_document(_load(path))


@pytest.mark.parametrize("name", sorted(INVALID_EXPECTED_PATH))
def test_invalid_documents_fail(name):
    document = _load(FIXTURES / "invalid" / f"{name}.json")
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(document)
    paths = [message.split(": ", 1)[0] for message in excinfo.value.errors]
    assert paths == [INVALID_EXPECTED_PATH[name]], excinfo.value.errors


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_documents_carry_version_and_engine_provenance(path):
    document = _load(path)
    assert document["schema_version"] == CURRENT_RUN_SCHEMA_VERSION
    assert document["engine"]["name"]
    assert "version" in document["engine"]


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_missing_musical_content_is_explicit_null(path):
    document = _load(path)
    assert "score" in document
    assert document["score"] is None


def test_non_object_document_is_rejected():
    with pytest.raises(RunDocumentValidationError):
        validate_run_document([])


def test_error_lists_every_violation():
    document = _load(FIXTURES / "valid" / "succeeded-minimal.json")
    del document["engine"]
    document["status"] = "unknown"
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(document)
    assert len(excinfo.value.errors) >= 2
    assert isinstance(excinfo.value, ValueError)


def test_docs_example_validates():
    text = (REPO_ROOT / "docs" / "json-schema.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    assert blocks, "docs/json-schema.md should contain JSON examples"
    for block in blocks:
        validate_run_document(json.loads(block))


def test_malformed_error_object_reports_each_violation():
    document = _load(FIXTURES / "valid" / "failed-engine-exit.json")
    document["error"] = {"code": "Not Snake", "unexpected": True}
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(document)
    assert len(excinfo.value.errors) == 3, excinfo.value.errors
    assert all(message.startswith("$.error") for message in excinfo.value.errors)


def test_load_run_schema_returns_independent_copies():
    schema = load_run_schema()
    schema["properties"].clear()
    assert load_run_schema()["properties"]
    validate_run_document(_load(VALID[0]))


def _with_artifact(value: str) -> dict:
    document = _load(FIXTURES / "valid" / "succeeded-minimal.json")
    document["artifacts"] = {"musicxml": value}
    return document


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/abs/result.musicxml", "must be relative"),
        ("C:result.musicxml", "Windows drive prefix"),
        ("raw\\result.musicxml", "separator"),
        ("raw/../result.musicxml", "'..' segment"),
        ("result.musicxml\n", "control characters"),
    ],
)
def test_artifact_path_errors_name_the_broken_rule(value, expected):
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(_with_artifact(value))
    (message,) = excinfo.value.errors
    assert message.startswith("$.artifacts.musicxml: ")
    assert expected in message
    assert "(?" not in message and "^" not in message, "should not echo the regex"


def test_newline_cannot_hide_parent_segment():
    # '.' in a regex does not match a newline; the '..' rule must still apply.
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(_with_artifact("raw\n/../../outside.xml"))
    messages = excinfo.value.errors
    assert any("control characters" in message for message in messages), messages
    assert any("'..' segment" in message for message in messages), messages


def test_errors_are_ordered_by_numeric_array_index():
    document = _load(FIXTURES / "valid" / "succeeded-minimal.json")
    document["warnings"] = [{"code": "ok", "message": "fine"} for _ in range(12)]
    for index in (2, 10):
        document["warnings"][index] = "not an object"
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(document)
    paths = [message.split(": ", 1)[0] for message in excinfo.value.errors]
    assert paths == ["$.warnings[2]", "$.warnings[10]"]


def test_supported_versions_include_current():
    assert CURRENT_RUN_SCHEMA_VERSION in SUPPORTED_RUN_SCHEMA_VERSIONS
    for version in SUPPORTED_RUN_SCHEMA_VERSIONS:
        assert load_run_schema(version)["properties"]["schema_version"]["const"] == version


def test_unsupported_explicit_version_is_rejected():
    document = _load(FIXTURES / "valid" / "succeeded-minimal.json")
    with pytest.raises(ValueError, match="Unsupported run schema version"):
        validate_run_document(document, version="9.9.9")
    with pytest.raises(ValueError, match="Unsupported run schema version"):
        load_run_schema("9.9.9")


def test_explicit_version_validates_against_that_schema():
    document = _load(FIXTURES / "valid" / "succeeded-minimal.json")
    validate_run_document(document, version=CURRENT_RUN_SCHEMA_VERSION)


def test_unknown_document_version_is_reported_as_violation():
    document = _load(FIXTURES / "valid" / "succeeded-minimal.json")
    document["schema_version"] = "9.9.9"
    with pytest.raises(RunDocumentValidationError) as excinfo:
        validate_run_document(document)
    assert [m.split(": ", 1)[0] for m in excinfo.value.errors] == ["$.schema_version"]
