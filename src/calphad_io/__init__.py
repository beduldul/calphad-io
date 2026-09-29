"""The public API of :mod:`calphad_io`.

Typical use::

    import calphad_io

    db = calphad_io.load("steel.TDB")
    print(db.summary())

    for finding in calphad_io.validate(db):
        print(finding)

    calphad_io.dump(db, "copy.TDB")

The two formats are also importable directly::

    from calphad_io import tdb, dat
    db = tdb.read(open("steel.TDB").read())
"""

from __future__ import annotations

import os
from typing import Sequence

from . import dat, tdb
from .errors import (
    CalphadIOError,
    ParseError,
    UnsupportedFeatureError,
    ValidationError,
    WriteError,
)
from .model import (
    Database,
    Element,
    Format,
    Function,
    Parameter,
    Phase,
    Species,
    Sublattice,
    TypeDefinition,
)
from .validate import Finding, Severity, validate

__version__ = "0.1.0"

__all__ = [
    "CalphadIOError",
    "Database",
    "Element",
    "Finding",
    "Format",
    "Function",
    "Parameter",
    "ParseError",
    "Phase",
    "Severity",
    "Species",
    "Sublattice",
    "TypeDefinition",
    "UnsupportedFeatureError",
    "ValidationError",
    "WriteError",
    "__version__",
    "convert",
    "dat",
    "detect_format",
    "dump",
    "dumps",
    "load",
    "loads",
    "tdb",
    "validate",
]

#: Formats tried, in order, when the format cannot be determined by extension.
_PROBE_ORDER = (Format.TDB, Format.DAT)


def detect_format(text: str, *, filename: str | None = None) -> str:
    """Work out whether ``text`` is TDB or DAT.

    The file extension wins if it is recognisable.  Otherwise the content is
    sniffed: TDB is a sequence of ``!``-terminated commands with upper-case
    keywords, whereas DAT begins with a free-text title line followed by a line
    of bare integers.

    Raises
    ------
    ValueError
        If neither format matches.
    """
    if filename:
        extension = os.path.splitext(filename)[1].lstrip(".")
        if extension:
            try:
                return Format.normalise(extension)
            except ValueError:
                pass

    stripped = text.lstrip()
    if not stripped:
        raise ValueError("cannot detect the format of an empty file")

    upper = stripped.upper()
    for keyword in ("ELEMENT", "PHASE", "PARAMETER", "FUNCTION", "TYPE_DEFINITION"):
        if upper.startswith(keyword) or f"\n{keyword} " in upper:
            return Format.TDB
    if "!" in text and not stripped.startswith(" System"):
        return Format.TDB

    # DAT: the second line is a row of bare integers.
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        tokens = lines[1].split()
        if tokens and all(_looks_like_int(t) for t in tokens):
            return Format.DAT
    raise ValueError("cannot determine whether this file is TDB or DAT")


def _looks_like_int(token: str) -> bool:
    try:
        int(token)
    except ValueError:
        return False
    return True


def loads(text: str, *, format: str | None = None, source: str | None = None) -> Database:
    """Parse database text.

    Parameters
    ----------
    text:
        The file contents.
    format:
        ``"tdb"`` or ``"dat"``.  If omitted it is detected from ``source`` or
        sniffed from the content.
    source:
        A name to record on the database and to use for format detection.
    """
    format = (
        detect_format(text, filename=source)
        if format is None
        else Format.normalise(format)
    )
    if format == Format.TDB:
        return tdb.read(text, source=source)
    return dat.read(text, source=source)


def load(path: os.PathLike[str] | str, *, format: str | None = None) -> Database:
    """Read a ``.TDB`` or ``.DAT`` file from disk.

    The file is decoded as UTF-8 with ``errors="replace"``: real databases in
    the wild contain stray non-UTF-8 bytes in comment lines, and refusing to
    read a file over a comment would be unhelpful.
    """
    name = os.fspath(path)
    with open(name, "rb") as handle:
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    return loads(text, format=format, source=name)


def dumps(database: Database, *, format: str | None = None) -> str:
    """Render a database to text.

    If ``format`` is given and differs from the database's own format, the
    conversion is attempted and, if it is not supported, a
    :class:`~calphad_io.errors.WriteError` is raised with an explanation.
    """
    if format is None or Format.normalise(format) == database.format:
        if database.format == Format.TDB:
            return tdb.write(database)
        return dat.write(database)
    return convert(database, format).text


def dump(database: Database, path: os.PathLike[str] | str, *, format: str | None = None) -> None:
    """Write a database to disk."""
    text = dumps(database, format=format)
    with open(os.fspath(path), "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


class ConversionResult:
    """The outcome of a format conversion."""

    __slots__ = ("database", "losses", "text")

    def __init__(self, database: Database, text: str, losses: Sequence[str]) -> None:
        self.database = database
        self.text = text
        self.losses = tuple(losses)
        """Human-readable descriptions of information the conversion dropped."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ConversionResult(losses={len(self.losses)})"


def convert(database: Database, to: str) -> ConversionResult:
    """Convert a database between formats, or canonicalise it in place.

    Passing the database's own format re-renders it in the library's canonical
    layout.  That is useful: it normalises whitespace and field widths without
    changing any value.

    **Cross-format conversion is refused, deliberately.**  Neither direction can
    be done faithfully from the information the other format carries:

    * ``DAT -> TDB`` loses QKTO chemical groups, MQMQA quadruplet coordinations
      and per-endmember thermodynamic data options -- there is nowhere in TDB to
      put them, so the model would be silently changed.
    * ``TDB -> DAT`` needs exactly those fields.  A DAT writer must know each
      endmember's thermodynamic data option, its P-T molar-volume terms and the
      sublattice atom count; a TDB records none of them.  Guessing produces a
      file that looks right and computes the wrong energy.

    A :class:`~calphad_io.errors.WriteError` explaining the specific loss is
    raised rather than emitting a file that would be quietly wrong.
    """
    target = Format.normalise(to)

    if target == database.format:
        return ConversionResult(database, dumps(database), ())

    if database.format == Format.DAT and target == Format.TDB:
        raise WriteError(
            "converting DAT to TDB is not supported: the ChemSage format carries "
            "model information (QKTO chemical groups, MQMQA quadruplet "
            "coordinations, per-endmember thermodynamic data options) that has no "
            "representation in TDB. Producing a TDB from it would silently "
            "discard the model."
        )

    raise WriteError(
        "converting TDB to DAT is not supported: a faithful DAT requires "
        "per-endmember thermodynamic data options, P-T molar-volume terms and "
        "sublattice atom counts, none of which are present in a TDB. This "
        "library will not guess them."
    )
