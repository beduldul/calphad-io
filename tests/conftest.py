"""Shared test fixtures: the real databases, and helpers for round-tripping."""

from __future__ import annotations

import os
from typing import List, Tuple

import pytest

import calphad_io
from calphad_io.model import Database

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CORRUPT_DIR = os.path.join(DATA_DIR, "corrupt")

TDB_FILES: List[str] = sorted(
    f for f in os.listdir(DATA_DIR) if f.lower().endswith(".tdb")
)
DAT_FILES: List[str] = sorted(
    f for f in os.listdir(DATA_DIR) if f.lower().endswith(".dat")
)
ALL_FILES: List[str] = TDB_FILES + DAT_FILES

#: Files this library deliberately refuses.  Each entry is
#: ``(filename, expected_exception, expected_feature_substring)``.
UNSUPPORTED: List[Tuple[str, str, str]] = [
    ("FeMnCaS-1.dat", "UnsupportedFeatureError", "SUBI"),
]


def data_path(name: str) -> str:
    """Absolute path of a fixture database."""
    return os.path.join(DATA_DIR, name)


def read_bytes(name: str) -> bytes:
    """Raw bytes of a fixture database."""
    with open(data_path(name), "rb") as handle:
        return handle.read()


def read_text(name: str) -> str:
    """Fixture database decoded the same way :func:`calphad_io.load` does."""
    return read_bytes(name).decode("utf-8", errors="replace")


def fingerprint(database: Database) -> Tuple[object, ...]:
    """A structural fingerprint of a database.

    Two databases with the same fingerprint describe the same model, ignoring
    formatting and record order.  Used to assert round-trip fidelity.
    """
    return (
        database.format,
        tuple(sorted(database.element_names())),
        tuple(sorted(p.name.upper() for p in database.phases)),
        tuple(
            sorted(
                (
                    p.name.upper(),
                    p.kind,
                    p.model,
                    tuple(sorted(tuple(s.constituents) for s in p.sublattices)),
                )
                for p in database.phases
            )
        ),
        tuple(sorted(p.signature() for p in database.parameters)),
        tuple(sorted(f.name for f in database.functions)),
        tuple(sorted(t.name for t in database.type_definitions)),
    )


def roundtrip(database: Database) -> Database:
    """Write a database and read it back."""
    text = calphad_io.dumps(database)
    return calphad_io.loads(text, format=database.format)


@pytest.fixture(params=TDB_FILES, ids=TDB_FILES)
def tdb_file(request: pytest.FixtureRequest) -> str:
    """Parametrised over every real TDB fixture."""
    return str(request.param)


@pytest.fixture(params=DAT_FILES, ids=DAT_FILES)
def dat_file(request: pytest.FixtureRequest) -> str:
    """Parametrised over every real DAT fixture."""
    return str(request.param)


@pytest.fixture(params=ALL_FILES, ids=ALL_FILES)
def any_file(request: pytest.FixtureRequest) -> str:
    """Parametrised over every real fixture, both formats."""
    return str(request.param)
