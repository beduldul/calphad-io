"""ChemSage ``.DAT`` writer.

Split out of :mod:`calphad_io.dat` so that neither module grows past a
comfortable size.  The reader lives in ``__init__.py``; everything that turns a
:class:`~calphad_io.dat.layout.DatLayout` back into text lives here.

The one rule that matters: **every field starts with a space and is padded to a
minimum width, never a maximum.**  A value needing more digits grows its field.
That is what makes two adjacent fields impossible to merge into one token, and
it is why the output is always unambiguously re-parseable -- including by this
library's own reader.

Field widths are a minimum rather than a fixed column, because real ChemSage
files are not rigidly column-aligned: values are whitespace-separated tokens
whose widths vary with magnitude (``1.2713704859E+06`` next to ``-73.506705``).
"""

from __future__ import annotations

from typing import List, Tuple

from ..errors import WriteError
from ..model import Database, Format
from .layout import (
    MAGNETIC_MODELS,
    OPTIONS_WITH_ADDITIONAL,
    OPTIONS_WITH_PTVM,
    DatLayout,
    Endmember,
    ExcessTerm,
    MagneticExcessTerm,
    PhaseLayout,
)

__all__ = ["format_integer", "format_number", "write", "write_layout"]

#: A field is rendered with a leading space and padded to at least this width.
#: The width is a *minimum*, not a limit: a value needing more digits grows its
#: field.  Because every field starts with a space, two adjacent fields can
#: never merge into one token, so the output is always unambiguously readable.
_FIELD_WIDTH = 16

_ELEMENTS_PER_LINE = 3


def format_number(value: float, width: int = _FIELD_WIDTH) -> str:
    """Render a float as an exact, whitespace-safe, fixed-minimum-width field.

    The output always converts back to exactly the same :class:`float`.  A plain
    ``"%.6f"`` rendering would silently destroy small magnitudes (``1e-9``
    becomes ``0.0``), which would make repeated round-trips drift.
    """
    inner = width - 1
    if value == 0.0:
        return " " + "0.000000".ljust(inner)

    # Prefer fixed point, then Python's shortest round-trip representation.
    for candidate in (f"{value:.6f}", repr(value)):
        text = candidate.replace("e", "E")
        if len(text) <= inner and float(text) == value:
            return " " + text.ljust(inner)

    # Otherwise keep the most significant digits that still round-trip exactly.
    best = f"{value:.1E}"
    for digits in range(16, 0, -1):
        text = f"{value:.{digits}E}"
        if float(text) == value:
            best = text
            break
    return " " + best.ljust(inner)


def format_integer(value: int, width: int = 6) -> str:
    """Render an integer as a whitespace-safe field."""
    return " " + str(value).ljust(width - 1)


def _write_endmember(
    endmember: Endmember, *, num_elements: int, num_gibbs: int, out: List[str]
) -> None:
    if len(endmember.stoichiometry) != num_elements:
        raise WriteError(
            f"endmember {endmember.species!r} has "
            f"{len(endmember.stoichiometry)} stoichiometry values but the "
            f"database declares {num_elements} elements"
        )

    header = " " + endmember.species
    if endmember.is_dummy:
        header = header.ljust(24)
        if not header.endswith(" "):
            header += " "
        header += "#"
    out.append(header)

    out.append(
        format_integer(endmember.option, 4)
        + format_integer(len(endmember.intervals), 4)
        + "".join(format_number(v, 13) for v in endmember.stoichiometry)
    )

    reduced = endmember.reduced_option
    has_additional = reduced in OPTIONS_WITH_ADDITIONAL
    has_ptvm = reduced in OPTIONS_WITH_PTVM

    for interval in endmember.intervals:
        if len(interval.coefficients) != num_gibbs:
            raise WriteError(
                f"interval of endmember {endmember.species!r} has "
                f"{len(interval.coefficients)} coefficients but the database "
                f"declares {num_gibbs} Gibbs coefficient indices"
            )
        out.append(
            format_number(interval.t_max)
            + "".join(format_number(c) for c in interval.coefficients)
        )
        if has_additional:
            line = format_integer(len(interval.additional_terms), 4)
            for term in interval.additional_terms:
                line += format_number(term.coefficient) + format_number(
                    term.exponent, 13
                )
            out.append(line)
        if has_ptvm:
            out.append("".join(format_number(t) for t in interval.ptvm_terms))

    if endmember.curie_temperature is not None:
        out.append(
            format_number(endmember.curie_temperature)
            + format_number(endmember.magnetic_moment or 0.0)
        )


def _write_cef_phase(
    phase: PhaseLayout, *, num_gibbs: int, num_excess: int, out: List[str]
) -> None:
    base = phase.model[:4]
    out.append(" " + phase.name)
    out.append(" " + phase.model)

    if phase.model in MAGNETIC_MODELS:
        out.append(
            format_number(phase.magnetic_afm_factor or 0.0, 13)
            + format_number(phase.magnetic_structure_factor or 0.0, 13)
        )

    for endmember in phase.endmembers:
        _write_endmember(
            endmember, num_elements=len(endmember.stoichiometry), num_gibbs=num_gibbs, out=out
        )
        if base == "QKTO":
            out.append(
                format_number(endmember.zeta or 0.0, 13)
                + format_integer(endmember.chemical_group or 0, 8)
            )

    if base == "SUBL":
        ratios = phase.sublattice_ratios
        out.append(
            format_integer(len(ratios), 4)
            + "".join(format_number(r, 13) for r in ratios)
        )
        out.append(
            "".join(
                format_integer(len(c), 4) for c in phase.sublattice_constituents
            )
        )
        for constituents in phase.sublattice_constituents:
            out.append("".join(" " + c.ljust(23) for c in constituents))
        for column in zip(*phase.endmember_constituent_indices, strict=False):
            out.append("".join(format_integer(v, 6) for v in column))

    if base == "IDMX":
        # IDMX phases have no excess block and no terminator.
        return

    if base == "QKTO":
        if phase.chemical_group_overrides:
            out.append(format_integer(-len(phase.chemical_group_overrides), 4))
            out.extend(phase.chemical_group_overrides)
        for term in phase.excess_terms:
            out.append(
                format_integer(term.num_interacting, 4)
                + "".join(format_integer(i, 6) for i in term.species_indices)
                + "".join(format_integer(e, 6) for e in term.exponents)
                + "".join(format_number(c) for c in term.coefficients)
            )
        out.append(format_integer(0, 4))
        return

    # RKMP / SUBL
    if phase.model in MAGNETIC_MODELS:
        # The magnetic block is written even when empty: its terminating zero is
        # what tells the reader the block has ended.
        for group, items in _group_magnetic(phase.magnetic_excess_terms):
            out.append(
                format_integer(len(group), 4)
                + "".join(format_integer(i, 6) for i in group)
                + format_integer(len(items), 4)
            )
            for item in items:
                out.append(
                    format_number(item.curie_temperature)
                    + format_number(item.magnetic_moment)
                )
        out.append(format_integer(0, 4))

    for excess_group, excess_items in _group_excess(phase.excess_terms):
        out.append(
            format_integer(len(excess_group), 4)
            + "".join(format_integer(i, 6) for i in excess_group)
            + format_integer(len(excess_items), 4)
        )
        for excess_term in excess_items:
            out.append(
                "".join(format_number(c) for c in excess_term.coefficients)
            )
    out.append(format_integer(0, 4))


def _group_excess(
    terms: Tuple[ExcessTerm, ...],
) -> List[Tuple[Tuple[int, ...], List[ExcessTerm]]]:
    """Group consecutive excess terms that share an interacting-species set."""
    groups: List[Tuple[Tuple[int, ...], List[ExcessTerm]]] = []
    for term in terms:
        key = term.species_indices
        if groups and groups[-1][0] == key:
            groups[-1][1].append(term)
        else:
            groups.append((key, [term]))
    return groups


def _group_magnetic(
    terms: Tuple[MagneticExcessTerm, ...],
) -> List[Tuple[Tuple[int, ...], List[MagneticExcessTerm]]]:
    groups: List[Tuple[Tuple[int, ...], List[MagneticExcessTerm]]] = []
    for term in terms:
        key = term.species_indices
        if groups and groups[-1][0] == key:
            groups[-1][1].append(term)
        else:
            groups.append((key, [term]))
    return groups


def _write_mqmqa_phase(
    phase: PhaseLayout, *, num_gibbs: int, num_excess: int, out: List[str]
) -> None:
    out.append(" " + phase.name)
    out.append(" " + phase.model)
    if phase.model == "SUBG":
        out.append(format_number(phase.zeta or 0.0, 13))
    out.append(format_integer(phase.num_pairs, 4) + format_integer(phase.num_quadruplets, 4))

    for endmember in phase.endmembers:
        _write_endmember(
            endmember, num_elements=len(endmember.stoichiometry), num_gibbs=num_gibbs, out=out
        )
        out.append(
            "".join(format_number(v, 13) for v in endmember.quadruplet_stoichiometry)
        )
        if phase.model == "SUBQ":
            out.append(format_number(endmember.zeta or 0.0, 13))

    first = _mqmqa_sublattice(phase, 0)
    second = _mqmqa_sublattice(phase, 1)
    out.append(format_integer(len(first[0]), 4) + format_integer(len(second[0]), 4))
    out.append("".join(" " + c.ljust(23) for c in first[0]))
    out.append("".join(" " + c.ljust(23) for c in second[0]))
    out.append("".join(format_number(c, 13) for c in first[1]))
    out.append("".join(format_integer(g, 6) for g in first[2]))
    out.append("".join(format_number(c, 13) for c in second[1]))
    out.append("".join(format_integer(g, 6) for g in second[2]))

    num_first = len(first[0])
    num_second = len(second[0])
    pair_count = num_first * num_second
    flat_indices = _mqmqa_pair_indices(phase, num_first, num_second, pair_count)
    out.append("".join(format_integer(i, 6) for i in flat_indices[0]))
    out.append("".join(format_integer(i, 6) for i in flat_indices[1]))

    for quadruplet in phase.quadruplets:
        out.append("".join(format_integer(i, 6) for i in quadruplet.indices))
        out.append("".join(format_number(c, 13) for c in quadruplet.coordinations))

    for term in phase.subq_excess_terms:
        out.append(format_integer(term.mixing_type, 4) + " " + term.mixing_code)
        out.append("".join(format_integer(v, 6) for v in term.mixing_constants))
        out.append("".join(format_integer(v, 6) for v in term.mixing_exponents))
        out.append("".join(format_number(v, 13) for v in term.metadata))
        out.append(
            format_integer(term.additional_cation, 6)
            + format_integer(term.additional_anion, 6)
        )
        out.append("".join(format_number(c) for c in term.coefficients))

    if phase.chemical_group_overrides:
        out.append(format_integer(-len(phase.chemical_group_overrides), 4))
        out.extend(phase.chemical_group_overrides)
    else:
        out.append(format_integer(0, 4))


def _mqmqa_sublattice(
    phase: PhaseLayout, index: int
) -> Tuple[Tuple[str, ...], Tuple[float, ...], Tuple[int, ...]]:
    """The constituents, charges and chemical groups of one MQMQA sublattice."""
    if len(phase.mqmqa_sublattices) != 2:
        raise WriteError(
            f"MQMQA phase {phase.name!r} is missing its sublattice data; "
            "it cannot be written"
        )
    return phase.mqmqa_sublattices[index]


def _mqmqa_pair_indices(
    phase: PhaseLayout, num_first: int, num_second: int, pair_count: int
) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """The two flat index columns that pair sublattice-1 to sublattice-2."""
    if len(phase.mqmqa_pair_indices) != 2:
        raise WriteError(
            f"MQMQA phase {phase.name!r} is missing its pair-index columns; "
            "it cannot be written"
        )
    first, second = phase.mqmqa_pair_indices
    if len(first) != pair_count or len(second) != pair_count:
        raise WriteError(
            f"MQMQA phase {phase.name!r} declares {num_first}x{num_second} = "
            f"{pair_count} pairs but has {len(first)} and {len(second)} indices"
        )
    return first, second


def write(database: Database) -> str:
    """Render a :class:`~calphad_io.model.Database` back to ChemSage DAT text.

    The database must have been read from DAT (so that it carries a layout).
    """
    if database.format != Format.DAT:
        raise WriteError(
            f"cannot write a {database.format!r} database as DAT; convert it first"
        )
    layout = database.header.get("layout")
    if not isinstance(layout, DatLayout):
        raise WriteError(
            "this database has no DAT layout and cannot be written as DAT; "
            "it was constructed in memory or converted from another format"
        )
    return write_layout(layout)


def write_layout(layout: DatLayout) -> str:
    """Render a :class:`~calphad_io.dat.layout.DatLayout` as DAT text."""
    header = layout.header
    out: List[str] = []
    out.append(header.title or " System " + "-".join(header.elements))
    out.append(
        format_integer(header.num_elements, 5)
        + format_integer(header.num_solution_phases, 5)
        + "".join(
            format_integer(c, 5) for c in header.solution_phase_species_counts
        )
        + format_integer(header.num_stoichiometric_phases, 5)
    )
    for start in range(0, len(header.elements), _ELEMENTS_PER_LINE):
        name_chunk = header.elements[start : start + _ELEMENTS_PER_LINE]
        out.append("".join(" " + e.ljust(23) for e in name_chunk))
    for start in range(0, len(header.masses), _ELEMENTS_PER_LINE):
        mass_chunk = header.masses[start : start + _ELEMENTS_PER_LINE]
        out.append("".join(format_number(m, 24) for m in mass_chunk))
    out.append(
        format_integer(len(header.gibbs_coefficient_indices), 4)
        + "".join(format_integer(i, 4) for i in header.gibbs_coefficient_indices)
    )
    out.append(
        format_integer(len(header.excess_coefficient_indices), 4)
        + "".join(format_integer(i, 4) for i in header.excess_coefficient_indices)
    )

    num_gibbs = len(header.gibbs_coefficient_indices)
    num_excess = len(header.excess_coefficient_indices)

    for phase in layout.phases:
        if phase.kind == "stoichiometric":
            endmember = phase.endmembers[0]
            _write_endmember(
                endmember,
                num_elements=len(endmember.stoichiometry),
                num_gibbs=num_gibbs,
                out=out,
            )
            if phase.magnetic_afm_factor is not None:
                out.append(
                    format_number(phase.magnetic_afm_factor, 13)
                    + format_number(phase.magnetic_structure_factor or 0.0, 13)
                )
        elif phase.kind == "cef":
            _write_cef_phase(
                phase, num_gibbs=num_gibbs, num_excess=num_excess, out=out
            )
        elif phase.kind == "mqmqa":
            _write_mqmqa_phase(
                phase, num_gibbs=num_gibbs, num_excess=num_excess, out=out
            )
        else:
            raise WriteError(f"cannot write phase kind {phase.kind!r}")

    text = "\n".join(out) + "\n"
    if layout.trailing:
        text += layout.trailing + "\n"
    return text
