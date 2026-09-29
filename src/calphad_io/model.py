"""The documented object model shared by both file formats.

Both ``.TDB`` (Thermo-Calc) and ``.DAT`` (ChemSage/FactSage) describe the same
things -- elements, species, phases, parameters -- with different syntax.  This
module defines one set of frozen dataclasses that both readers populate and both
writers consume.

Design rules
------------
* **Immutable.** Every model object is a frozen dataclass.  Readers build them
  once; nothing mutates them afterwards.  Transformations return new objects.
* **Faithful.** A model records what the file said, not what a thermodynamic
  evaluator would make of it.  In particular, parameter expressions are kept as
  source text; this library never evaluates them.
* **Lossless where it can be.** TDB keeps the original source span of every
  command so an unmodified database can be re-emitted byte-for-byte.  DAT keeps
  every numeric field exactly as a float, so rewriting never loses precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Mapping, Tuple

__all__ = [
    "Database",
    "Element",
    "Format",
    "Function",
    "Parameter",
    "Phase",
    "Species",
    "TypeDefinition",
]


class Format:
    """Format identifiers used throughout the library."""

    TDB = "tdb"
    DAT = "dat"

    ALL: Tuple[str, ...] = (TDB, DAT)

    @staticmethod
    def normalise(value: str) -> str:
        """Map a file extension or format name onto a canonical identifier."""
        v = value.strip().lower().lstrip(".")
        if v in ("tdb", "tdb5", "thermo-calc", "thermocalc"):
            return Format.TDB
        if v in ("dat", "chemsage", "factsage", "solgasmix"):
            return Format.DAT
        raise ValueError(f"unknown format {value!r}; expected one of {Format.ALL}")


@dataclass(frozen=True)
class Element:
    """A pure element (or the electron/vacuum pseudo-elements)."""

    name: str
    """Element symbol, upper-cased, e.g. ``"FE"``."""

    reference_phase: str = ""
    """Reference phase name from the TDB ``ELEMENT`` command (``"BLANK"`` for DAT)."""

    mass: float = 0.0
    """Standard atomic weight in g/mol."""

    h298: float = 0.0
    """Enthalpy of formation at 298.15 K, J/mol."""

    s298: float = 0.0
    """Standard entropy at 298.15 K, J/(mol K)."""

    @property
    def is_pseudo(self) -> bool:
        """True for ``VA`` (vacuum), ``/-`` (electron gas) and ``/-``-like names.

        These are not chemical elements but they occupy element slots in every
        CALPHAD database, so they must be modelled rather than dropped.
        """
        return self.name in ("VA", "ELECTRON_GAS", "/-") or self.name.startswith("/")


@dataclass(frozen=True)
class Species:
    """A named species: an element, a molecule, or a charged ion."""

    name: str
    """Species name as written, e.g. ``"FE+2"``, ``"H2O"``."""

    stoichiometry: Dict[str, float] = field(default_factory=dict)
    """Element composition, e.g. ``{"FE": 1.0, "O": 2.0}``. May be empty."""

    charge: float = 0.0
    """Electrical charge in units of the elementary charge."""


@dataclass(frozen=True)
class Sublattice:
    """One sublattice of a phase: a set of allowed constituents and a ratio."""

    constituents: Tuple[str, ...]
    """Constituent names as written in the file, e.g. ``("FE", "CR", "VA")``."""

    ratio: float = 1.0
    """Stoichiometric ratio (TDB: from the ``PHASE`` command)."""


@dataclass(frozen=True)
class Phase:
    """A phase and its sublattice model."""

    name: str
    """Phase name, e.g. ``"FCC_A1"``."""

    sublattices: Tuple[Sublattice, ...] = ()
    """The sublattice model. Empty if the file never gave a ``CONSTITUENT`` line."""

    type_code: str = ""
    """TDB type-definition string, e.g. ``"%&"``; empty for DAT."""

    model: str = ""
    """DAT model keyword, e.g. ``"SUBG"``, ``"QKTO"``; empty for TDB."""

    is_dummy: bool = False
    """True for ChemSage ``#`` dummy phases (see pycalphad issue #417)."""

    kind: str = ""
    """Record family: ``"cef"``, ``"mqmqa"``, ``"stoichiometric"`` or ``""``.

    Empty when the phase was built without a layout, e.g. by hand.
    """

    is_stoichiometric: bool = False
    """True when the phase is a stoichiometric compound, not a solution phase."""

    def constituent_names(self) -> Tuple[Tuple[str, ...], ...]:
        """All sublattice constituent tuples, in order."""
        return tuple(s.constituents for s in self.sublattices)

    def references(self) -> Tuple[str, ...]:
        """Every constituent name mentioned by this phase."""
        out: List[str] = []
        for subl in self.sublattices:
            out.extend(subl.constituents)
        return tuple(out)


@dataclass(frozen=True)
class Parameter:
    """A thermodynamic parameter (Gibbs energy, interaction, magnetic term, ...).

    ``expression`` is kept as source text.  This library does not parse or
    evaluate thermodynamic expressions; it only checks that they are
    well-formed enough to be re-emitted.
    """

    kind: str
    """Parameter kind, e.g. ``"G"``, ``"L"``, ``"TC"``, ``"BM"``, ``"MQMG"``."""

    phase: str
    """Name of the phase this parameter belongs to."""

    constituents: Tuple[Tuple[str, ...], ...] = ()
    """Constituent array, one tuple per sublattice, e.g. ``(("FE",), ("VA",))``."""

    order: int | None = None
    """Redlich-Kister / interaction order, e.g. ``0`` in ``G(FCC_A1,AL;0)``."""

    expression: str = ""
    """The expression source text, including its ``; T_max N`` tails."""

    source_line: int | None = None
    """1-based line number in the source file, for diagnostics."""

    def signature(self) -> str:
        """A canonical one-line identity for this parameter.

        Used by the validator to detect duplicates and by tests to compare
        databases without depending on formatting.
        """
        cons = ":".join(",".join(s) for s in self.constituents)
        order = "" if self.order is None else f";{self.order}"
        return f"{self.kind}({self.phase},{cons}{order})"


@dataclass(frozen=True)
class TypeDefinition:
    """A TDB ``TYPE_DEFINITION`` directive."""

    symbol: str
    """The single character used in ``PHASE`` type codes, e.g. ``"%"`` or ``"&"``."""

    kind: str
    """Directive family, e.g. ``"SEQ"`` or ``"GES"``."""

    body: str = ""
    """Everything after the kind, verbatim."""

    options: Tuple[str, ...] = ()
    """For ``GES``: the whitespace-separated option tokens."""

    source_line: int | None = None


@dataclass(frozen=True)
class Function:
    """A TDB ``FUNCTION``, i.e. a named expression reusable inside parameters."""

    name: str
    expression: str
    source_line: int | None = None


@dataclass(frozen=True)
class Database:
    """A parsed CALPHAD database.

    This is the single object returned by every reader and accepted by every
    writer.  It is immutable: use :meth:`with_phase`, :meth:`with_parameter` or
    :func:`dataclasses.replace` to derive a modified copy.
    """

    format: str
    """Which format this database came from: :attr:`Format.TDB` or :attr:`Format.DAT`."""

    source: str | None = None
    """Name of the file it was read from, if any."""

    elements: Tuple[Element, ...] = ()
    """Declared elements, in declaration order."""

    species: Tuple[Species, ...] = ()
    """Declared species (TDB only; DAT encodes these implicitly)."""

    phases: Tuple[Phase, ...] = ()
    """Declared phases, in declaration order."""

    parameters: Tuple[Parameter, ...] = ()
    """All parameters, in declaration order."""

    functions: Tuple[Function, ...] = ()
    """Named functions (TDB only)."""

    type_definitions: Tuple[TypeDefinition, ...] = ()
    """Type definitions (TDB only)."""

    comments: Tuple[str, ...] = ()
    """Free-text lines that are not part of any record."""

    header: Mapping[str, object] = field(default_factory=dict)
    """Format-specific scalar header data.

    TDB stores ``system_default`` and ``default_commands`` here.  DAT stores the
    system title, the coefficient-index rows, and -- under the key
    ``"layout"`` -- the full :class:`~calphad_io.dat.layout.DatLayout`, which is
    what makes a canonical re-render possible.
    """

    raw: str | None = None
    """For TDB: the original text, kept so an unmodified database round-trips
    byte-for-byte.  ``None`` for databases that were constructed or modified."""

    # -- convenience accessors -------------------------------------------------

    def element_names(self) -> Tuple[str, ...]:
        """Names of all declared elements, in order."""
        return tuple(e.name for e in self.elements)

    def phase(self, name: str) -> Phase | None:
        """Look up a phase by name (case-insensitive). Returns ``None`` if absent."""
        target = name.upper()
        for p in self.phases:
            if p.name.upper() == target:
                return p
        return None

    def parameters_for(self, phase_name: str) -> Tuple[Parameter, ...]:
        """All parameters belonging to a phase (case-insensitive)."""
        target = phase_name.upper()
        return tuple(p for p in self.parameters if p.phase.upper() == target)

    def type_definition(self, symbol: str) -> TypeDefinition | None:
        """Look up a type definition by its symbol character."""
        for td in self.type_definitions:
            if td.symbol == symbol:
                return td
        return None

    def with_phase(self, phase: Phase) -> Database:
        """Return a copy with ``phase`` added or replacing one of the same name."""
        target = phase.name.upper()
        kept = tuple(p for p in self.phases if p.name.upper() != target)
        return replace(self, phases=(*kept, phase))

    def with_parameter(self, parameter: Parameter) -> Database:
        """Return a copy with ``parameter`` appended."""
        return replace(self, parameters=(*self.parameters, parameter))

    def summary(self) -> Dict[str, object]:
        """A small dict of counts, for ``info`` output and quick inspection."""
        return {
            "format": self.format,
            "source": self.source,
            "elements": len(self.elements),
            "species": len(self.species),
            "phases": len(self.phases),
            "parameters": len(self.parameters),
            "functions": len(self.functions),
            "type_definitions": len(self.type_definitions),
            "solution_phases": sum(1 for p in self.phases if not p.is_stoichiometric),
            "stoichiometric_phases": sum(1 for p in self.phases if p.is_stoichiometric),
            "dummy_phases": sum(1 for p in self.phases if p.is_dummy),
        }
