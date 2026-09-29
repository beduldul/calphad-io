#!/usr/bin/env python3
"""Check regenerated databases against the originals with pycalphad.

``calphad-io`` claims that an independent reader -- pycalphad -- sees the files
it writes as identical to the originals.  This script makes that claim
checkable: for every regenerated file it builds a structural fingerprint with
pycalphad's own ``Database`` and compares it against the fingerprint of the
original.

The fingerprint covers the whole model pycalphad exposes: elements, species,
phases and their sublattice constituents, model hints, and every parameter
(``phase_name``, ``parameter_type``, ``parameter_order``,
``constituent_array``, ``parameter``, ``tzero``).

This script needs pycalphad, which is *not* a dependency of ``calphad-io``.
Run it with a separate virtualenv that has pycalphad installed; see
``scripts/README.md``.  Typical use, after :mod:`scripts.generate`:

    python scripts/generate.py --out /tmp/calphad-roundtrip
    /tmp/calphad-refenv/bin/python scripts/verify.py \\
        tests/data /tmp/calphad-roundtrip

A clean corpus prints ``IDENTICAL=25 DIFFERS=0 SKIPPED=0``.

Exit codes::

    0   every regenerated file matches its original
    1   at least one file differs, or a regenerated file could not be read
    2   pycalphad is not installed, or the arguments are unusable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from typing import TYPE_CHECKING, Dict, List, Sequence, Tuple

if TYPE_CHECKING:  # pycalphad is not a dependency; only the type is borrowed.
    from pycalphad import Database as PycalphadDatabase

EXIT_OK = 0
EXIT_DIFFERS = 1
EXIT_USAGE = 2

READABLE_SUFFIXES: Tuple[str, ...] = (".tdb", ".dat")


def load_pycalphad() -> type[PycalphadDatabase]:
    """Return pycalphad's ``Database``, or explain how to install it.

    pycalphad is deliberately not a dependency of this library, so the import
    happens here rather than at module scope.
    """
    try:
        import pycalphad
        from pycalphad import Database
    except ImportError:  # pragma: no cover - depends on the environment
        print(
            "error: pycalphad is not importable.\n"
            "This script must run under a virtualenv that has pycalphad, "
            "separate from\ncalphad-io's own. See scripts/README.md.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_USAGE) from None
    print(f"pycalphad {pycalphad.__version__}")
    return Database


def fingerprint(database_type: type[PycalphadDatabase], path: str) -> Dict[str, object]:
    """A structural fingerprint of the database at ``path``.

    Two databases with the same fingerprint describe the same model.  Ordering
    is normalised throughout, so record order is not part of the comparison.
    """
    database = database_type(path)
    return {
        "elements": sorted(str(e) for e in database.elements),
        "species": sorted(str(s) for s in database.species),
        "phases": {
            name: sorted(sorted(str(s) for s in sub) for sub in phase.constituents)
            for name, phase in sorted(database.phases.items())
        },
        "hints": {
            name: sorted(str(v) for v in phase.model_hints.values())
            for name, phase in sorted(database.phases.items())
        },
        # ``_parameters`` is pycalphad's internal parameter container; its
        # public surface has no equivalent iterator, so it is used deliberately.
        "params": sorted(
            json.dumps(
                {
                    "phase": p["phase_name"],
                    "type": p["parameter_type"],
                    "order": p.get("parameter_order"),
                    "const": [sorted(str(c) for c in cs) for cs in p.get("constituent_array", [])],
                    "param": str(p.get("parameter")),
                    "tzero": str(p.get("tzero")),
                },
                sort_keys=True,
            )
            for p in database._parameters.all()
        ),
    }


def readable_files(directory: str) -> List[str]:
    """Every readable database in ``directory``, sorted by name."""
    return sorted(
        name
        for name in os.listdir(directory)
        if name.lower().endswith(READABLE_SUFFIXES)
        and os.path.isfile(os.path.join(directory, name))
    )


def compare(
    originals: str,
    regenerated: str,
    database_type: type[PycalphadDatabase],
) -> Tuple[int, int, int, List[str]]:
    """Compare every regenerated file against its original.

    Returns ``(identical, differs, skipped, not_regenerated)``.  ``skipped``
    counts files pycalphad itself could not read; ``not_regenerated`` lists
    originals calphad-io refused, which therefore have no counterpart here.
    """
    names = readable_files(regenerated)
    not_regenerated = sorted(set(readable_files(originals)) - set(names))
    identical = differs = skipped = 0

    for name in names:
        try:
            expected = fingerprint(database_type, os.path.join(originals, name))
        except Exception as exc:  # pycalphad failed on the original
            skipped += 1
            print(f"SKIP (original unreadable by pycalphad) {name}: {str(exc)[:70]}")
            continue
        try:
            actual = fingerprint(database_type, os.path.join(regenerated, name))
        except Exception as exc:  # pycalphad failed on our output
            differs += 1
            print(f"DIFFERS (regenerated unreadable) {name}: {str(exc)[:70]}")
            continue
        if expected == actual:
            identical += 1
        else:
            differs += 1
            print(f"DIFFERS: {name}")

    return identical, differs, skipped, not_regenerated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare regenerated databases with the originals via pycalphad.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("originals", help="directory holding the original databases")
    parser.add_argument("regenerated", help="directory holding calphad-io's output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for label, directory in (("originals", args.originals), ("regenerated", args.regenerated)):
        if not os.path.isdir(directory):
            print(f"error: {label} is not a directory: {directory}", file=sys.stderr)
            return EXIT_USAGE

    warnings.filterwarnings("ignore")
    database_type = load_pycalphad()
    identical, differs, skipped, not_regenerated = compare(
        args.originals, args.regenerated, database_type
    )
    if not_regenerated:
        print(
            f"not regenerated by calphad-io ({len(not_regenerated)}): {', '.join(not_regenerated)}"
        )
    print(f"\nIDENTICAL={identical} DIFFERS={differs} SKIPPED={skipped}")
    return EXIT_DIFFERS if differs else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
