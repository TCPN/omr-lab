"""Command-line interface: ``omr-lab doctor``, ``run`` and ``benchmark`` (docs/cli.md).

The CLI only parses arguments, builds engines and reports results; recognition
itself is ``omr_lab.pipeline.run_pipeline``. Exit codes are part of the
documented interface, so they are named constants here.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from omr_lab import __version__
from omr_lab.benchmark import ManifestError, load_manifest, run_benchmark
from omr_lab.engines import AudiverisEngine, Engine, EngineOptions, EngineOptionValue
from omr_lab.engines.mock import MockEngine
from omr_lab.pipeline import (
    DEFAULT_TIMEOUT_SECONDS,
    RAW_DIRNAME,
    RUN_DOCUMENT_FILENAME,
    OutputDirectoryError,
    RunErrorCode,
    run_pipeline,
)

EXIT_OK = 0
"""``run`` succeeded, ``doctor`` found every checked engine available, or every
``benchmark`` sample that was not excluded succeeded."""
EXIT_RUN_FAILED = 1
"""The run failed for another reason (engine failure, invalid MusicXML, internal error),
or a ``benchmark`` sample failed or was not run (the report is still written)."""
EXIT_USAGE = 2
"""Invalid command line (also argparse's own exit status for usage errors)."""
EXIT_INVALID_INPUT = 3
"""The input file is missing, unreadable, empty, or of an unsupported type; or the
``benchmark`` manifest cannot be read or is invalid."""
EXIT_ENGINE_UNAVAILABLE = 4
"""The engine's prerequisites are missing (``run``), or an engine is unavailable (``doctor``)."""
EXIT_INVALID_OPTION = 5
"""The engine rejected an ``--option`` name or value."""
EXIT_OUTPUT_DIR_UNUSABLE = 6
"""The output directory cannot be used; no run document (or report) was written."""
EXIT_INTERRUPTED = 130
"""Stopped with Ctrl-C (128 + SIGINT, the shell convention)."""

_EXIT_BY_ERROR_CODE = {
    RunErrorCode.INVALID_INPUT: EXIT_INVALID_INPUT,
    RunErrorCode.ENGINE_UNAVAILABLE: EXIT_ENGINE_UNAVAILABLE,
    RunErrorCode.INVALID_OPTION: EXIT_INVALID_OPTION,
    RunErrorCode.INTERRUPTED: EXIT_INTERRUPTED,
}

# Engine name -> factory. A factory, not an instance, so each command gets a fresh
# engine (AudiverisEngine caches its availability check per instance).
ENGINES: dict[str, Callable[[], Engine]] = {
    "audiveris": AudiverisEngine,
    "mock": MockEngine,
}
DEFAULT_ENGINE = "audiveris"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omr-lab",
        description="Local-first Optical Music Recognition pipeline.",
        epilog=(
            f"Exit codes: {EXIT_OK} success, {EXIT_RUN_FAILED} run failed, "
            f"{EXIT_USAGE} usage error, {EXIT_INVALID_INPUT} invalid input, "
            f"{EXIT_ENGINE_UNAVAILABLE} engine unavailable, {EXIT_INVALID_OPTION} invalid "
            f"engine option, {EXIT_OUTPUT_DIR_UNUSABLE} unusable output directory, "
            f"{EXIT_INTERRUPTED} interrupted. See docs/cli.md."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    engine_names = sorted(ENGINES)

    doctor = subparsers.add_parser(
        "doctor",
        help="Check Python and the OMR engines this machine can run.",
        description=(
            "Report the Python version and, for each engine, whether it is available, "
            "its version, and what to install if it is missing. Exits with "
            f"{EXIT_ENGINE_UNAVAILABLE} if a checked engine is unavailable."
        ),
    )
    doctor.add_argument(
        "--engine",
        choices=engine_names,
        help="Check only this engine (default: all engines).",
    )

    run = subparsers.add_parser(
        "run",
        help="Recognize one image or PDF and write a run directory.",
        description=(
            "Recognize INPUT with an engine and write OUTPUT/run.json (the run document), "
            "OUTPUT/raw/ (the engine's raw artifacts) and, when the engine reports any, "
            "OUTPUT/engine_diagnostics.log. OUTPUT must be missing or empty. "
            "A recognized score is not evidence that the music was recognized correctly."
        ),
    )
    run.add_argument("input", type=Path, help="Input image (.png, .jpg, .tif, .bmp) or PDF.")
    run.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Run output directory; it must not exist or be empty.",
    )
    _add_engine_arguments(run, engine_names)

    benchmark = subparsers.add_parser(
        "benchmark",
        help="Run every sample of a benchmark manifest and write a report.",
        description=(
            "Run each sample of MANIFEST with one engine and write OUTPUT/report.json, "
            "OUTPUT/report.md and one run directory per sample in OUTPUT/runs/. OUTPUT must "
            "be missing or empty. Exits with "
            f"{EXIT_RUN_FAILED} when a sample failed or was not run. See docs/benchmark.md."
        ),
    )
    benchmark.add_argument("manifest", type=Path, help="Benchmark manifest (JSON).")
    benchmark.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Benchmark output directory; it must not exist or be empty.",
    )
    _add_engine_arguments(benchmark, engine_names)
    return parser


def _add_engine_arguments(parser: argparse.ArgumentParser, engine_names: list[str]) -> None:
    parser.add_argument(
        "--engine",
        choices=engine_names,
        default=DEFAULT_ENGINE,
        help=f"Engine adapter to use (default: {DEFAULT_ENGINE}).",
    )
    parser.add_argument(
        "--option",
        dest="options",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Engine option; repeatable. VALUE is read as JSON when it is a JSON number, "
            "true, false, null or a quoted string, and as plain text otherwise "
            "(so outcome=failed and outcome='\"failed\"' are the same)."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_parse_timeout_seconds,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"Engine timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )


def main(argv: Sequence[str] | None = None) -> int:
    _tolerate_unencodable_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.engine, sys.stdout)
    if args.command in ("run", "benchmark"):
        try:
            options = parse_options(args.options)
        except ValueError as exc:
            parser.error(str(exc))
        if args.command == "run":
            return _run(args.input, args.output, args.engine, options, args.timeout)
        return _benchmark(args.manifest, args.output, args.engine, options, args.timeout)
    parser.print_help()
    return EXIT_OK


def _tolerate_unencodable_output() -> None:
    # Paths are printed as given. One that stdout cannot encode (a non-UTF-8 directory
    # name on POSIX, or a character outside the ANSI code page of a redirected Windows
    # stream) must not turn a finished run into a traceback and the wrong exit code.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def parse_options(pairs: Sequence[str]) -> EngineOptions:
    """Turn ``KEY=VALUE`` strings into engine options; raise ValueError on bad syntax."""
    options: dict[str, EngineOptionValue] = {}
    for pair in pairs:
        key, separator, text = pair.partition("=")
        key = key.strip()
        if not separator or not key:
            raise ValueError(f"--option {pair!r} must have the form KEY=VALUE")
        if key in options:
            raise ValueError(f"--option {key!r} is given more than once")
        options[key] = _parse_option_value(text)
    return options


def _parse_option_value(text: str) -> EngineOptionValue:
    try:
        value = json.loads(text)
    except ValueError:
        return text
    # A JSON array or object is not an option value; keep the text so the engine
    # reports it rather than receiving a guessed structure.
    if isinstance(value, list | dict):
        return text
    # json accepts NaN and Infinity, which a run document cannot store.
    if isinstance(value, float) and not math.isfinite(value):
        return text
    return value


def _parse_timeout_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be a finite number greater than zero")
    return value


def _doctor(engine_name: str | None, out: TextIO) -> int:
    print(f"omr-lab {__version__}", file=out)
    print(f"Python {platform.python_version()} ({sys.executable})", file=out)
    exit_code = EXIT_OK
    for name in [engine_name] if engine_name else sorted(ENGINES):
        availability = ENGINES[name]().check_available()
        if availability.available:
            version = f" {availability.version}" if availability.version else ""
            print(f"[ok]      {name}{version}", file=out)
        else:
            exit_code = EXIT_ENGINE_UNAVAILABLE
            print(f"[missing] {name}: {availability.message or 'not available'}", file=out)
    return exit_code


def _run(
    input_path: Path,
    output_dir: Path,
    engine_name: str,
    options: EngineOptions,
    timeout_seconds: float,
) -> int:
    aborted_message = _aborted_run_message(output_dir)
    if aborted_message is not None:
        print(f"omr-lab: error: {aborted_message}", file=sys.stderr)
        return EXIT_OUTPUT_DIR_UNUSABLE
    engine = ENGINES[engine_name]()
    try:
        result = run_pipeline(
            input_path, output_dir, engine, timeout_seconds=timeout_seconds, options=options
        )
    except OutputDirectoryError as exc:
        print(f"omr-lab: error: {exc}", file=sys.stderr)
        return EXIT_OUTPUT_DIR_UNUSABLE
    except KeyboardInterrupt:
        # run_pipeline records "interrupted" only for a Ctrl-C during the engine step;
        # before or after it, no run.json is written.
        document_path = output_dir / RUN_DOCUMENT_FILENAME
        if document_path.is_file():
            print(f"omr-lab: interrupted; see {document_path}", file=sys.stderr)
        else:
            print("omr-lab: interrupted before the run document was written", file=sys.stderr)
        return EXIT_INTERRUPTED
    except ValueError as exc:
        # Arguments that make a run record impossible, such as an input file name
        # the run document cannot store.
        print(f"omr-lab: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        # Raised only while writing run.json (a full disk, a quota); the directory is
        # now an aborted run.
        print(
            f"omr-lab: error: {output_dir / RUN_DOCUMENT_FILENAME} could not be written "
            f"({exc.strerror or exc}). {output_dir} now holds an aborted run; keep it for "
            "inspection and choose a new output directory.",
            file=sys.stderr,
        )
        return EXIT_OUTPUT_DIR_UNUSABLE

    document = result.document
    print(f"Run document: {result.document_path}")
    for warning in document["warnings"]:
        print(f"warning [{warning['code']}]: {warning['message']}", file=sys.stderr)
    if result.succeeded:
        print(f"Status: succeeded (engine {engine_name})")
        return EXIT_OK
    error = document["error"]
    print(f"Status: failed [{error['code']}]: {error['message']}", file=sys.stderr)
    return _EXIT_BY_ERROR_CODE.get(error["code"], EXIT_RUN_FAILED)


def _benchmark(
    manifest_path: Path,
    output_dir: Path,
    engine_name: str,
    options: EngineOptions,
    timeout_seconds: float,
) -> int:
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"omr-lab: error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    try:
        result = run_benchmark(
            manifest,
            output_dir,
            ENGINES[engine_name](),
            timeout_seconds=timeout_seconds,
            options=options,
        )
    except OutputDirectoryError as exc:
        print(f"omr-lab: error: {exc}", file=sys.stderr)
        return EXIT_OUTPUT_DIR_UNUSABLE
    except ValueError as exc:
        # Arguments the report cannot record, such as an option that is not valid UTF-8;
        # checked before anything is written.
        print(f"omr-lab: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print(
            f"omr-lab: interrupted; no report was written. The finished runs are in {output_dir}",
            file=sys.stderr,
        )
        return EXIT_INTERRUPTED
    except OSError as exc:
        print(
            f"omr-lab: error: the benchmark could not write its files in {output_dir} "
            f"({exc.strerror or exc}); no complete report was written.",
            file=sys.stderr,
        )
        return EXIT_OUTPUT_DIR_UNUSABLE

    summary = result.report["summary"]
    print(f"Report: {result.report_path}")
    print(f"Summary: {result.markdown_path}")
    for entry in result.report["samples"]:
        detail = ""
        if entry["run"] is not None and entry["run"]["error_code"]:
            detail = f" [{entry['run']['error_code']}]"
        elif entry["problem"] is not None:
            detail = f" [{entry['problem']['code']}]"
        print(f"  {entry['id']}: {entry['status']}{detail}")
    print(
        f"Samples: {summary['samples']}, excluded {summary['excluded']}, "
        f"not run {summary['not_run']}, succeeded {summary['succeeded']}, "
        f"failed {summary['failed']}"
    )
    return EXIT_OK if result.all_succeeded else EXIT_RUN_FAILED


def _aborted_run_message(output_dir: Path) -> str | None:
    """Explain an output directory left behind by a run that could not write run.json."""
    try:
        aborted = (output_dir / RAW_DIRNAME).is_dir() and not (
            output_dir / RUN_DOCUMENT_FILENAME
        ).exists()
    except OSError:
        return None  # run_pipeline reports the unusable directory itself.
    if not aborted:
        return None
    return (
        f"Output directory {str(output_dir)!r} holds an aborted run: it has {RAW_DIRNAME}/ but "
        f"no {RUN_DOCUMENT_FILENAME} (the run could not write its record, for example because "
        "the disk was full, or another run is still writing it). Keep it for inspection and "
        "choose a new output directory."
    )


if __name__ == "__main__":
    raise SystemExit(main())
