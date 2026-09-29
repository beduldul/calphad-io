"""Structural validation.

The point of validation is to catch a broken database *before* it reaches a
solver, where the failure mode is usually a crash deep inside a numerical
routine or -- worse -- a silently wrong answer.

Validation never raises.  It returns a list of :class:`Finding` objects, each
carrying a severity, a stable machine-readable code, and a human-readable
message.  This is deliberate: a validator that throws on the first problem is
useless for triaging a file with twenty problems in it.

Severity
--------
``error``
    The database is definitely broken: a phase references something that does
    not exist, a name is duplicated, a delimiter is unbalanced.  A solver will
    either fail or produce meaningless results.
``warning``
    Suspicious but not necessarily wrong.  Real databases in the wild trip
    several of these; for example ``AL%`` is a legitimate two-sublattice
    constituent name that looks like a typo.
``info``
    Observations worth knowing, never a defect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple

from .model import Database, Format, Phase

__all__ = ["ALL_CODES", "Finding", "Severity", "validate"]


class Severity:
    """Severity levels, as plain strings so they serialise directly to JSON."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    ORDER = (ERROR, WARNING, INFO)

    @staticmethod
    def rank(value: str) -> int:
        try:
            return Severity.ORDER.index(value)
        except ValueError:
            return len(Severity.ORDER)


@dataclass(frozen=True)
class Finding:
    """One validation result."""

    code: str
    """Stable machine-readable identifier, e.g. ``"undefined_constituent"``."""

    severity: str
    """One of :class:`Severity`'s values."""

    message: str
    """Human-readable description, including the offending name."""

    location: str = ""
    """Where the problem is, e.g. ``"phase FCC_A1"``."""

    detail: Dict[str, str] = field(default_factory=dict)
    """Extra structured context, e.g. ``{"constituent": "MG%"}``."""

    def to_dict(self) -> Dict[str, object]:
        """A JSON-serialisable representation."""
        out: Dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.location:
            out["location"] = self.location
        if self.detail:
            out["detail"] = dict(self.detail)
        return out

    def __str__(self) -> str:  # pragma: no cover - presentation
        where = f" [{self.location}]" if self.location else ""
        return f"{self.severity}: {self.code}: {self.message}{where}"


#: Every finding code this validator can emit.  Useful for tests and docs.
ALL_CODES: Tuple[str, ...] = (
    "duplicate_element",
    "duplicate_phase",
    "duplicate_parameter",
    "duplicate_function",
    "duplicate_type_definition",
    "no_phases",
    "no_elements",
    "undefined_constituent",
    "undefined_type_definition",
    "unused_type_definition",
    "parameter_for_undefined_phase",
    "empty_parameter_expression",
    "unbalanced_parentheses",
    "constituent_count_mismatch",
    "phase_without_constituents",
    "parameter_sublattice_mismatch",
    "suspicious_constituent_name",
    "suspiciously_named_phase",
    "element_mass_missing",
    "phase_has_no_parameters",
)

#: Names that occupy an element slot but are not chemical elements.
PSEUDO_CONSTITUENTS = frozenset({"VA", "ELECTRON_GAS", "/-", "/--", "/"})

#: Characters that cannot appear in a phase name in either format.  Note that
#: parentheses, hyphens, dots, colons and slashes are all legal and common in
#: real phase names ("Fe2O3_High-Pressure-(s2)", "LIQUID:L", "O2"), so they are
#: deliberately not listed here.
_ILLEGAL_NAME_CHARS = re.compile(r"[\s!$\[\]{}<>\"',;]")


def _composed_of_elements(name: str, known_elements: Set[str]) -> bool:
    """True if ``name`` can be read as a formula over the declared elements.

    ``"NaCl"`` with elements ``{NA, CL}`` is a valid species; ``"ZZCl"`` is not,
    because ``ZZ`` is undeclared.  This does proper formula tokenisation: at each
    position it matches the *longest* declared element symbol, then skips any
    digits.  Splitting on letter runs would be wrong, because ``"OH"`` is
    ``O`` + ``H``, not an element called ``OH``.

    Trailing charge markers (``+``, ``-``, ``[3+]``) and the ``%`` sublattice
    markers used by two-sublattice constituent names are ignored.
    """
    if not name:
        return False
    stripped = re.sub(r"\[[^\]]*\]", "", name)
    stripped = re.sub(r"[+\-]+$", "", stripped)
    stripped = re.sub(r"%+$", "", stripped)
    stripped = stripped.replace("%", "")
    if not stripped:
        return False

    symbols = sorted(known_elements, key=len, reverse=True)
    if not symbols:
        return False
    upper = stripped.upper()
    index = 0
    matched_any = False
    while index < len(upper):
        ch = upper[index]
        if ch.isdigit() or ch == ".":
            index += 1
            continue
        for symbol in symbols:
            if upper.startswith(symbol, index):
                index += len(symbol)
                matched_any = True
                break
        else:
            return False
    return matched_any


#: Parameter kinds whose constituent array must name every sublattice of the
#: phase.  Magnetic (``TC``, ``BM``), diffusion (``DQ``, ``DRT``) and MQMQA
#: kinds use their own conventions and are exempt.
_LEADING_KINDS = frozenset({"G", "L", "G2", "V0", "VA", "VB", "VC", "THETA"})


def _is_leading_kind(kind: str) -> bool:
    return kind.upper() in _LEADING_KINDS


def _canonical(name: str) -> str:
    return name.strip().upper()


def _constituent_key(name: str) -> str:
    """Normalise a constituent name for comparison.

    In TDB a trailing ``%`` marks a substitutional constituent, so ``AL%`` and
    ``AL`` are the same species; ``COST507.tdb`` mixes the two spellings freely.
    Without this, 954 parameters in that one file look like they reference
    constituents their phase does not allow.
    """
    return _canonical(name).rstrip("%")


def _phase_key(name: str) -> str:
    """Normalise a phase name as it appears inside a parameter.

    TDB lets a parameter name a *composition set* by joining phase names with
    ``&``, e.g. ``MQ(HCP_A3&AG,*:VA)`` -- the parameter belongs to ``HCP_A3``.
    """
    return _canonical(name).split("&", 1)[0]


def _split_constituent(name: str) -> Tuple[str, str]:
    """Split a constituent into (element-or-species, remainder).

    ``"FE%2"`` -> ``("FE", "%2")``; ``"MG%"`` -> ``("MG", "%")``;
    ``"VA"`` -> ``("VA", "")``.
    """
    match = re.match(r"^([A-Za-z_/][A-Za-z_]*)(.*)$", name)
    if not match:
        return name, ""
    return match.group(1), match.group(2)


def _validate_structure(database: Database) -> List[Finding]:
    findings: List[Finding] = []

    # -- elements -------------------------------------------------------------
    seen_elements: Set[str] = set()
    for element in database.elements:
        key = _canonical(element.name)
        if key in seen_elements:
            findings.append(
                Finding(
                    "duplicate_element",
                    Severity.ERROR,
                    f"element {element.name!r} is declared more than once",
                    location=f"element {element.name}",
                    detail={"element": element.name},
                )
            )
        seen_elements.add(key)
        if element.mass == 0.0 and not element.is_pseudo:
            findings.append(
                Finding(
                    "element_mass_missing",
                    Severity.WARNING,
                    f"element {element.name!r} has a mass of zero",
                    location=f"element {element.name}",
                    detail={"element": element.name},
                )
            )

    if not database.elements:
        findings.append(
            Finding(
                "no_elements",
                Severity.ERROR,
                "the database declares no elements",
            )
        )

    # -- duplicate declarations recorded by the reader ------------------------
    # A dict-backed reader dedupes silently, so the reader counts duplicates as
    # it goes and hands them over here.  Only TDB populates this.
    raw_duplicates = database.header.get("duplicate_declarations")
    duplicates: Dict[str, object] = (
        dict(raw_duplicates) if isinstance(raw_duplicates, dict) else {}
    )
    code_for_prefix = {
        "element": "duplicate_element",
        "phase": "duplicate_phase",
        "function": "duplicate_function",
        "type_definition": "duplicate_type_definition",
    }
    for key, count in sorted(duplicates.items()):
        prefix, _, name = str(key).partition(":")
        code = code_for_prefix.get(prefix)
        if code is None:
            continue
        if code == "duplicate_phase" and database.format == Format.DAT:
            # DAT allows repeated phase blocks with different models; the
            # block-aware check below handles that case, so skip the generic one.
            continue
        findings.append(
            Finding(
                code,
                Severity.ERROR,
                f"{prefix.replace('_', ' ')} {name!r} is declared {count} times",
                location=f"{prefix.replace('_', ' ')} {name}",
                detail={prefix: name, "count": str(count)},
            )
        )

    # -- phases ---------------------------------------------------------------
    seen_phases: Dict[str, List[Phase]] = {}
    for phase in database.phases:
        seen_phases.setdefault(_canonical(phase.name), []).append(phase)
    for key, phases in seen_phases.items():
        if len(phases) == 1:
            continue
        if database.format == Format.DAT:
            # A DAT file may contain several phase blocks with the same name --
            # Kaye_Pd-Ru-Tc-Mo.dat declares BCCN twice, and PdRuTcMo.dat declares
            # FCCN ten times -- each with different parameters.  That is a
            # legitimate way to build a database, so only report it when the
            # blocks are literally identical, which is almost certainly a
            # copy-paste error.
            distinct = {
                (
                    tuple(s.constituents for s in p.sublattices),
                    p.model,
                    p.kind,
                )
                for p in phases
            }
            if len(distinct) > 1:
                findings.append(
                    Finding(
                        "duplicate_phase",
                        Severity.INFO,
                        f"phase {key!r} appears in {len(phases)} separate DAT "
                        "blocks with different models; a solver may load only "
                        "the first",
                        location=f"phase {key}",
                        detail={"phase": key, "count": str(len(phases))},
                    )
                )
            else:
                findings.append(
                    Finding(
                        "duplicate_phase",
                        Severity.WARNING,
                        f"phase {key!r} is declared {len(phases)} times with "
                        "identical models; a solver may load only the first",
                        location=f"phase {key}",
                        detail={"phase": key, "count": str(len(phases))},
                    )
                )
            continue
        findings.append(
            Finding(
                "duplicate_phase",
                Severity.ERROR,
                f"phase {key!r} is declared {len(phases)} times",
                location=f"phase {key}",
                detail={"phase": key, "count": str(len(phases))},
            )
        )

    if not database.phases:
        findings.append(
            Finding(
                "no_phases",
                Severity.ERROR,
                "the database declares no phases",
            )
        )

    # -- constituents ---------------------------------------------------------
    known_elements = {_canonical(e.name) for e in database.elements}
    known_species = {_canonical(s.name) for s in database.species}
    # In DAT, a phase's sublattice lists species names, and those names are
    # built out of element symbols ("Fe2O3", "NaCl", "O").  Resolving them
    # against declared species would produce a false positive for every real
    # file, so for DAT the check is whether the name is *composed* of declared
    # elements rather than whether it is literally one of them.
    names_are_species = database.format == Format.DAT
    phases_with_parameters = {
        _phase_key(p.phase) for p in database.parameters
    }
    for phase in database.phases:
        phase_has_parameters = _phase_key(phase.name) in phases_with_parameters
        if not phase.sublattices:
            # A phase with no sublattice model at all: this happens when a
            # CONSTITUENT line never appeared, or when the format carries the
            # model elsewhere (DAT stoichiometric compounds).  It is not a
            # defect in the file, so it is not reported here.
            continue
        if phase.is_stoichiometric and names_are_species:
            # In DAT a stoichiometric compound is written as a single endmember
            # whose species name *is* the phase name, e.g. "Li_solid(s)".  That
            # name is not a formula and must not be read as one.
            continue
        for index, sublattice in enumerate(phase.sublattices):
            if not sublattice.constituents:
                if phase.kind == "cef":
                    if phase_has_parameters:
                        # A TDB phase whose CONSTITUENT line was never written
                        # still has a usable model: the parameters name their
                        # constituents.  Worth surfacing, but not an error.
                        findings.append(
                            Finding(
                                "phase_without_constituents",
                                Severity.WARNING,
                                f"phase {phase.name!r} has no CONSTITUENT line; "
                                "its sublattice model can only be inferred from "
                                "its parameters",
                                location=f"phase {phase.name}",
                                detail={"phase": phase.name, "sublattice": str(index)},
                            )
                        )
                    else:
                        findings.append(
                            Finding(
                                "phase_without_constituents",
                                Severity.ERROR,
                                f"phase {phase.name!r} sublattice {index} is empty "
                                "and the phase has no parameters, so its model is "
                                "unrecoverable",
                                location=f"phase {phase.name}",
                                detail={"phase": phase.name, "sublattice": str(index)},
                            )
                        )
                # Other kinds are not expected to carry a constituent table at
                # all -- a DAT stoichiometric compound is a single endmember --
                # so they are not reported here.
                continue
            for constituent in sublattice.constituents:
                name = _canonical(constituent)
                if name in PSEUDO_CONSTITUENTS:
                    continue
                if name in known_elements or name in known_species:
                    continue
                if _constituent_key(name) in {
                    _constituent_key(e) for e in known_elements
                }:
                    # e.g. "AL%" -- the trailing marker is a sublattice marker,
                    # not a typo.
                    continue
                if names_are_species and _composed_of_elements(name, known_elements):
                    # A species name such as "Fe2O3" built from declared
                    # elements; this is how every real DAT file works.
                    continue
                base, suffix = _split_constituent(name)
                if base in known_elements or base in known_species:
                    # A legitimate DAT constituent such as "MG%" or "FE+2";
                    # common in practice, but worth surfacing because a typo
                    # looks exactly the same.
                    findings.append(
                        Finding(
                            "suspicious_constituent_name",
                            Severity.WARNING,
                            f"phase {phase.name!r} references constituent "
                            f"{constituent!r}, whose base element {base!r} is "
                            f"declared but which carries the suffix {suffix!r}",
                            location=f"phase {phase.name}",
                            detail={
                                "phase": phase.name,
                                "constituent": constituent,
                                "base": base,
                                "suffix": suffix,
                            },
                        )
                    )
                    continue
                findings.append(
                    Finding(
                        "undefined_constituent",
                        Severity.ERROR,
                        f"phase {phase.name!r} references constituent "
                        f"{constituent!r}, which is neither a declared element "
                        "nor a declared species",
                        location=f"phase {phase.name}",
                        detail={"phase": phase.name, "constituent": constituent},
                    )
                )

        if _ILLEGAL_NAME_CHARS.search(phase.name):
            findings.append(
                Finding(
                    "suspiciously_named_phase",
                    Severity.WARNING,
                    f"phase name {phase.name!r} contains characters that are "
                    "not valid in either format",
                    location=f"phase {phase.name}",
                    detail={"phase": phase.name},
                )
            )

    # -- type definitions -----------------------------------------------------
    seen_symbols: Set[str] = set()
    for definition in database.type_definitions:
        if definition.symbol in seen_symbols:
            findings.append(
                Finding(
                    "duplicate_type_definition",
                    Severity.ERROR,
                    f"type-definition symbol {definition.symbol!r} is declared "
                    "more than once",
                    location=f"type definition {definition.symbol}",
                    detail={"symbol": definition.symbol},
                )
            )
        seen_symbols.add(definition.symbol)

    used_symbols: Set[str] = set()
    for phase in database.phases:
        for ch in phase.type_code:
            if not ch.isalnum():
                used_symbols.add(ch)
    # "%" is the implicit default type code. Real databases use it on nearly
    # every phase without ever declaring a TYPE_DEFINITION for it -- see
    # pycalphad's own `al_parameter.tdb` fixture -- so it is not a defect.
    used_symbols.discard("%")
    for symbol in sorted(used_symbols - seen_symbols):
        findings.append(
            Finding(
                "undefined_type_definition",
                Severity.ERROR,
                f"phases use the type-definition symbol {symbol!r}, which is "
                "never declared by a TYPE_DEFINITION command",
                location=f"type definition {symbol}",
                detail={"symbol": symbol},
            )
        )
    for symbol in sorted(seen_symbols - used_symbols):
        findings.append(
            Finding(
                "unused_type_definition",
                Severity.INFO,
                f"type-definition symbol {symbol!r} is declared but no phase "
                "uses it",
                location=f"type definition {symbol}",
                detail={"symbol": symbol},
            )
        )

    return findings


def _validate_parameters(database: Database) -> List[Finding]:
    findings: List[Finding] = []

    phase_names = {_canonical(p.name) for p in database.phases}
    signatures: Dict[str, int] = {}

    for parameter in database.parameters:
        signature = parameter.signature()
        signatures[signature] = signatures.get(signature, 0) + 1
        if _phase_key(parameter.phase) not in phase_names:
            findings.append(
                Finding(
                    "parameter_for_undefined_phase",
                    Severity.ERROR,
                    f"parameter {signature} refers to phase "
                    f"{parameter.phase!r}, which is never declared by a PHASE "
                    "command",
                    location=f"phase {parameter.phase}",
                    detail={"phase": parameter.phase, "parameter": signature},
                )
            )

        if not parameter.expression.strip():
            findings.append(
                Finding(
                    "empty_parameter_expression",
                    Severity.ERROR,
                    f"parameter {signature} has an empty expression",
                    location=f"phase {parameter.phase}",
                    detail={"parameter": signature},
                )
            )
            continue

        if parameter.expression.count("(") != parameter.expression.count(")"):
            findings.append(
                Finding(
                    "unbalanced_parentheses",
                    Severity.ERROR,
                    f"parameter {signature} has unbalanced parentheses in its "
                    f"expression: {parameter.expression!r}",
                    location=f"phase {parameter.phase}",
                    detail={"parameter": signature},
                )
            )

    for signature, count in signatures.items():
        if count > 1:
            if database.format != Format.TDB:
                # In DAT, several phase blocks can legitimately share a name
                # (Kaye_Pd-Ru-Tc-Mo.dat declares BCCN twice, each with its own
                # parameter set).  A repeated signature there reflects separate
                # blocks, not a redefinition within one, so it is not an error.
                continue
            findings.append(
                Finding(
                    "duplicate_parameter",
                    Severity.ERROR,
                    f"parameter {signature} is defined {count} times; a solver "
                    "will use only one of them",
                    location=f"parameter {signature}",
                    detail={"parameter": signature, "count": str(count)},
                )
            )

    # Phases that declare a sublattice model but carry no parameters at all.
    counts: Dict[str, int] = {}
    for parameter in database.parameters:
        counts[_canonical(parameter.phase)] = counts.get(_canonical(parameter.phase), 0) + 1
    for phase in database.phases:
        key = _canonical(phase.name)
        if phase.sublattices and counts.get(key, 0) == 0:
            findings.append(
                Finding(
                    "phase_has_no_parameters",
                    Severity.WARNING,
                    f"phase {phase.name!r} declares a sublattice model but has "
                    "no parameters; it will contribute no energy",
                    location=f"phase {phase.name}",
                    detail={"phase": phase.name},
                )
            )

    return findings


def _validate_parameters_against_phases(database: Database) -> List[Finding]:
    """Check that a parameter's constituent array matches its phase's model.

    Only meaningful for TDB, where ``PARAMETER`` commands name their
    constituents.  The DAT projection synthesises parameters from endmember
    intervals and excess terms, whose "constituents" are species names or
    integer indices, so checking them against the sublattice model would only
    produce noise.
    """
    if database.format != Format.TDB:
        return []

    findings: List[Finding] = []
    for parameter in database.parameters:
        phase = database.phase(_phase_key(parameter.phase))
        if phase is None or not phase.sublattices:
            continue
        # A parameter whose kind is not a leading-sublattice kind is not
        # required to name every sublattice (magnetic and diffusion parameters
        # commonly do not), so the sublattice-count check does not apply.
        if not _is_leading_kind(parameter.kind):
            continue
        if parameter.constituents and len(parameter.constituents) != len(
            phase.sublattices
        ):
            findings.append(
                Finding(
                    "parameter_sublattice_mismatch",
                    Severity.ERROR,
                    f"parameter {parameter.signature()} has "
                    f"{len(parameter.constituents)} constituent groups but phase "
                    f"{phase.name!r} has {len(phase.sublattices)} sublattices",
                    location=f"phase {phase.name}",
                    detail={
                        "phase": phase.name,
                        "parameter": parameter.signature(),
                        "parameter_sublattices": str(len(parameter.constituents)),
                        "phase_sublattices": str(len(phase.sublattices)),
                    },
                )
            )
            continue
        for index, (group, sublattice) in enumerate(
            zip(parameter.constituents, phase.sublattices, strict=False)
        ):
            allowed = {_constituent_key(c) for c in sublattice.constituents}
            if not allowed:
                continue
            for name in group:
                key = _constituent_key(name)
                if key in PSEUDO_CONSTITUENTS:
                    continue
                if key == "*":
                    # An interaction wildcard: Thermo-Calc expands it over every
                    # constituent of the sublattice, so it is always valid.
                    continue
                if key not in allowed:
                    findings.append(
                        Finding(
                            "parameter_sublattice_mismatch",
                            Severity.ERROR,
                            f"parameter {parameter.signature()} uses "
                            f"constituent {name!r} in sublattice {index}, but "
                            f"phase {phase.name!r} does not allow it there",
                            location=f"phase {phase.name}",
                            detail={
                                "phase": phase.name,
                                "parameter": parameter.signature(),
                                "constituent": name,
                                "sublattice": str(index),
                            },
                        )
                    )
    return findings


def validate(database: Database) -> List[Finding]:
    """Check a database for structural problems.

    Returns every finding, sorted by severity then code then location, so that
    output is stable across runs.

    This function never raises for a well-formed :class:`Database`; a
    :class:`~calphad_io.errors.ValidationError` is only raised by the CLI when
    ``--strict`` is requested.
    """
    findings: List[Finding] = []
    findings.extend(_validate_structure(database))
    findings.extend(_validate_parameters(database))
    findings.extend(_validate_parameters_against_phases(database))
    findings.sort(key=lambda f: (Severity.rank(f.severity), f.code, f.location))
    return findings


def counts_by_severity(findings: Sequence[Finding]) -> Dict[str, int]:
    """Tally findings per severity, always including zero counts."""
    out = {Severity.ERROR: 0, Severity.WARNING: 0, Severity.INFO: 0}
    for finding in findings:
        out[finding.severity] = out.get(finding.severity, 0) + 1
    return out
