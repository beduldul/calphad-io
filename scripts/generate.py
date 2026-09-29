#!/usr/bin/env python3
"""Regenerate every fixture database with ``calphad-io`` itself.

This is the generator half of the pycalphad cross-check documented in
``README.md``: it reads each real database under ``tests/data/``, writes it back
out with this library's own writer, and drops the result in a target directory.
:mod:`scripts.verify` then asks an independent implementation (pycalphad)
whether the regenerated files still describe the same database.

Run it with the repository's own virtualenv -- it only needs ``calphad-io``::

    python scripts/generate.py --out /tmp/calphad-roundtrip

The default source directory is ``tests/data/`` next to this repository.  Files
the library refuses by design (``FeMnCaS-1.dat``, which uses the ``SUBI`` model)
are reported and skipped rather than counted as failures, so a full corpus of 26
databases yields 25 regenerated files.

Exit codes::

    0   every readable fixture was regenerated
    1   a fixture could not be read or written
    2   bad usage (missing or empty source directory)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Sequence, Tuple

import calphad_io
from calphad_io.errors import CalphadIOError, UnsupportedFeatureError

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

#: Extensions this library can read, matching ``tests/conftest.py``.
READABLE_SUFFIXES: Tuple[str, ...] = (".tdb", ".dat")

DEFAULT_ORIGINALS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "data"
)


def readable_files(directory: str) -> List[str]:
    """Every readable database in ``directory``, sorted by name."""
    return sorted(
        name
        for name in os.listdir(directory)
        if name.lower().endswith(READABLE_SUFFIXES)
        and os.path.isfile(os.path.join(directory, name))
    )


def regenerate(originals: str, target: str) -> Tuple[int, int, int]:
    """Round-trip every fixture in ``originals`` into ``target``.

    Returns ``(written, skipped, failed)``.
    """
    os.makedirs(target, exist_ok=True)
    written = skipped = failed = 0
    for name in readable_files(originals):
        source = os.path.join(originals, name)
        destination = os.path.join(target, name)
        try:
            database = calphad_io.load(source)
        except UnsupportedFeatureError as exc:
            skipped += 1
            print(f"SKIPPED {name}: refused by design ({exc.feature})")
            continue
        except CalphadIOError as exc:
            failed += 1
            print(f"FAILED  {name}: {exc}")
            continue
        try:
            with open(destination, "w", encoding="utf-8", newline="") as handle:
                handle.write(calphad_io.dumps(database))
        except (CalphadIOError, OSError) as exc:
            failed += 1
            print(f"FAILED  {name}: {exc}")
            continue
        written += 1
    return written, skipped, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regenerate the fixture databases with calphad-io.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"``originals`` defaults to {DEFAULT_ORIGINALS}",
    )
    parser.add_argument(
        "originals",
        nargs="?",
        default=DEFAULT_ORIGINALS,
        metavar="ORIGINALS",
        help="directory holding the original databases",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="directory to write the regenerated databases into",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.isdir(args.originals):
        print(f"error: not a directory: {args.originals}", file=sys.stderr)
        return EXIT_USAGE
    if not readable_files(args.originals):
        print(f"error: no .TDB or .DAT files in {args.originals}", file=sys.stderr)
        return EXIT_USAGE

    written, skipped, failed = regenerate(args.originals, args.out)
    print(f"\nGENERATED={written} SKIPPED={skipped} FAILED={failed}")
    print(f"originals:   {args.originals}")
    print(f"regenerated: {args.out}")
    return EXIT_FAILURE if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
