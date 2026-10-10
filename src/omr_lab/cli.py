from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omr-lab",
        description="Local-first Optical Music Recognition pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="Check basic OMR Lab prerequisites.")
    run_parser = subparsers.add_parser(
        "run", help="Run recognition (engine integration is not implemented yet)."
    )
    run_parser.add_argument("input", help="Input image or PDF path.")
    run_parser.add_argument("--engine", default="audiveris", help="Engine adapter to use.")
    run_parser.add_argument("--output", required=True, help="Output directory.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "doctor":
        print("OMR Lab package is available.")
        print("External engine checks will be added in the adapter milestone.")
        return 0
    if args.command == "run":
        print("Recognition is not implemented yet. See issue OMR-007.", file=sys.stderr)
        return 2
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
