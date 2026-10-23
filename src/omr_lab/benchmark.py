"""Run every sample of a benchmark manifest through the pipeline and write a report.

Manifest and report formats, the output layout and the sample statuses are in
docs/benchmark.md. This module orchestrates only: each sample is one ordinary
``run_pipeline`` run, and the report summarizes the run documents.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omr_lab import __version__
from omr_lab.engines import Engine, EngineOptions
from omr_lab.pipeline import (
    RUN_DOCUMENT_FILENAME,
    OutputDirectoryError,
    _path_redactor,
    _write_atomically,
    run_pipeline,
)
from omr_lab.schemas.benchmark import (
    BENCHMARK_REPORT_SCHEMA_VERSION,
    BenchmarkDocumentValidationError,
    validate_benchmark_manifest,
    validate_benchmark_report,
)

REPORT_JSON_FILENAME = "report.json"
"""File name of the report in the benchmark output directory."""

REPORT_MARKDOWN_FILENAME = "report.md"
"""File name of the human-readable summary written next to report.json."""

RUNS_DIRNAME = "runs"
"""Directory holding one pipeline run directory per sample, named by sample id."""


class ManifestError(ValueError):
    """The manifest cannot be read, is not valid JSON, or breaks the manifest rules."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """A validated manifest. ``base_dir`` is the directory sample paths are relative to."""

    path: Path
    sha256: str
    document: dict[str, Any]

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    @property
    def samples(self) -> list[dict[str, Any]]:
        return self.document["samples"]


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    report: dict[str, Any]
    report_path: Path
    markdown_path: Path

    @property
    def all_succeeded(self) -> bool:
        """True when no sample failed or was left not run (also when all are excluded)."""
        summary = self.report["summary"]
        return summary["failed"] == 0 and summary["not_run"] == 0


def load_manifest(path: Path | str) -> Manifest:
    """Read and validate a manifest; raise ManifestError with every problem found."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ManifestError(
            f"Manifest {str(path)!r} cannot be read: {exc.strerror or exc}."
        ) from exc
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestError(f"Manifest {str(path)!r} is not UTF-8 JSON: {exc}.") from exc
    try:
        # json accepts escapes of unpaired surrogates ("\\ud83c"), which cannot be
        # written back as UTF-8; reject them before any sample runs.
        json.dumps(document, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ManifestError(
            f"Manifest {str(path)!r} contains an unpaired surrogate escape: {exc}."
        ) from exc
    try:
        validate_benchmark_manifest(document)
    except BenchmarkDocumentValidationError as exc:
        raise ManifestError(f"Manifest {str(path)!r}: {exc}") from exc
    duplicates = sorted(
        sample_id
        for sample_id, count in Counter(sample["id"] for sample in document["samples"]).items()
        if count > 1
    )
    if duplicates:
        raise ManifestError(
            f"Manifest {str(path)!r}: sample ids must be unique; repeated: {', '.join(duplicates)}."
        )
    return Manifest(path=path, sha256=hashlib.sha256(data).hexdigest(), document=document)


def run_benchmark(
    manifest: Manifest,
    output_dir: Path | str,
    engine: Engine,
    *,
    timeout_seconds: float,
    options: EngineOptions | None = None,
) -> BenchmarkResult:
    """Run each manifest sample with ``engine`` and write report.json and report.md.

    Raises ValueError for an invalid ``timeout_seconds``, engine name or options before anything
    is written. ``output_dir`` must be missing or empty (OutputDirectoryError otherwise).
    Failed runs do not stop the benchmark; they are recorded. A KeyboardInterrupt stops
    it without a report. OSError is raised when the report cannot be written.
    """
    output_dir = Path(output_dir)
    # The same argument checks as run_pipeline, before the output directory is claimed.
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int | float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a finite number greater than zero")
    if not isinstance(engine.name, str) or not engine.name:
        raise ValueError(f"Engine name must be a non-empty string, got {engine.name!r}.")
    requested = dict(options or {})
    try:
        json.dumps(requested, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Engine options cannot be recorded as UTF-8 JSON: {exc}.") from exc
    generated_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    runs_dir = _prepare_output_dir(output_dir)
    entries = [
        _run_sample(manifest, sample, runs_dir, engine, timeout_seconds, requested)
        for sample in manifest.samples
    ]
    report = {
        "schema_version": BENCHMARK_REPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "manifest": {
            "filename": manifest.path.name,
            "sha256": manifest.sha256,
            "name": manifest.document.get("name"),
            "schema_version": manifest.document["schema_version"],
        },
        "environment": environment(),
        "engine": {
            "name": engine.name,
            "requested_options": requested,
            "timeout_seconds": timeout_seconds,
        },
        "samples": entries,
        "summary": _summary(entries),
    }
    validate_benchmark_report(report)
    report_path = output_dir / REPORT_JSON_FILENAME
    markdown_path = output_dir / REPORT_MARKDOWN_FILENAME
    # Atomic, so a reader never finds a truncated report.json.
    _write_atomically(
        report_path, json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    )
    _write_atomically(markdown_path, render_markdown(report))
    return BenchmarkResult(report=report, report_path=report_path, markdown_path=markdown_path)


def environment() -> dict[str, str]:
    """Software and platform facts for the report; no host name, user or path."""
    return {
        "omr_lab_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
    }


def _prepare_output_dir(output_dir: Path) -> Path:
    try:
        if output_dir.exists():
            if not output_dir.is_dir():
                raise OutputDirectoryError(f"Output path {str(output_dir)!r} is not a directory.")
            if any(output_dir.iterdir()):
                raise OutputDirectoryError(
                    f"Output directory {str(output_dir)!r} is not empty. Use a new or empty "
                    "directory per benchmark so earlier reports are not overwritten or mixed."
                )
        else:
            output_dir.mkdir(parents=True)
        runs_dir = output_dir / RUNS_DIRNAME
        # Created exclusively, so of two benchmarks started into one directory only one runs.
        runs_dir.mkdir()
    except FileExistsError:
        raise OutputDirectoryError(
            f"Output directory {str(output_dir)!r} is already used by another benchmark."
        ) from None
    except OSError as exc:
        raise OutputDirectoryError(
            f"Output path {str(output_dir)!r} cannot be used: {exc.strerror or exc}."
        ) from exc
    return runs_dir


def _run_sample(
    manifest: Manifest,
    sample: dict[str, Any],
    runs_dir: Path,
    engine: Engine,
    timeout_seconds: float,
    requested_options: dict[str, Any],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": sample["id"],
        "status": "not_run",
        "image": sample["image"],
        "image_sha256": sample["image_sha256"],
        "ground_truth": sample.get("ground_truth"),
        "source": sample["source"],
        "license": sample["license"],
        "condition": sample["condition"],
        "exclusion_reason": None,
        "problem": None,
        "run": None,
        "correctness": None,
    }
    if "excluded" in sample:
        entry["status"] = "excluded"
        entry["exclusion_reason"] = sample["excluded"]["reason"]
        return entry
    image = manifest.base_dir / sample["image"]
    problem = _checksum_problem("image", image, sample["image"], sample["image_sha256"])
    if problem is None and sample.get("ground_truth") is not None:
        problem = _checksum_problem(
            "ground_truth",
            manifest.base_dir / sample["ground_truth"],
            sample["ground_truth"],
            sample["ground_truth_sha256"],
        )
    if problem is not None:
        entry["problem"] = problem
        return entry
    try:
        result = run_pipeline(
            image,
            runs_dir / sample["id"],
            engine,
            timeout_seconds=timeout_seconds,
            options=requested_options,
        )
    except ValueError as exc:
        # Arguments that make a run record impossible, such as an image file name the
        # run document cannot store.
        # The message may name local paths; redact them the way run documents do.
        redact = _path_redactor(image, runs_dir.parent)
        entry["problem"] = {"code": "run_not_started", "message": redact(str(exc))}
        return entry
    document = result.document
    entry["status"] = document["status"]
    entry["run"] = {
        "document": f"{RUNS_DIRNAME}/{sample['id']}/{RUN_DOCUMENT_FILENAME}",
        "status": document["status"],
        "error_code": document["error"]["code"] if document["error"] else None,
        "engine_version": document["engine"]["version"],
        "options": document["options"],
        "duration_seconds": document["timing"]["duration_seconds"],
        "warning_codes": dict(sorted(Counter(w["code"] for w in document["warnings"]).items())),
    }
    return entry


def _checksum_problem(
    role: str, path: Path, manifest_text: str, expected: str
) -> dict[str, str] | None:
    try:
        # Only regular files are read: a FIFO or device would block or never end.
        if not stat.S_ISREG(path.stat().st_mode):
            return {
                "code": f"{role}_missing",
                "message": f"{manifest_text!r} is not a regular file.",
            }
        digest = _sha256(path)
    except OSError as exc:
        return {
            "code": f"{role}_missing",
            "message": f"{manifest_text!r} cannot be read: {exc.strerror or exc}.",
        }
    if digest != expected:
        return {
            "code": f"{role}_checksum_mismatch",
            "message": (
                f"{manifest_text!r} has SHA-256 {digest}, but the manifest expects {expected}. "
                "Update the manifest only if the file was changed on purpose."
            ),
        }
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(entry["status"] for entry in entries)
    runs = [entry["run"] for entry in entries if entry["run"] is not None]
    attempted = len(runs)
    return {
        "samples": len(entries),
        "excluded": statuses["excluded"],
        "not_run": statuses["not_run"],
        "attempted": attempted,
        "succeeded": statuses["succeeded"],
        "failed": statuses["failed"],
        "success_rate": round(statuses["succeeded"] / attempted, 3) if attempted else None,
        "engine_versions": sorted(
            {run["engine_version"] for run in runs if run["engine_version"] is not None}
        ),
        "error_codes": dict(
            sorted(Counter(run["error_code"] for run in runs if run["error_code"]).items())
        ),
        "run_duration_seconds": round(sum(run["duration_seconds"] for run in runs), 3),
    }


def render_markdown(report: dict[str, Any]) -> str:
    """A short human-readable view of a report; report.json stays the full record."""
    manifest = report["manifest"]
    environment_ = report["environment"]
    engine = report["engine"]
    summary = report["summary"]
    rate = summary["success_rate"]
    lines = [
        f"# Benchmark report: {_cell(manifest['name'] or manifest['filename'])}",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Manifest: `{_cell(manifest['filename'])}` (SHA-256 `{manifest['sha256']}`)",
        f"- omr-lab {environment_['omr_lab_version']}, "
        f"{environment_['python_implementation']} {environment_['python_version']}, "
        f"{environment_['os']} {environment_['os_release']} ({environment_['machine']})",
        f"- Engine: {_cell(engine['name'])} "
        f"{_cell(', '.join(summary['engine_versions']) or 'version unknown')}; "
        f"options {_cell(json.dumps(engine['requested_options'], ensure_ascii=False))}; "
        f"timeout {engine['timeout_seconds']:g} s",
        f"- Samples: {summary['samples']} ({summary['excluded']} excluded, "
        f"{summary['not_run']} not run); attempted {summary['attempted']}: "
        f"{summary['succeeded']} succeeded, {summary['failed']} failed"
        + (f" (success rate {rate:.1%})" if rate is not None else ""),
        "",
        "Succeeded means the pipeline completed; it is not a claim that the music was "
        "recognized correctly. Correctness is not measured in this report.",
        "",
        "| Sample | Status | Condition | Duration (s) | Error or reason | Warnings |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in report["samples"]:
        run = entry["run"]
        if run is not None:
            duration = f"{run['duration_seconds']:.3f}"
            detail = run["error_code"] or ""
            warnings = ", ".join(f"{code} × {n}" for code, n in run["warning_codes"].items())
        else:
            duration = ""
            warnings = ""
            detail = (
                entry["exclusion_reason"]
                if entry["status"] == "excluded"
                else f"{entry['problem']['code']}: {entry['problem']['message']}"
            )
        lines.append(
            f"| {_cell(entry['id'])} | {entry['status']} | {_cell(entry['condition'])} "
            f"| {duration} | {_cell(detail)} | {_cell(warnings)} |"
        )
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


__all__ = [
    "REPORT_JSON_FILENAME",
    "REPORT_MARKDOWN_FILENAME",
    "RUNS_DIRNAME",
    "BenchmarkResult",
    "Manifest",
    "ManifestError",
    "environment",
    "load_manifest",
    "render_markdown",
    "run_benchmark",
]
