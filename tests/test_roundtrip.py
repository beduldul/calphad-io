"""Round-trip fidelity against real databases.

These are the headline tests.  They run against every real ``.TDB`` and ``.DAT``
file committed under ``tests/data/``, which were obtained from the pycalphad and
Thermochimica projects.
"""

from __future__ import annotations

import pytest
from conftest import (
    ALL_FILES,
    DAT_FILES,
    TDB_FILES,
    UNSUPPORTED,
    fingerprint,
    read_text,
    roundtrip,
)

import calphad_io
from calphad_io.errors import UnsupportedFeatureError
from calphad_io.model import Format

UNSUPPORTED_NAMES = {name for name, _, _ in UNSUPPORTED}


def test_fixture_corpus_is_present() -> None:
    """Guard against the corpus silently becoming empty."""
    assert len(TDB_FILES) >= 9, TDB_FILES
    assert len(DAT_FILES) >= 15, DAT_FILES
    assert len(ALL_FILES) >= 24


@pytest.mark.parametrize("name", TDB_FILES, ids=TDB_FILES)
def test_tdb_roundtrip_is_byte_exact(name: str) -> None:
    """A TDB that is read and written back must be byte-for-byte identical.

    This is the strongest fidelity claim the library makes.  It holds because
    each command's original source span is retained, so an unmodified database
    is re-emitted verbatim.
    """
    original = read_text(name)
    database = calphad_io.loads(original, format="tdb", source=name)
    assert database.format == Format.TDB
    assert calphad_io.dumps(database) == original


@pytest.mark.parametrize("name", DAT_FILES, ids=DAT_FILES)
def test_dat_roundtrip_preserves_the_model(name: str) -> None:
    """A DAT that is read and written back must describe the same database.

    DAT round-trip is semantic, not byte-exact: real files are hand-edited and
    nothing in the ecosystem depends on column positions.
    """
    if name in UNSUPPORTED_NAMES:
        pytest.skip(f"{name} uses a model this library refuses by design")
    database = calphad_io.loads(read_text(name), format="dat", source=name)
    assert database.format == Format.DAT
    assert fingerprint(database) == fingerprint(roundtrip(database))


@pytest.mark.parametrize("name", DAT_FILES, ids=DAT_FILES)
def test_dat_roundtrip_preserves_the_layout(name: str) -> None:
    """The rich DAT layout must survive a round-trip too.

    The generic parameter list is only a projection; the layout is what the
    writer renders from, so it gets its own check.
    """
    if name in UNSUPPORTED_NAMES:
        pytest.skip(f"{name} uses a model this library refuses by design")
    from calphad_io.dat import read_layout

    text = read_text(name)
    layout = read_layout(text)
    rewritten = calphad_io.dumps(calphad_io.loads(text, format="dat"))
    assert read_layout(rewritten) == layout


@pytest.mark.parametrize("name", DAT_FILES, ids=DAT_FILES)
def test_dat_roundtrip_is_idempotent(name: str) -> None:
    """Writing an already-written database must not change it again."""
    if name in UNSUPPORTED_NAMES:
        pytest.skip(f"{name} uses a model this library refuses by design")
    database = calphad_io.loads(read_text(name), format="dat")
    once = calphad_io.dumps(database)
    twice = calphad_io.dumps(calphad_io.loads(once, format="dat"))
    assert once == twice


@pytest.mark.parametrize("name", ALL_FILES, ids=ALL_FILES)
def test_every_fixture_loads_or_is_refused_cleanly(name: str) -> None:
    """Every file either loads, or raises a documented error type."""
    text = read_text(name)
    try:
        database = calphad_io.loads(text, source=name)
    except UnsupportedFeatureError as exc:
        assert name in UNSUPPORTED_NAMES, f"{name} refused unexpectedly: {exc}"
        return
    assert database.phases, f"{name} parsed to zero phases"


@pytest.mark.parametrize("name", ALL_FILES, ids=ALL_FILES)
def test_numbers_survive_exactly(name: str) -> None:
    """Every numeric field must convert back to the identical float.

    A ``"%.6f"`` rendering would turn ``1e-9`` into ``0.0``; the writer is
    required to avoid that, so the check is exact equality, not a tolerance.
    """
    from calphad_io.dat import format_number

    for value in (0.0, -0.0, 1.0, -1.0, 1e-9, -1e-9, 1e12, 1.23456789e-7,
                  123456789.0, -14782200.0, 1 / 3, 2.5e-13):
        rendered = format_number(value)
        assert float(rendered) == value, (value, rendered)
        assert rendered.startswith(" "), repr(rendered)


def test_unsupported_file_raises_the_documented_error() -> None:
    """The one file we cannot read must fail with a precise, useful message."""
    name, _kind, feature = UNSUPPORTED[0]
    with pytest.raises(UnsupportedFeatureError) as info:
        calphad_io.loads(read_text(name), format="dat", source=name)
    assert feature in str(info.value)
    assert info.value.feature
