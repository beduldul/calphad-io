"""ChemSage ``.DAT`` reader.

Turns DAT text into a :class:`~calphad_io.dat.layout.DatLayout` (the complete,
re-renderable record) and projects that onto the format-neutral
:class:`~calphad_io.model.Database`.

The layout is the authoritative record.  The ``Database`` a caller normally
sees carries the layout under ``header["layout"]``, and the generic
``Parameter`` list it also exposes is a *projection for inspection* -- DAT has
no TDB-style parameter commands, so those objects are synthesised from
endmember intervals and excess terms.

The layout summary, and the amount of data each block consumes, is documented
in the package docstring.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Tuple

from ..errors import ParseError, UnsupportedFeatureError
from ..model import Database, Element, Format, Parameter
from .layout import (
    CEF_MODELS,
    MAGNETIC_MODELS,
    MQMQA_MODELS,
    OPTIONS_HEAT_CAPACITY,
    OPTIONS_WITH_ADDITIONAL,
    OPTIONS_WITH_CONSTANT_VM,
    OPTIONS_WITH_PTVM,
    AdditionalTerm,
    DatHeader,
    DatLayout,
    Endmember,
    ExcessTerm,
    Interval,
    MagneticExcessTerm,
    PhaseLayout,
    Quadruplet,
    SubqExcessTerm,
)
from .project import parameters_from_layout, phase_from_layout
from .tokens import Stream, tokenize

__all__ = ["read", "read_layout"]

#: P-T molar-volume records always carry eleven terms in observed files.
_PTVM_TERM_COUNT = 11

#: Number of metadata values on an MQMQA excess term.
_SUBQ_METADATA_COUNT = 12

#: Chemical-group override strings are always ten tokens wide.
_OVERRIDE_TOKEN_COUNT = 10


# ---------------------------------------------------------------------------
# header
# ---------------------------------------------------------------------------


def _read_header(stream: Stream) -> DatHeader:
    num_elements = stream.integer()
    num_solution_phases = stream.integer()
    if num_solution_phases < 0 or num_elements < 0:
        raise ParseError(
            "negative element or solution-phase count in header",
            line=stream.line(),
            source=stream.source,
        )
    species_counts = tuple(stream.integers(num_solution_phases))
    num_stoich = stream.integer()
    stream.skip_line()  # the remainder of the counts line is free-form

    elements = tuple(stream.strings(num_elements))
    masses = tuple(stream.numbers(num_elements))

    num_gibbs = stream.integer()
    gibbs_indices = tuple(stream.integers(num_gibbs))
    num_excess = stream.integer()
    excess_indices = tuple(stream.integers(num_excess))

    return DatHeader(
        num_elements=num_elements,
        num_solution_phases=num_solution_phases,
        solution_phase_species_counts=species_counts,
        num_stoichiometric_phases=num_stoich,
        elements=elements,
        masses=masses,
        gibbs_coefficient_indices=gibbs_indices,
        excess_coefficient_indices=excess_indices,
    )


# ---------------------------------------------------------------------------
# endmembers
# ---------------------------------------------------------------------------


def _read_endmember(
    stream: Stream,
    *,
    num_elements: int,
    num_gibbs: int,
    allow_dummy: bool = False,
) -> Endmember:
    species = stream.string()
    is_dummy = False
    if stream.peek_text() == "#":
        if not allow_dummy:
            raise ParseError(
                f"unexpected dummy marker after species {species!r}",
                line=stream.line(),
                source=stream.source,
            )
        stream.next()
        is_dummy = True

    try:
        option = stream.integer()
    except ParseError:
        # Some endmembers carry two extra floats before the option.  They are
        # always zero in every file we have seen; a non-zero value would mean
        # we do not understand the record, so refuse rather than guess.
        first = stream.number()
        second = stream.number()
        if first != 0.0 or second != 0.0:
            raise UnsupportedFeatureError(
                "non-zero values after species name",
                f"species {species!r} has {first} and {second} before the "
                "thermodynamic data option",
                line=stream.line(),
            ) from None
        option = stream.integer()

    if option < 1 or option > 24:
        raise UnsupportedFeatureError(
            "thermodynamic data option out of range",
            f"species {species!r} declares option {option}",
            line=stream.line(),
        )

    num_intervals = stream.integer()
    if num_intervals < 0:
        raise ParseError(
            f"negative interval count for species {species!r}",
            line=stream.line(),
            source=stream.source,
        )
    stoichiometry = tuple(stream.numbers(num_elements))

    reduced = option - 12 if option > 12 else option
    if reduced in OPTIONS_WITH_CONSTANT_VM:
        raise UnsupportedFeatureError(
            "constant molar volume thermodynamic data option",
            f"species {species!r} uses option {option}",
            line=stream.line(),
        )
    if reduced in OPTIONS_HEAT_CAPACITY:
        raise UnsupportedFeatureError(
            "heat-capacity thermodynamic data option",
            f"species {species!r} uses option {option}; the DAT file expresses "
            "this endmember as heat-capacity coefficients rather than a Gibbs "
            "energy polynomial",
            line=stream.line(),
        )

    has_additional = reduced in OPTIONS_WITH_ADDITIONAL
    has_ptvm = reduced in OPTIONS_WITH_PTVM

    intervals: List[Interval] = []
    for _ in range(num_intervals):
        t_max = stream.number()
        coefficients = tuple(stream.numbers(num_gibbs))
        additional: Tuple[AdditionalTerm, ...] = ()
        if has_additional:
            count = stream.integer()
            if count < 0:
                raise ParseError(
                    "negative additional-term count",
                    line=stream.line(),
                    source=stream.source,
                )
            additional = tuple(
                AdditionalTerm(stream.number(), stream.number()) for _ in range(count)
            )
        ptvm: Tuple[float, ...] = ()
        if has_ptvm:
            ptvm = tuple(stream.numbers(_PTVM_TERM_COUNT))
        intervals.append(
            Interval(
                t_max=t_max,
                coefficients=coefficients,
                additional_terms=additional,
                ptvm_terms=ptvm,
            )
        )

    curie: float | None = None
    moment: float | None = None
    if option > 12:
        curie = stream.number()
        moment = stream.number()

    return Endmember(
        species=species,
        option=option,
        stoichiometry=stoichiometry,
        intervals=tuple(intervals),
        is_dummy=is_dummy,
        curie_temperature=curie,
        magnetic_moment=moment,
    )


# ---------------------------------------------------------------------------
# excess terms
# ---------------------------------------------------------------------------


def _read_excess_terms(stream: Stream, num_excess: int) -> Tuple[ExcessTerm, ...]:
    """Redlich-Kister excess block, terminated by a zero count."""
    terms: List[ExcessTerm] = []
    while True:
        num_interacting = stream.integer()
        if num_interacting == 0:
            return tuple(terms)
        if num_interacting < 0:
            raise ParseError(
                "unexpected negative interacting-species count in excess block",
                line=stream.line(),
                source=stream.source,
            )
        indices = tuple(stream.integers(num_interacting))
        num_orders = stream.integer()
        if num_orders < 0:
            raise ParseError(
                "negative parameter-order count",
                line=stream.line(),
                source=stream.source,
            )
        for order in range(num_orders):
            terms.append(
                ExcessTerm(
                    num_interacting=num_interacting,
                    species_indices=indices,
                    order=order,
                    coefficients=tuple(stream.numbers(num_excess)),
                )
            )


def _read_magnetic_excess_terms(
    stream: Stream,
) -> Tuple[MagneticExcessTerm, ...]:
    """Magnetic excess block, terminated by a zero count.

    This block is present for *every* magnetic model, even when empty -- its
    terminating zero is what tells the reader the block has ended.
    """
    terms: List[MagneticExcessTerm] = []
    while True:
        num_interacting = stream.integer()
        if num_interacting == 0:
            return tuple(terms)
        if num_interacting < 0:
            raise ParseError(
                "unexpected negative interacting-species count in magnetic "
                "excess block",
                line=stream.line(),
                source=stream.source,
            )
        indices = tuple(stream.integers(num_interacting))
        num_orders = stream.integer()
        if num_orders < 0:
            raise ParseError(
                "negative magnetic parameter-order count",
                line=stream.line(),
                source=stream.source,
            )
        for order in range(num_orders):
            terms.append(
                MagneticExcessTerm(
                    species_indices=indices,
                    order=order,
                    curie_temperature=stream.number(),
                    magnetic_moment=stream.number(),
                )
            )


def _read_qkto_excess(
    stream: Stream, num_excess: int
) -> Tuple[Tuple[ExcessTerm, ...], Tuple[str, ...]]:
    """Kohler-Toop excess block.

    Unlike the Redlich-Kister layout this has **no** separate
    "number of parameter orders" field: the count of orders is implied by the
    number of coefficient rows that follow.  A negative count introduces the
    chemical-group override block, which also ends the excess block.
    """
    terms: List[ExcessTerm] = []
    overrides: List[str] = []
    while True:
        num_interacting = stream.integer()
        if num_interacting == 0:
            return tuple(terms), tuple(overrides)
        if num_interacting < 0:
            for _ in range(-num_interacting):
                overrides.append(
                    " ".join(stream.strings(_OVERRIDE_TOKEN_COUNT))
                )
            return tuple(terms), tuple(overrides)
        indices = tuple(stream.integers(num_interacting))
        exponents = tuple(stream.integers(num_interacting))
        terms.append(
            ExcessTerm(
                num_interacting=num_interacting,
                species_indices=indices,
                order=0,
                coefficients=tuple(stream.numbers(num_excess)),
                exponents=exponents,
            )
        )


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------


def _read_cef_phase(
    stream: Stream,
    *,
    name: str,
    model: str,
    num_elements: int,
    num_gibbs: int,
    num_excess: int,
    num_species: int,
) -> PhaseLayout:
    base = model[:4]
    magnetic = model in MAGNETIC_MODELS

    afm: float | None = None
    structure: float | None = None
    if magnetic:
        afm = stream.number()
        structure = stream.number()

    endmembers: List[Endmember] = []
    for _ in range(num_species):
        endmember = _read_endmember(
            stream, num_elements=num_elements, num_gibbs=num_gibbs
        )
        if base == "QKTO":
            endmember = replace(
                endmember,
                zeta=stream.number(),
                chemical_group=stream.integer(),
            )
        endmembers.append(endmember)

    ratios: Tuple[float, ...] = ()
    constituents: Tuple[Tuple[str, ...], ...] = ()
    index_columns: Tuple[Tuple[int, ...], ...] = ()
    if base == "SUBL":
        num_sublattices = stream.integer()
        if num_sublattices <= 0:
            raise ParseError(
                f"phase {name!r} declares {num_sublattices} sublattices",
                line=stream.line(),
                source=stream.source,
            )
        fractions = stream.numbers(num_sublattices)
        # Some phases append an atom count to the name, e.g. "SIGMA:30"; the
        # ratios are then that count times the declared fractions.
        atoms = 1.0
        if ":" in name:
            try:
                atoms = float(name.split(":", 1)[1])
            except ValueError as exc:
                raise ParseError(
                    f"phase {name!r} has a non-numeric atom count after ':'",
                    line=stream.line(),
                    source=stream.source,
                ) from exc
        ratios = tuple(atoms * f for f in fractions)

        counts = tuple(stream.integers(num_sublattices))
        if any(c < 0 for c in counts):
            raise ParseError(
                f"negative constituent count in phase {name!r}",
                line=stream.line(),
                source=stream.source,
            )
        constituents = tuple(
            tuple(stream.strings(c)) for c in counts
        )
        num_endmembers = 1
        for c in counts:
            num_endmembers *= c
        columns = tuple(
            tuple(stream.integers(num_endmembers)) for _ in range(num_sublattices)
        )
        index_columns = tuple(zip(*columns, strict=False))
    elif base in ("IDMX", "RKMP", "QKTO"):
        ratios = (1.0,)
    else:
        raise UnsupportedFeatureError(
            "phase model",
            f"{model!r} on phase {name!r}",
            line=stream.line(),
        )

    excess: Tuple[ExcessTerm, ...] = ()
    magnetic_excess: Tuple[MagneticExcessTerm, ...] = ()
    overrides: Tuple[str, ...] = ()
    if base == "IDMX":
        # IDMX phases carry no excess parameters and no terminator.
        pass
    elif base in ("RKMP", "SUBL"):
        if magnetic:
            magnetic_excess = _read_magnetic_excess_terms(stream)
        excess = _read_excess_terms(stream, num_excess)
    elif base == "QKTO":
        excess, overrides = _read_qkto_excess(stream, num_excess)

    return PhaseLayout(
        name=name,
        model=model,
        kind="cef",
        endmembers=tuple(endmembers),
        sublattice_ratios=ratios,
        sublattice_constituents=constituents,
        endmember_constituent_indices=index_columns,
        excess_terms=excess,
        magnetic_excess_terms=magnetic_excess,
        magnetic_afm_factor=afm,
        magnetic_structure_factor=structure,
        chemical_group_overrides=overrides,
    )


def _read_mqmqa_phase(
    stream: Stream,
    *,
    name: str,
    model: str,
    num_elements: int,
    num_gibbs: int,
    num_excess: int,
) -> PhaseLayout:
    zeta = stream.number() if model == "SUBG" else None
    num_pairs = stream.integer()
    num_quadruplets = stream.integer()
    if num_pairs < 0 or num_quadruplets < 0:
        raise ParseError(
            f"negative pair/quadruplet count in phase {name!r}",
            line=stream.line(),
            source=stream.source,
        )

    endmembers: List[Endmember] = []
    for _ in range(num_pairs):
        endmember = _read_endmember(
            stream, num_elements=num_elements, num_gibbs=num_gibbs
        )
        quadruplet_stoich = tuple(stream.numbers(5))
        # SUBQ gives every pair its own zeta; SUBG declares one for the phase.
        pair_zeta = stream.number() if model == "SUBQ" else None
        endmembers.append(
            replace(
                endmember,
                quadruplet_stoichiometry=quadruplet_stoich,
                zeta=pair_zeta if pair_zeta is not None else zeta,
            )
        )

    num_first = stream.integer()
    num_second = stream.integer()
    first_constituents = tuple(stream.strings(num_first))
    second_constituents = tuple(stream.strings(num_second))
    first_charges = tuple(stream.numbers(num_first))
    first_groups = tuple(stream.integers(num_first))
    second_charges = tuple(stream.numbers(num_second))
    second_groups = tuple(stream.integers(num_second))
    pair_count = num_first * num_second
    first_indices = tuple(stream.integers(pair_count))
    second_indices = tuple(stream.integers(pair_count))

    quadruplets = tuple(
        Quadruplet(
            indices=tuple(stream.integers(4)),
            coordinations=tuple(stream.numbers(4)),
        )
        for _ in range(num_quadruplets)
    )

    excess_terms: List[SubqExcessTerm] = []
    overrides: List[str] = []
    while True:
        mixing_type = stream.integer()
        if mixing_type == 0:
            break
        if mixing_type < 0:
            for _ in range(-mixing_type):
                overrides.append(" ".join(stream.strings(_OVERRIDE_TOKEN_COUNT)))
            break
        excess_terms.append(
            SubqExcessTerm(
                mixing_type=mixing_type,
                mixing_code=stream.string(),
                mixing_constants=tuple(stream.integers(4)),
                mixing_exponents=tuple(stream.integers(4)),
                metadata=tuple(stream.numbers(_SUBQ_METADATA_COUNT)),
                additional_cation=stream.integer(),
                additional_anion=stream.integer(),
                coefficients=tuple(stream.numbers(num_excess)),
            )
        )

    return PhaseLayout(
        name=name,
        model=model,
        kind="mqmqa",
        endmembers=tuple(endmembers),
        num_pairs=num_pairs,
        num_quadruplets=num_quadruplets,
        zeta=zeta,
        quadruplets=quadruplets,
        subq_excess_terms=tuple(excess_terms),
        mqmqa_sublattices=(
            (first_constituents, first_charges, first_groups),
            (second_constituents, second_charges, second_groups),
        ),
        mqmqa_pair_indices=(first_indices, second_indices),
        chemical_group_overrides=tuple(overrides),
    )


def _read_stoichiometric_phase(
    stream: Stream, *, num_elements: int, num_gibbs: int
) -> PhaseLayout:
    endmember = _read_endmember(
        stream,
        num_elements=num_elements,
        num_gibbs=num_gibbs,
        allow_dummy=True,
    )
    afm: float | None = None
    structure: float | None = None
    if endmember.is_magnetic:
        # Magnetic stoichiometric compounds carry AFM and structure factors
        # after the intervals, just like solution-phase endmembers do.
        afm = stream.number()
        structure = stream.number()
    return PhaseLayout(
        name=endmember.species,
        model="STOICHIOMETRIC",
        kind="stoichiometric",
        endmembers=(endmember,),
        magnetic_afm_factor=afm,
        magnetic_structure_factor=structure,
    )


def read_layout(text: str, *, source: str | None = None) -> DatLayout:
    """Parse DAT source into a :class:`~calphad_io.dat.layout.DatLayout`.

    The layout keeps every record in the file, which is what makes a faithful
    re-render possible.
    """
    lines = text.splitlines()
    title = lines[0] if lines else ""
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    stream = Stream(tokenize(body, start_line=2), source=source)

    header = _read_header(stream)
    num_elements = len(header.elements)
    num_gibbs = len(header.gibbs_coefficient_indices)
    num_excess = len(header.excess_coefficient_indices)

    phases: List[PhaseLayout] = []
    for index, species_count in enumerate(header.solution_phase_species_counts):
        if species_count == 0:
            # A zero here marks an absent gas phase; the block is not written.
            continue
        raw_name = stream.string()
        name = raw_name.split("=", 1)[0].strip() or raw_name
        if stream.peek_text() == "=":
            # Some converters append an annotation, e.g.
            # "GAS   = MIXTURE PHASE =  1"; the rest of the line is not data.
            stream.skip_line()
        model = stream.string().upper()
        if model in MQMQA_MODELS:
            phases.append(
                _read_mqmqa_phase(
                    stream,
                    name=name,
                    model=model,
                    num_elements=num_elements,
                    num_gibbs=num_gibbs,
                    num_excess=num_excess,
                )
            )
        elif model in CEF_MODELS:
            phases.append(
                _read_cef_phase(
                    stream,
                    name=name,
                    model=model,
                    num_elements=num_elements,
                    num_gibbs=num_gibbs,
                    num_excess=num_excess,
                    num_species=species_count,
                )
            )
        else:
            raise UnsupportedFeatureError(
                "phase model",
                f"{model!r} on phase {name!r} (solution phase {index})",
                line=stream.line(),
            )

    for _ in range(header.num_stoichiometric_phases):
        phases.append(
            _read_stoichiometric_phase(
                stream, num_elements=num_elements, num_gibbs=num_gibbs
            )
        )

    trailing = ""
    if not stream.at_end():
        remaining = stream.tokens[stream.position() :]
        trailing = "\n".join(t.text for t in remaining)

    header = replace(header, title=title)
    return DatLayout(header=header, phases=tuple(phases), trailing=trailing)


def read(text: str, *, source: str | None = None) -> Database:
    """Parse ChemSage DAT source into a :class:`~calphad_io.model.Database`.

    The returned database carries the complete
    :class:`~calphad_io.dat.layout.DatLayout` under ``header["layout"]``, so
    :func:`write` can reproduce the file.
    """
    layout = read_layout(text, source=source)
    header = layout.header
    num_elements = len(header.elements)

    elements = tuple(
        Element(name=name.upper(), reference_phase="BLANK", mass=mass)
        for name, mass in zip(header.elements, header.masses, strict=False)
    )
    phases = tuple(
        phase_from_layout(phase, num_elements) for phase in layout.phases
    )
    parameters: List[Parameter] = []
    for phase in layout.phases:
        parameters.extend(parameters_from_layout(phase))

    return Database(
        format=Format.DAT,
        source=source,
        elements=elements,
        species=(),
        phases=phases,
        parameters=tuple(parameters),
        functions=(),
        type_definitions=(),
        header={
            "title": header.title,
            "gibbs_coefficient_indices": header.gibbs_coefficient_indices,
            "excess_coefficient_indices": header.excess_coefficient_indices,
            "layout": layout,
        },
        raw=None,
    )


# ---------------------------------------------------------------------------
# writer
