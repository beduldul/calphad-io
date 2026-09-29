"""Rich layout records for ChemSage ``.DAT`` files.

The generic object model in :mod:`calphad_io.model` is deliberately format-neutral:
it records elements, phases and parameters.  That is enough to *validate* a
database, but it is not enough to *re-render* one, because the DAT format
interleaves information the generic model has nowhere to put -- thermodynamic
data options per endmember, P-T molar-volume terms, magnetic factors, QKTO
chemical groups, MQMQA quadruplet coordinations, and so on.

These records keep everything.  A database read from DAT carries a
:class:`DatLayout` under ``database.header["layout"]``, and the writer renders
from it.  Everything here is a frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

__all__ = [
    "AdditionalTerm",
    "DatHeader",
    "DatLayout",
    "Endmember",
    "ExcessTerm",
    "Interval",
    "MagneticExcessTerm",
    "PhaseLayout",
    "Quadruplet",
    "SubqExcessTerm",
]

#: Phase model keywords, grouped by how their bodies are laid out.
CEF_MODELS = frozenset({"IDMX", "RKMP", "RKMPM", "QKTO", "SUBL", "SUBLM"})
MQMQA_MODELS = frozenset({"SUBQ", "SUBG"})
MAGNETIC_MODELS = frozenset({"RKMPM", "SUBLM"})
MAGNETIC_CAPABLE = frozenset({"RKMP", "RKMPM", "SUBL", "SUBLM", "QKTO", "SUBG", "SUBQ"})

#: Thermodynamic data options that carry additional coefficient pairs.
OPTIONS_WITH_ADDITIONAL = frozenset({4, 5, 6, 10, 11, 12})
#: Thermodynamic data options that carry P-T molar-volume terms.
OPTIONS_WITH_PTVM = frozenset({3, 6, 9, 12})
#: Thermodynamic data options expressing constant molar volume (unsupported).
OPTIONS_WITH_CONSTANT_VM = frozenset({2, 5, 8, 11})
#: Thermodynamic data options that are Gibbs-energy intervals.
OPTIONS_GIBBS = frozenset({1, 2, 3, 4, 5, 6})
#: Thermodynamic data options that are heat-capacity intervals (unsupported).
OPTIONS_HEAT_CAPACITY = frozenset({7, 8, 9, 10, 11, 12})


@dataclass(frozen=True)
class DatHeader:
    """The fixed-width prologue of a DAT file."""

    title: str = ""
    """The free-text first line, e.g. ``" System O-H"``."""

    num_elements: int = 0
    num_solution_phases: int = 0
    solution_phase_species_counts: Tuple[int, ...] = ()
    """Species count per solution phase; a ``0`` means an absent gas phase."""

    num_stoichiometric_phases: int = 0
    elements: Tuple[str, ...] = ()
    masses: Tuple[float, ...] = ()
    gibbs_coefficient_indices: Tuple[int, ...] = ()
    excess_coefficient_indices: Tuple[int, ...] = ()

    def is_absent_gas(self, index: int) -> bool:
        """True if solution phase ``index`` is a placeholder for an absent gas."""
        return (
            0 <= index < len(self.solution_phase_species_counts)
            and self.solution_phase_species_counts[index] == 0
        )


@dataclass(frozen=True)
class AdditionalTerm:
    """One additional coefficient/exponent pair inside an interval."""

    coefficient: float
    exponent: float


@dataclass(frozen=True)
class Interval:
    """One temperature interval of an endmember's Gibbs energy."""

    t_max: float
    coefficients: Tuple[float, ...] = ()
    additional_terms: Tuple[AdditionalTerm, ...] = ()
    ptvm_terms: Tuple[float, ...] = ()


@dataclass(frozen=True)
class Endmember:
    """One endmember of a solution phase, or a whole stoichiometric compound."""

    species: str
    """Species name exactly as written, e.g. ``"Fe2O3"``, ``"NaCl"``."""

    option: int
    """Thermodynamic data option (1-24).  Values above 12 imply magnetic data."""

    stoichiometry: Tuple[float, ...] = ()
    """Composition, one value per database element, in header element order."""

    intervals: Tuple[Interval, ...] = ()
    is_dummy: bool = False
    """True if the ``#`` dummy marker followed the species name."""

    curie_temperature: float | None = None
    magnetic_moment: float | None = None
    quadruplet_stoichiometry: Tuple[float, ...] = ()
    """MQMQA only: the five extra pair-stoichiometry values."""

    zeta: float | None = None
    """QKTO stoichiometric factor, or the per-pair MQMQA zeta."""

    chemical_group: int | None = None
    """QKTO chemical group index."""

    @property
    def is_magnetic(self) -> bool:
        """True when the option value encodes a magnetic contribution."""
        return self.option > 12

    @property
    def reduced_option(self) -> int:
        """The option with the magnetic offset removed."""
        return self.option - 12 if self.is_magnetic else self.option


@dataclass(frozen=True)
class ExcessTerm:
    """One Redlich-Kister style excess parameter."""

    num_interacting: int
    species_indices: Tuple[int, ...]
    order: int
    coefficients: Tuple[float, ...] = ()
    exponents: Tuple[int, ...] = ()
    """QKTO only: the Kohler-Toop exponents."""


@dataclass(frozen=True)
class MagneticExcessTerm:
    """One magnetic excess parameter (Curie temperature / moment)."""

    species_indices: Tuple[int, ...]
    order: int
    curie_temperature: float
    magnetic_moment: float


@dataclass(frozen=True)
class Quadruplet:
    """MQMQA quadruplet coordination data."""

    indices: Tuple[int, ...]
    coordinations: Tuple[float, ...]


@dataclass(frozen=True)
class SubqExcessTerm:
    """One MQMQA excess (quadruplet mixing) term."""

    mixing_type: int
    mixing_code: str
    mixing_constants: Tuple[int, ...] = ()
    mixing_exponents: Tuple[int, ...] = ()
    metadata: Tuple[float, ...] = ()
    additional_cation: int = 0
    additional_anion: int = 0
    coefficients: Tuple[float, ...] = ()


@dataclass(frozen=True)
class PhaseLayout:
    """Everything the DAT file said about one phase."""

    name: str
    model: str
    kind: str
    """``"cef"``, ``"mqmqa"`` or ``"stoichiometric"``."""

    endmembers: Tuple[Endmember, ...] = ()
    sublattice_ratios: Tuple[float, ...] = ()
    sublattice_constituents: Tuple[Tuple[str, ...], ...] = ()
    endmember_constituent_indices: Tuple[Tuple[int, ...], ...] = ()
    excess_terms: Tuple[ExcessTerm, ...] = ()
    magnetic_excess_terms: Tuple[MagneticExcessTerm, ...] = ()
    magnetic_afm_factor: float | None = None
    magnetic_structure_factor: float | None = None
    num_pairs: int = 0
    num_quadruplets: int = 0
    zeta: float | None = None
    quadruplets: Tuple[Quadruplet, ...] = ()
    subq_excess_terms: Tuple[SubqExcessTerm, ...] = ()
    mqmqa_sublattices: Tuple[Tuple[Tuple[str, ...], Tuple[float, ...], Tuple[int, ...]], ...] = ()
    """MQMQA only: per sublattice, its (constituents, charges, chemical groups)."""

    mqmqa_pair_indices: Tuple[Tuple[int, ...], ...] = ()
    """MQMQA only: the two flat index columns pairing sublattice-1 to sublattice-2."""

    chemical_group_overrides: Tuple[str, ...] = ()
    """Verbatim override strings; each is ten tokens that are preserved as text."""


@dataclass(frozen=True)
class DatLayout:
    """The complete, re-renderable description of a DAT file."""

    header: DatHeader
    phases: Tuple[PhaseLayout, ...] = ()
    trailing: str = ""
    """Text after the last record, preserved so it is not silently dropped."""
