"""Run orchestration: input checks, engine call, artifact mapping, and the run document.

This module only orchestrates. Engine-specific invocation stays in the adapters
under ``omr_lab.engines``; the run document contract is docs/json-schema.md and
the pipeline behavior is described in docs/pipeline.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omr_lab.engines.base import (
    Engine,
    EngineOptions,
    EngineOptionValue,
    EngineResult,
    InvalidEngineOptionError,
)
from omr_lab.schemas import (
    CURRENT_RUN_SCHEMA_VERSION,
    RunDocumentValidationError,
    validate_run_document,
)

logger = logging.getLogger(__name__)

RUN_DOCUMENT_FILENAME = "run.json"
"""Name of the normalized run document inside the run output directory."""

RAW_DIRNAME = "raw"
"""Subdirectory of the run output directory that the engine writes its raw artifacts to."""

DEFAULT_TIMEOUT_SECONDS = 600.0
"""Default engine timeout; real engines can need minutes for a multi-page PDF."""

SUPPORTED_MEDIA_TYPES: Mapping[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
}
"""Accepted input file extensions (case-insensitive) and the media type recorded for each."""

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

_SCORE_NOT_NORMALIZED_MESSAGE = (
    f"Normalized score content is not produced by run schema {CURRENT_RUN_SCHEMA_VERSION}; "
    "the recognized notation is only in the raw artifacts."
)


class OutputDirectoryError(ValueError):
    """The run output directory cannot be used (it is a file or is not empty)."""


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of one pipeline run.

    ``document`` is the validated run document that was written to
    ``document_path``. ``succeeded`` reflects pipeline completion only; it is not
    a claim that the music was recognized correctly.
    """

    document: dict[str, Any]
    document_path: Path
    output_dir: Path

    @property
    def succeeded(self) -> bool:
        return self.document["status"] == "succeeded"


@dataclass(slots=True)
class _RunState:
    """Facts collected during a run; turned into the run document at the end."""

    media_type: str | None = None
    sha256: str | None = None
    engine_version: str | None = None
    options: dict[str, EngineOptionValue] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def fail(self, code: str, message: str, **extra: Any) -> None:
        self.error = {"code": code, "message": message, **extra}

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})


def run_pipeline(
    input_path: Path | str,
    output_dir: Path | str,
    engine: Engine,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    options: EngineOptions | None = None,
) -> PipelineResult:
    """Recognize one input file with ``engine`` and write a validated run document.

    Layout of ``output_dir`` (created if missing; it must not exist or be empty):
    ``raw/`` holds the engine's raw artifacts and ``run.json`` the run document.
    The input file is only read, never modified or copied.

    Run failures (invalid input, unavailable engine, invalid option, engine
    failure, unexpected errors while running the engine) do not raise: they are
    recorded as a failed run document, which is written and returned. Raises
    ValueError for arguments that make a run record impossible (an unusable
    ``output_dir`` raises OutputDirectoryError, a ValueError subclass).
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    engine_name = engine.name
    if not isinstance(engine_name, str) or not engine_name:
        raise ValueError(f"Engine name must be a non-empty string, got {engine_name!r}.")
    filename = _recordable_filename(input_path)
    _prepare_output_dir(output_dir)

    started_at = datetime.now(UTC)
    start = time.perf_counter()
    state = _RunState()
    try:
        _run(state, input_path, output_dir, engine, timeout_seconds, options)
    except Exception as exc:  # Recorded in the run document and logged, not swallowed.
        logger.exception("Pipeline run failed unexpectedly")
        state.fail("internal_error", f"Unexpected {type(exc).__name__} during the run: {exc}")
    if state.error is None:
        state.warn("score_not_normalized", _SCORE_NOT_NORMALIZED_MESSAGE)
    duration = time.perf_counter() - start

    document = _build_document(state, filename, engine_name, started_at, duration)
    try:
        text = _serialize(document)
    except (RunDocumentValidationError, TypeError, ValueError) as exc:
        # A bug in an adapter or in this module produced an invalid record. Write a
        # minimal failed record instead, so the run still leaves a valid trace.
        logger.exception("Run document failed validation; writing a minimal failed record")
        fallback = _RunState(media_type=state.media_type, sha256=state.sha256)
        fallback.fail("internal_error", f"The run document could not be written: {exc}")
        document = _build_document(fallback, filename, engine_name, started_at, duration)
        text = _serialize(document)

    document_path = output_dir / RUN_DOCUMENT_FILENAME
    _write_atomically(document_path, text)
    return PipelineResult(document=document, document_path=document_path, output_dir=output_dir)


def _run(
    state: _RunState,
    input_path: Path,
    output_dir: Path,
    engine: Engine,
    timeout_seconds: float,
    options: EngineOptions | None,
) -> None:
    if not _check_input(state, input_path):
        return

    availability = engine.check_available()
    state.engine_version = availability.version or None
    if not availability.available:
        message = availability.message or f"Engine {engine.name!r} is not available."
        state.fail("engine_unavailable", message)
        return

    try:
        result = engine.recognize(
            input_path,
            output_dir / RAW_DIRNAME,
            timeout_seconds=timeout_seconds,
            options=options,
        )
    except InvalidEngineOptionError as exc:
        state.fail("invalid_option", str(exc))
        return
    _record_result(state, result, engine.name, output_dir)


def _check_input(state: _RunState, input_path: Path) -> bool:
    """Record media type and digest; record an invalid_input error and return False on failure."""
    state.media_type = SUPPORTED_MEDIA_TYPES.get(input_path.suffix.lower())
    if not input_path.exists():
        state.fail("invalid_input", f"Input file {input_path.name!r} does not exist.")
        return False
    if not input_path.is_file():
        state.fail("invalid_input", f"Input {input_path.name!r} is not a regular file.")
        return False
    if state.media_type is None:
        supported = ", ".join(sorted(SUPPORTED_MEDIA_TYPES))
        state.fail(
            "invalid_input",
            f"Input file {input_path.name!r} has an unsupported extension "
            f"{input_path.suffix!r}. Supported extensions: {supported}.",
        )
        return False
    try:
        size = input_path.stat().st_size
        digest = _sha256(input_path) if size else None
    except OSError as exc:
        state.fail("invalid_input", f"Input file {input_path.name!r} cannot be read: {exc}")
        return False
    if digest is None:
        state.fail("invalid_input", f"Input file {input_path.name!r} is empty.")
        return False
    state.sha256 = digest
    return True


def _record_result(
    state: _RunState, result: EngineResult, engine_name: str, output_dir: Path
) -> None:
    state.options = dict(result.options)
    if result.version:
        state.engine_version = result.version
    for diagnostic in result.diagnostics:
        if diagnostic.strip():
            state.warn("engine_diagnostic", diagnostic)
    state.artifacts = _map_artifacts(state, result.artifacts, output_dir)
    if not result.success:
        message = result.error or (
            f"Engine {engine_name!r} reported a failure without an error message "
            f"(return code {result.returncode})."
        )
        state.fail("engine_failed", message, returncode=result.returncode)


def _map_artifacts(
    state: _RunState, artifacts: Mapping[str, Path], output_dir: Path
) -> dict[str, str]:
    """Map engine artifact keys and paths to run document kinds and relative POSIX paths."""
    kinds = _artifact_kinds(list(artifacts))
    root = output_dir.resolve()
    recorded: dict[str, str] = {}
    for key, path in artifacts.items():
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            relative = None
        if relative is None or relative == ".":
            reason = "it is not inside the run output directory"
        elif not resolved.exists():
            reason = "the file does not exist"
        elif _CONTROL_CHARACTERS.search(relative) or "\\" in relative:
            reason = "its path contains a backslash or control character"
        else:
            recorded[kinds[key]] = relative
            continue
        # The path itself is left out of the message: it may be an absolute local path.
        state.warn("artifact_not_recorded", f"Engine artifact {key!r} was not recorded: {reason}.")
    return recorded


def _artifact_kinds(keys: list[str]) -> dict[str, str]:
    """Assign each engine artifact key a unique snake_case artifact kind.

    Keys that already are valid kinds are kept. Other keys use the part before the
    first ':' (``musicxml:sub/a.musicxml`` -> ``musicxml``), lowercased with other
    characters replaced by '_'; collisions get ``_2``, ``_3``, ... in sorted key order.
    """
    kinds = {key: key for key in keys if _KIND_PATTERN.match(key)}
    taken = set(kinds.values())
    for key in sorted(key for key in keys if key not in kinds):
        base = re.sub(r"[^a-z0-9]+", "_", key.split(":", 1)[0].lower()).strip("_")
        if not base:
            base = "artifact"
        elif not base[0].isalpha():
            base = f"artifact_{base}"
        candidate, number = base, 2
        while candidate in taken:
            candidate, number = f"{base}_{number}", number + 1
        kinds[key] = candidate
        taken.add(candidate)
    return kinds


def _build_document(
    state: _RunState,
    filename: str,
    engine_name: str,
    started_at: datetime,
    duration: float,
) -> dict[str, Any]:
    return {
        "schema_version": CURRENT_RUN_SCHEMA_VERSION,
        "status": "failed" if state.error is not None else "succeeded",
        "input": {"filename": filename, "media_type": state.media_type, "sha256": state.sha256},
        "engine": {"name": engine_name, "version": state.engine_version},
        "options": dict(state.options),
        "artifacts": dict(state.artifacts),
        "timing": {
            "duration_seconds": max(duration, 0.0),
            "started_at": started_at.isoformat(timespec="milliseconds"),
        },
        "score": None,
        "warnings": list(state.warnings),
        "error": state.error,
    }


def _serialize(document: dict[str, Any]) -> str:
    """Return the JSON text of a document after checking it round-trips and validates."""
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    validate_run_document(json.loads(text))
    return text


def _recordable_filename(input_path: Path) -> str:
    name = input_path.name
    if not name or "\\" in name or _CONTROL_CHARACTERS.search(name):
        raise ValueError(
            f"Input path {str(input_path)!r} has no file name that can be recorded "
            "(empty, or containing a backslash or control character)."
        )
    return name


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir():
            raise OutputDirectoryError(f"Output path {str(output_dir)!r} is not a directory.")
        if any(output_dir.iterdir()):
            raise OutputDirectoryError(
                f"Output directory {str(output_dir)!r} is not empty. Use a new or empty "
                "directory per run so earlier results are not overwritten or mixed."
            )
    output_dir.mkdir(parents=True, exist_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "RAW_DIRNAME",
    "RUN_DOCUMENT_FILENAME",
    "SUPPORTED_MEDIA_TYPES",
    "OutputDirectoryError",
    "PipelineResult",
    "run_pipeline",
]
