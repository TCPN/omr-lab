"""Run orchestration: input checks, engine call, artifact mapping, and the run document.

This module only orchestrates. Engine-specific invocation stays in the adapters
under ``omr_lab.engines``; the run document contract is docs/json-schema.md and
the pipeline behavior is described in docs/pipeline.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
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
    RunErrorCode,
    RunWarningCode,
    artifact_path_violation,
    validate_run_document,
)

logger = logging.getLogger(__name__)

RUN_DOCUMENT_FILENAME = "run.json"
"""Name of the normalized run document inside the run output directory."""

RAW_DIRNAME = "raw"
"""Subdirectory of the run output directory that the engine writes its raw artifacts to."""

ENGINE_DIAGNOSTICS_FILENAME = "engine_diagnostics.log"
"""File in the run output directory that holds ``EngineResult.diagnostics``, one per line."""

ENGINE_DIAGNOSTICS_KIND = "engine_diagnostics"
"""Artifact kind of the engine diagnostics file."""

DEFAULT_TIMEOUT_SECONDS = 600.0
"""Default engine timeout; real engines can need minutes for a multi-page PDF."""

MEDIA_TYPE_BY_EXTENSION: Mapping[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
}
"""Accepted input file extensions (case-insensitive) and the media type recorded for each."""


# fullmatch, not match with "$": Python's "$" also matches before a final newline.
_ARTIFACT_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
# Raised by _serialize() for a document that cannot be written as valid JSON.
_SERIALIZE_ERRORS = (RunDocumentValidationError, TypeError, ValueError)
# The adapter's primary MusicXML key (docs/engine-interface.md).
_PRIMARY_MUSICXML_KEY = "musicxml"
# Kinds of files the pipeline itself records; an engine key with one of them is renamed.
_PIPELINE_ARTIFACT_KINDS = frozenset({ENGINE_DIAGNOSTICS_KIND})

_SCORE_NOT_NORMALIZED_MESSAGE = (
    f"Normalized score content is not produced by run schema {CURRENT_RUN_SCHEMA_VERSION}; "
    "the recognized notation is only in the raw artifacts."
)


class OutputDirectoryError(ValueError):
    """The run output directory cannot be used (not a directory, not empty, or not creatable)."""


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


def _identity(text: str) -> str:
    return text


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
    # Applied to every recorded message, so local paths do not reach run.json.
    redact_paths: Callable[[str], str] = _identity

    def record_error(self, code: RunErrorCode, message: str, **extra: Any) -> None:
        """Store the run's error, replacing any earlier one; it does not stop the run."""
        # The schema requires a non-empty message; an adapter may raise without one.
        if not isinstance(message, str) or not message.strip():
            message = f"Failed with {code} and no further message."
        self.error = {"code": code, "message": self._recordable(message), **extra}

    def warn(self, code: RunWarningCode, message: str) -> None:
        self.warnings.append({"code": code, "message": self._recordable(message)})

    def _recordable(self, message: str) -> str:
        # Undecodable engine output (surrogate escapes) cannot be written as UTF-8;
        # backslash escapes keep it visible, as in the diagnostics file.
        return self.redact_paths(message).encode("utf-8", "backslashreplace").decode("utf-8")


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
    ``raw/`` holds the engine's raw artifacts, ``engine_diagnostics.log`` the
    engine's diagnostics (when there are any) and ``run.json`` the run document.
    The input file is only read, never modified or copied.

    Run failures (invalid input, unavailable engine, invalid option, engine
    failure, unexpected errors while running the engine) do not raise: they are
    recorded as a failed run document, which is written and returned. A
    KeyboardInterrupt during the run is recorded as an ``interrupted`` run and
    then re-raised. Raises ValueError for arguments that make a run record
    impossible (an unusable ``output_dir`` raises OutputDirectoryError, a
    ValueError subclass).
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    # bool is an int subclass, so True would otherwise pass as a 1-second timeout.
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a finite number greater than zero")
    engine_name = engine.name
    if not isinstance(engine_name, str) or not engine_name:
        raise ValueError(f"Engine name must be a non-empty string, got {engine_name!r}.")
    filename = _recordable_filename(input_path)
    _prepare_output_dir(output_dir)

    started_at = datetime.now(UTC)
    start = time.perf_counter()
    state = _RunState(redact_paths=_path_redactor(input_path, output_dir))
    pending_interrupt: KeyboardInterrupt | None = None
    try:
        _check_and_recognize(state, input_path, output_dir, engine, timeout_seconds, options)
    except KeyboardInterrupt as exc:
        # Ctrl-C during a long engine run is normal: leave a record, then re-raise.
        pending_interrupt = exc
        message = "The run was interrupted (KeyboardInterrupt)."
        if state.error is None:
            state.record_error(RunErrorCode.INTERRUPTED, message)
        else:
            state.warn(RunWarningCode.UNEXPECTED_ERROR, message)
    except Exception as exc:  # Recorded in the run document and logged, not swallowed.
        logger.exception("Pipeline run failed unexpectedly")
        message = f"Unexpected {type(exc).__name__} during the run: {exc}"
        if state.error is None:
            state.record_error(RunErrorCode.INTERNAL_ERROR, message)
        else:
            # Keep the failure already recorded (e.g. engine_failed); it is the cause
            # the user needs, and this error came while reading the rest of the result.
            state.warn(RunWarningCode.UNEXPECTED_ERROR, message)
    if state.error is None:
        state.warn(RunWarningCode.SCORE_NOT_NORMALIZED, _SCORE_NOT_NORMALIZED_MESSAGE)
    duration = time.perf_counter() - start

    document = _build_document(state, filename, engine_name, started_at, duration)
    try:
        text = _serialize(document)
    except _SERIALIZE_ERRORS as exc:
        # A bug in an adapter or in this module produced an invalid record. Write a
        # reduced failed record instead, so the run still leaves a valid trace.
        logger.exception("Run document failed validation; writing a reduced failed record")
        build_document = partial(
            _build_document,
            filename=filename,
            engine_name=engine_name,
            started_at=started_at,
            duration=duration,
        )
        document, text = _fallback_document(state, exc, build_document)

    document_path = output_dir / RUN_DOCUMENT_FILENAME
    _write_atomically(document_path, text)
    if pending_interrupt is not None:
        raise pending_interrupt
    return PipelineResult(document=document, document_path=document_path, output_dir=output_dir)


def _fallback_document(
    state: _RunState, exc: Exception, build_document: Callable[[_RunState], dict[str, Any]]
) -> tuple[dict[str, Any], str]:
    """Return a failed document that keeps as much of ``state`` as still validates.

    ``options`` and ``artifacts`` come from the adapter and are the usual cause, so
    they are dropped first; input, engine version, warnings and the original error
    are kept. If that still fails, only input provenance and the error are written.
    """
    reason = f"The run document could not be written: {exc}"
    reduced = _RunState(
        media_type=state.media_type,
        sha256=state.sha256,
        engine_version=state.engine_version,
        warnings=[w for w in state.warnings if w["code"] != RunWarningCode.SCORE_NOT_NORMALIZED],
        # The pipeline's own files have fixed, schema-valid paths; keeping them keeps
        # the pointer to the engine diagnostics.
        artifacts={k: v for k, v in state.artifacts.items() if k in _PIPELINE_ARTIFACT_KINDS},
        error=state.error,
        redact_paths=state.redact_paths,
    )
    if reduced.error is None:
        reduced.record_error(RunErrorCode.INTERNAL_ERROR, reason)
    else:
        reduced.warn(
            RunWarningCode.RUN_DOCUMENT_INCOMPLETE,
            f"{reason}. Options and engine artifacts were left out.",
        )
    document = build_document(reduced)
    try:
        return document, _serialize(document)
    except _SERIALIZE_ERRORS:
        logger.exception("Reduced run document failed validation; writing a minimal record")
    minimal = _RunState(
        media_type=state.media_type, sha256=state.sha256, redact_paths=state.redact_paths
    )
    minimal.record_error(RunErrorCode.INTERNAL_ERROR, reason)
    document = build_document(minimal)
    return document, _serialize(document)


def _path_redactor(input_path: Path, output_dir: Path) -> Callable[[str], str]:
    """Return a function that replaces local directory paths with placeholders.

    Engine messages and diagnostics often contain absolute paths (the command line,
    the run directory), which name the user and private folders. The output
    directory becomes ``<output_dir>``, the input file's directory ``<input_dir>``
    and the home directory ``~``. Longer paths are replaced first, so a run
    directory under the home directory still becomes ``<output_dir>``.
    """
    paths_to_redact: list[tuple[Path, str]] = []
    try:
        paths_to_redact.append((Path.home(), "~"))
    except (RuntimeError, KeyError, OSError):
        pass  # No home directory (e.g. a container user without a passwd entry).
    paths_to_redact += [(input_path.parent, "<input_dir>"), (output_dir, "<output_dir>")]
    placeholder_by_path: dict[str, str] = {}
    for path, placeholder in paths_to_redact:
        for form in _absolute_forms(path):
            # A filesystem root ("/") would turn every path into a placeholder.
            if Path(form).parent != Path(form):
                placeholder_by_path[form] = placeholder
    if not placeholder_by_path:
        return _identity
    longest_first = sorted(placeholder_by_path, key=len, reverse=True)
    # A path only matches as whole components: it must be followed by the end of the
    # text, a path separator, whitespace, a quote or punctuation, so "/home/ann"
    # leaves "/home/anna" alone.
    pattern = re.compile(
        "(?:" + "|".join(map(re.escape, longest_first)) + r")(?=$|[\\/\s'\".,;:)\]>])"
    )
    return lambda text: pattern.sub(lambda match: placeholder_by_path[match.group(0)], text)


def _absolute_forms(path: Path) -> set[str]:
    """Return the absolute and the resolved form of ``path`` (they differ under a symlink)."""
    forms = set()
    for to_form in (path.absolute, path.resolve):
        try:
            forms.add(str(to_form()))
        except (OSError, RuntimeError):
            continue
    return forms


def _check_and_recognize(
    state: _RunState,
    input_path: Path,
    output_dir: Path,
    engine: Engine,
    timeout_seconds: float,
    options: EngineOptions | None,
) -> None:
    if not _check_and_record_input(state, input_path):
        return

    availability = engine.check_available()
    state.engine_version = availability.version or None
    if not availability.available:
        message = availability.message or f"Engine {engine.name!r} is not available."
        state.record_error(RunErrorCode.ENGINE_UNAVAILABLE, message)
        return

    try:
        result = engine.recognize(
            input_path,
            output_dir / RAW_DIRNAME,
            timeout_seconds=timeout_seconds,
            options=options,
        )
    except InvalidEngineOptionError as exc:
        state.record_error(
            RunErrorCode.INVALID_OPTION,
            str(exc) or f"Engine {engine.name!r} rejected the options.",
        )
        return
    _record_result(state, result, engine.name, output_dir)


def _check_and_record_input(state: _RunState, input_path: Path) -> bool:
    """Record media type and digest; record an invalid_input error and return False on failure."""
    # From the name only, so even a missing input records the media type it claims.
    state.media_type = MEDIA_TYPE_BY_EXTENSION.get(input_path.suffix.lower())
    # One stat() in a try: on Python 3.11-3.13 exists() and is_file() raise
    # PermissionError for an input in a directory that cannot be searched.
    try:
        info = input_path.stat()
    except (FileNotFoundError, NotADirectoryError):
        state.record_error(
            RunErrorCode.INVALID_INPUT, f"Input file {input_path.name!r} does not exist."
        )
        return False
    except OSError as exc:
        state.record_error(RunErrorCode.INVALID_INPUT, _unreadable_input_message(input_path, exc))
        return False
    if not stat.S_ISREG(info.st_mode):
        state.record_error(
            RunErrorCode.INVALID_INPUT, f"Input {input_path.name!r} is not a regular file."
        )
        return False
    if state.media_type is None:
        supported = ", ".join(sorted(MEDIA_TYPE_BY_EXTENSION))
        state.record_error(
            RunErrorCode.INVALID_INPUT,
            f"Input file {input_path.name!r} has an unsupported extension "
            f"{input_path.suffix!r}. Supported extensions: {supported}.",
        )
        return False
    if info.st_size == 0:
        state.record_error(RunErrorCode.INVALID_INPUT, f"Input file {input_path.name!r} is empty.")
        return False
    try:
        state.sha256 = _sha256(input_path)
    except OSError as exc:
        state.record_error(RunErrorCode.INVALID_INPUT, _unreadable_input_message(input_path, exc))
        return False
    return True


def _unreadable_input_message(input_path: Path, exc: OSError) -> str:
    # strerror, not str(exc): str(exc) repeats the absolute path.
    return f"Input file {input_path.name!r} cannot be read: {exc.strerror or type(exc).__name__}."


def _record_result(
    state: _RunState, result: EngineResult, engine_name: str, output_dir: Path
) -> None:
    # The failure is recorded first, so run_pipeline keeps it (and adds a warning) if
    # reading the rest of the result (options, diagnostics, artifacts) raises.
    if not result.success:
        message = result.error or (
            f"Engine {engine_name!r} reported a failure without an error message "
            f"(return code {result.returncode})."
        )
        state.record_error(RunErrorCode.ENGINE_FAILED, message, returncode=result.returncode)
    state.options = dict(result.options)
    if result.version:
        state.engine_version = result.version
    diagnostics = _write_diagnostics(state, result.diagnostics, output_dir)
    state.artifacts = _map_artifacts(state, result.artifacts, output_dir)
    if diagnostics is not None:
        state.artifacts[ENGINE_DIAGNOSTICS_KIND] = diagnostics
    if result.success and _PRIMARY_MUSICXML_KEY not in state.artifacts:
        # An adapter that wrote no MusicXML must fail (docs/engine-interface.md), and a
        # succeeded run must give later steps its primary MusicXML.
        if _PRIMARY_MUSICXML_KEY in result.artifacts:
            problem = "was not recorded (see the artifact_not_recorded warning)"
        else:
            problem = "is missing from its artifacts"
        state.record_error(
            RunErrorCode.ENGINE_FAILED,
            f"Engine {engine_name!r} reported success, but its primary MusicXML artifact "
            f"{_PRIMARY_MUSICXML_KEY!r} {problem}; this is a bug in the engine adapter.",
            returncode=result.returncode,
        )


def _write_diagnostics(
    state: _RunState, diagnostics: Iterable[object] | str, output_dir: Path
) -> str | None:
    """Write the engine's diagnostics to the diagnostics file; return its relative path.

    Diagnostics are mostly log lines (the command line, stdout and stderr tails),
    so they go to a file instead of ``warnings``. Returns None when there are no
    non-blank diagnostics or the file cannot be written (a warning says why).
    """
    # A plain string would otherwise be split into one entry per character, and raw
    # process output (bytes) into one int per byte.
    if isinstance(diagnostics, str | bytes | bytearray):
        entries: tuple[object, ...] = (diagnostics,)
    else:
        entries = tuple(diagnostics)
    lines = [
        state.redact_paths(entry) for entry in entries if isinstance(entry, str) and entry.strip()
    ]
    left_out = [type(entry).__name__ for entry in entries if not isinstance(entry, str)]
    if left_out:
        # One warning, however many entries: a long list must not flood the warnings.
        state.warn(
            RunWarningCode.ENGINE_DIAGNOSTIC_NOT_RECORDED,
            f"{len(left_out)} engine diagnostic(s) of type {', '.join(sorted(set(left_out)))} "
            "were left out; diagnostics must be strings.",
        )
    if not lines:
        return None
    path = output_dir / ENGINE_DIAGNOSTICS_FILENAME
    try:
        # backslashreplace: undecodable engine output (surrogate escapes) stays visible.
        with path.open("x", encoding="utf-8", errors="backslashreplace", newline="\n") as stream:
            stream.writelines(f"{line}\n" for line in lines)
    except OSError as exc:
        state.warn(
            RunWarningCode.ENGINE_DIAGNOSTIC_NOT_RECORDED,
            f"{len(lines)} engine diagnostics were left out: {ENGINE_DIAGNOSTICS_FILENAME} "
            f"cannot be written ({exc.strerror or type(exc).__name__}).",
        )
        return None
    return ENGINE_DIAGNOSTICS_FILENAME


def _map_artifacts(
    state: _RunState, artifacts: Mapping[str, Path], output_dir: Path
) -> dict[str, str]:
    """Map engine artifact keys and paths to run document kinds and relative POSIX paths."""
    run_root = output_dir.resolve()
    # The engine writes into raw/; anything else in the run directory (run.json and
    # the diagnostics file) belongs to the pipeline and must not be claimed by an engine.
    raw_root = (output_dir / RAW_DIRNAME).resolve()
    recordable: dict[str, str] = {}
    for key, path in artifacts.items():
        # A symlink loop raises RuntimeError on Python 3.11/3.12, an unsearchable
        # directory PermissionError, and a non-path value from an adapter TypeError.
        try:
            resolved = Path(path).resolve()
            exists = resolved.exists()
            is_file = exists and resolved.is_file()
        except (OSError, RuntimeError, TypeError):
            resolved, exists, is_file = None, False, False
        # raw/ itself may be a symlink out of the run directory; then nothing in it counts.
        inside_raw = (
            resolved is not None
            and resolved != raw_root
            and resolved.is_relative_to(raw_root)
            and resolved.is_relative_to(run_root)
        )
        relative = resolved.relative_to(run_root).as_posix() if inside_raw else ""
        if resolved is None:
            reason = "its path cannot be resolved"
        elif not inside_raw:
            reason = f"it is not inside the engine's {RAW_DIRNAME}/ output directory"
        elif not exists:
            reason = "the file does not exist"
        elif not is_file:
            reason = "it is not a regular file"
        elif (rule := artifact_path_violation(relative)) is not None:
            reason = f"its path {rule}"
        else:
            recordable[key] = relative
            continue
        # The path itself is left out of the message: it may be an absolute local path.
        state.warn(
            RunWarningCode.ARTIFACT_NOT_RECORDED,
            f"Engine artifact {key!r} was not recorded: {reason}.",
        )
    # Kinds are assigned after filtering, so a dropped "musicxml:<path>" never pushes a
    # recorded one to a numbered kind. A dropped key that is itself a kind (such as the
    # primary "musicxml") stays reserved, so no other artifact takes its kind.
    reserved = {key for key in artifacts if _ARTIFACT_KIND_PATTERN.fullmatch(key)}
    kinds = _artifact_kinds(list(recordable), reserved | _PIPELINE_ARTIFACT_KINDS)
    return {kinds[key]: relative for key, relative in recordable.items()}


def _artifact_kinds(keys: list[str], reserved: set[str] | frozenset[str]) -> dict[str, str]:
    """Assign each engine artifact key a unique snake_case artifact kind.

    Keys that already are valid kinds are kept, except the pipeline's own kinds.
    Other keys use the part before the first ':' (``musicxml:sub/a.musicxml`` ->
    ``musicxml``), lowercased with other characters replaced by '_'; a kind that
    is in ``reserved`` or already taken gets ``_2``, ``_3``, ... in sorted key order.
    """
    kinds = {
        key: key
        for key in keys
        if _ARTIFACT_KIND_PATTERN.fullmatch(key) and key not in _PIPELINE_ARTIFACT_KINDS
    }
    taken = set(kinds.values()) | reserved
    for key in sorted(key for key in keys if key not in kinds):
        kind_prefix = re.sub(r"[^a-z0-9]+", "_", key.split(":", 1)[0].lower()).strip("_")
        if not kind_prefix:
            kind_prefix = "artifact"
        elif not kind_prefix[0].isalpha():
            kind_prefix = f"artifact_{kind_prefix}"
        candidate, number = kind_prefix, 2
        while candidate in taken:
            candidate, number = f"{kind_prefix}_{number}", number + 1
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
    # Lone surrogates (undecodable bytes kept by surrogateescape) survive json.dumps but
    # cannot be written as UTF-8; UnicodeEncodeError is a ValueError, so it falls back.
    text.encode("utf-8")
    validate_run_document(json.loads(text))
    return text


def _recordable_filename(input_path: Path) -> str:
    name = input_path.name
    if not name or "\\" in name or _CONTROL_CHARACTERS.search(name):
        raise ValueError(
            f"Input path {str(input_path)!r} has no file name that can be recorded "
            "(empty, or containing a backslash or control character)."
        )
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        # On POSIX, a name in a legacy encoding (e.g. Big5 from a Windows zip) arrives
        # with surrogate escapes, which run.json cannot store.
        raise ValueError(
            f"Input file name {name!r} is not valid UTF-8 (perhaps Big5 or GBK). "
            "Rename the file to a UTF-8 name."
        ) from exc
    return name


def _prepare_output_dir(output_dir: Path) -> None:
    # Any OSError (a dangling symlink, a file as a parent, an unsearchable parent that
    # makes exists() raise on Python 3.11-3.13) means the path cannot be used.
    try:
        if output_dir.exists():
            if not output_dir.is_dir():
                raise OutputDirectoryError(f"Output path {str(output_dir)!r} is not a directory.")
            if any(output_dir.iterdir()):
                raise OutputDirectoryError(
                    f"Output directory {str(output_dir)!r} is not empty. Use a new or empty "
                    "directory per run so earlier results are not overwritten or mixed."
                )
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _unusable_output_dir(output_dir, exc) from exc
    # The run claims the directory by creating raw/ exclusively: of several runs that
    # all found it missing or empty, only one gets past this point.
    try:
        (output_dir / RAW_DIRNAME).mkdir(exist_ok=False)
    except FileExistsError:
        raise OutputDirectoryError(
            f"Output directory {str(output_dir)!r} is already used by another run. Use a "
            "new or empty directory per run so earlier results are not overwritten or mixed."
        ) from None
    except OSError as exc:
        raise _unusable_output_dir(output_dir, exc) from exc


def _unusable_output_dir(output_dir: Path, exc: OSError) -> OutputDirectoryError:
    return OutputDirectoryError(
        f"Output path {str(output_dir)!r} cannot be used: {exc.strerror or exc}."
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomically(path: Path, text: str) -> None:
    # A unique temporary name, so concurrent writers never rename each other's file.
    # open("x") rather than tempfile.mkstemp: mkstemp always creates mode 0600, which
    # would make run.json unreadable to other users who can read the rest of the run.
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ENGINE_DIAGNOSTICS_FILENAME",
    "ENGINE_DIAGNOSTICS_KIND",
    "MEDIA_TYPE_BY_EXTENSION",
    "RAW_DIRNAME",
    "RUN_DOCUMENT_FILENAME",
    "OutputDirectoryError",
    "PipelineResult",
    "RunErrorCode",
    "RunWarningCode",
    "run_pipeline",
]
