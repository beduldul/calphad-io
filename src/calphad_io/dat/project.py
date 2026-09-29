"""Projection of a DAT layout onto the format-neutral object model.

DAT does not have TDB-style ``PARAMETER`` commands.  It expresses energy as
per-endmember temperature intervals plus excess terms, and it carries model
information (thermodynamic data options, P-T molar-volume terms, QKTO chemical
groups, MQMQA quadruplet coordinations) that the generic :class:`Database` has
nowhere to put.

So the generic view is a *projection*, not the source of truth:

* the authoritative record is the
  :class:`~calphad_io.dat.layout.DatLayout` kept under ``header["layout"]``, and
  that is what the writer renders from;
* the objects produced here exist so that ``validate()``, the CLI and callers
  who just want to list phases have something uniform to work with.

The synthesised ``Parameter`` objects record the file's own numbers as text.
They are not evaluated, and they are not round-tripped.
"""

from __future__ import annotations

from typing import List

from ..model import Parameter, Phase, Sublattice
from .layout import DatLayout, Interval, PhaseLayout  # noqa: F401  (Interval used in annotations)

__all__ = ["parameters_from_layout", "phase_from_layout"]


# ---------------------------------------------------------------------------
# projection onto the generic model
# ---------------------------------------------------------------------------


def phase_from_layout(layout: PhaseLayout, num_elements: int) -> Phase:
    """Project a rich layout phase onto the format-neutral :class:`Phase`.

    A stoichiometric compound is represented as a single sublattice holding its
    species name; its element composition lives on the endmember, because the
    generic model has no separate place for it.
    """
    if layout.kind == "stoichiometric":
        endmember = layout.endmembers[0]
        return Phase(
            name=layout.name,
            sublattices=(
                Sublattice(constituents=(endmember.species,), ratio=1.0),
            ),
            model=layout.model,
            kind=layout.kind,
            is_dummy=endmember.is_dummy,
            is_stoichiometric=True,
        )

    if layout.kind == "mqmqa":
        if layout.mqmqa_sublattices:
            sublattices = tuple(
                Sublattice(constituents=constituents, ratio=1.0)
                for constituents, _charges, _groups in layout.mqmqa_sublattices
            )
        else:
            # No sublattice table in the file: fall back to one sublattice
            # listing every endmember species, so the model is still readable.
            sublattices = (
                Sublattice(
                    constituents=tuple(e.species for e in layout.endmembers),
                    ratio=1.0,
                ),
            )
        return Phase(
            name=layout.name,
            sublattices=sublattices,
            model=layout.model,
            kind=layout.kind,
            is_stoichiometric=False,
        )

    if layout.sublattice_constituents:
        sublattices = tuple(
            Sublattice(constituents=constituents, ratio=ratio)
            for constituents, ratio in zip(
                layout.sublattice_constituents,
                layout.sublattice_ratios or (1.0,),
                strict=False,
            )
        )
    else:
        # Only SUBL writes an explicit constituent table.  For IDMX, RKMP and
        # QKTO the constituents are implicit in the endmembers, so use those --
        # reporting an empty sublattice here would be a false positive.
        sublattices = (
            Sublattice(
                constituents=tuple(e.species for e in layout.endmembers),
                ratio=(layout.sublattice_ratios or (1.0,))[0],
            ),
        )
    return Phase(
        name=layout.name,
        sublattices=sublattices,
        model=layout.model,
        kind=layout.kind,
        is_stoichiometric=False,
    )


def parameters_from_layout(layout: PhaseLayout) -> List[Parameter]:
    """Project the thermodynamic content of a phase onto generic parameters.

    The generic model has no room for temperature intervals, so each endmember
    contributes one parameter per interval, distinguished by ``order`` (the
    interval index).  The expressions are *recorded*, not evaluated.

    These parameters are a **projection for inspection**, not the file's own
    structure: DAT expresses energy as per-endmember intervals and excess terms,
    not as TDB-style ``PARAMETER`` commands.  The authoritative record is the
    :class:`~calphad_io.dat.layout.DatLayout`, which the writer uses.
    """
    parameters: List[Parameter] = []

    def render_interval(interval: Interval) -> str:
        parts = [f"T<={interval.t_max:g}"]
        parts.extend(f"{c:g}" for c in interval.coefficients)
        for term in interval.additional_terms:
            parts.append(f"{term.coefficient:g}*T**{term.exponent:g}")
        return " ".join(parts)

    if layout.kind == "stoichiometric":
        endmember = layout.endmembers[0]
        for index, interval in enumerate(endmember.intervals):
            parameters.append(
                Parameter(
                    kind="G",
                    phase=layout.name,
                    constituents=((endmember.species,),),
                    order=index,
                    expression=render_interval(interval),
                )
            )
        return parameters

    for endmember in layout.endmembers:
        for index, interval in enumerate(endmember.intervals):
            parameters.append(
                Parameter(
                    kind="G",
                    phase=layout.name,
                    constituents=((endmember.species,),),
                    order=index,
                    expression=render_interval(interval),
                )
            )

    for term in layout.excess_terms:
        parameters.append(
            Parameter(
                kind="L",
                phase=layout.name,
                constituents=(tuple(str(i) for i in term.species_indices),),
                order=term.order,
                expression=" ".join(f"{c:g}" for c in term.coefficients),
            )
        )
    for magnetic_term in layout.magnetic_excess_terms:
        parameters.append(
            Parameter(
                kind="TC",
                phase=layout.name,
                constituents=(tuple(str(i) for i in magnetic_term.species_indices),),
                order=magnetic_term.order,
                expression=f"{magnetic_term.curie_temperature:g}",
            )
        )
    return parameters
