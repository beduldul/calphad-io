"""ChemSage / FactSage ``.DAT`` support.

A thin facade over :mod:`calphad_io.dat.reader` and :mod:`calphad_io.dat.writer`.
Import those modules directly if you want the pieces; import this one if you
just want ``read`` and ``write``.

Layout summary
--------------
A DAT file is a flat token stream.  After a free-text title line it carries a
header (element count, solution-phase species counts, stoichiometric-phase
count, element names, atomic masses, Gibbs coefficient indices, excess
coefficient indices) followed by one block per solution phase and one block per
stoichiometric compound.

The amount of data each block consumes depends on the header's coefficient index
counts and on each endmember's *thermodynamic data option*.  Options 1-6 are
Gibbs-energy intervals; 7-12 are heat-capacity intervals; adding 12 to either
selects the magnetic variant.  Options 4-6 and 10-12 add a trailing "number of
additional coefficient pairs" line; options 3, 6, 9 and 12 add eleven P-T
molar-volume terms.

Round-trip fidelity
-------------------
Parsing then writing produces a **semantically identical** file: every element,
phase, endmember, interval coefficient, excess term and magnetic term is
preserved exactly, and the writer emits values in a form that converts back to
the identical :class:`float`.  Formatting and comments are *not* preserved --
DAT files are hand-edited in practice, and no reader in the ecosystem depends on
column positions.
"""

from .layout import DatHeader, DatLayout, Endmember, Interval, PhaseLayout
from .reader import read, read_layout
from .writer import format_integer, format_number, write, write_layout

__all__ = [
    "DatHeader",
    "DatLayout",
    "Endmember",
    "Interval",
    "PhaseLayout",
    "format_integer",
    "format_number",
    "read",
    "read_layout",
    "write",
    "write_layout",
]
