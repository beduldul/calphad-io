"""Thermo-Calc TDB reader and writer.

The TDB format is a sequence of ``!``-terminated commands.  A command may span
several lines and may contain ``$`` comments, which run to end of line.  The
commands this library understands are ``ELEMENT``, ``SPECIES``, ``FUNCTION``,
``TYPE_DEFINITION``, ``DEFINE_SYSTEM_DEFAULT``, ``DEFAULT_COMMAND``, ``PHASE``,
``CONSTITUENT`` and ``PARAMETER``.

Anything else is retained verbatim rather than discarded, so a database can be
read, inspected and written back without losing commands this library has no
opinion about.

Round-trip fidelity
-------------------
A database that is read and written back **without modification** is emitted
from its original source text and is therefore **byte-for-byte identical**.
This is verified against real databases in the test suite.

If the database was modified or built from scratch, every record is rendered
from the object model in a canonical layout.  That path produces a valid,
loadable file but does not preserve the original whitespace.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Dict, List, Tuple

from ..errors import ParseError, WriteError
from ..model import (
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

__all__ = ["Command", "read", "split_commands", "strip_comment", "write"]

#: Commands this library parses into the object model.
KNOWN_COMMANDS = frozenset(
    {
        "ELEMENT",
        "SPECIES",
        "FUNCTION",
        "TYPE_DEFINITION",
        "DEFINE_SYSTEM_DEFAULT",
        "DEFAULT_COMMAND",
        "PHASE",
        "CONSTITUENT",
        "PARAMETER",
    }
)

#: Commands whose bodies are free-form prose and are always kept verbatim.
PROSE_COMMANDS = frozenset(
    {
        "DATABASE_INFO",
        "ASSESSED_SYSTEM",
        "ASSESSED_SYSTEMS",
        "LIST_OF_REFERENCES",
        "NUMBER_OF_REFERENCES",
        "TABLE",
        "ADDITIONAL_INPUT",
        "TEMPERATURE_LIMITS",
        "VERSION_DATE",
    }
)

_WRAP = 78


class Command:
    """One ``!``-terminated TDB command.

    ``raw`` is the exact source span including the terminating ``!``; ``text``
    is the command with comments stripped and internal whitespace collapsed.
    """

    __slots__ = ("body", "keyword", "line", "parsed", "raw")

    def __init__(
        self,
        keyword: str,
        body: str,
        raw: str,
        line: int,
        parsed: bool = True,
    ) -> None:
        self.keyword = keyword
        self.body = body
        self.raw = raw
        self.line = line
        self.parsed = parsed

    @property
    def text(self) -> str:
        """The command as a single normalised line, without the ``!``."""
        return f"{self.keyword} {self.body}".strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Command({self.keyword!r}, line={self.line})"


# ---------------------------------------------------------------------------
# lexical layer
# ---------------------------------------------------------------------------


def strip_comment(line: str) -> str:
    """Remove a ``$`` comment from **one line**.

    ``$`` starts a comment anywhere outside a double-quoted string.

    This must be applied line by line.  Applied to a whole multi-line block it
    would delete everything after the first ``$``, silently swallowing the
    leading comment header of a real database along with the first command.
    """
    out: List[str] = []
    in_string = False
    for ch in line:
        if ch == '"':
            in_string = not in_string
        if ch == "$" and not in_string:
            break
        out.append(ch)
    return "".join(out)


def _strip_comments_block(text: str) -> str:
    return "\n".join(strip_comment(line) for line in text.splitlines())


def split_commands(text: str) -> List[Command]:
    """Split TDB source into commands.

    A ``!`` inside a ``$`` comment or inside a quoted string does **not**
    terminate a command.  Each command records its exact source span.
    """
    spans: List[Tuple[int, int, int]] = []
    index = 0
    length = len(text)
    start = 0
    line = 1
    start_line = 1
    in_string = False
    in_comment = False

    while index < length:
        ch = text[index]
        if ch == "\n":
            line += 1
            in_comment = False
            index += 1
            continue
        if in_comment:
            index += 1
            continue
        if ch == "$" and not in_string:
            in_comment = True
            index += 1
            continue
        if ch == '"':
            in_string = not in_string
            index += 1
            continue
        if ch == "!" and not in_string:
            spans.append((start, index + 1, start_line))
            index += 1
            start = index
            start_line = line
            continue
        index += 1

    trailing = text[start:]
    commands: List[Command] = []
    for begin, end, line_number in spans:
        raw = text[begin:end]
        head = raw[:-1] if raw.endswith("!") else raw
        joined = " ".join(_strip_comments_block(head).split())
        if joined:
            command = _build_command(joined, line_number)
            command.raw = raw
            commands.append(command)
    if trailing:
        # Preserve trailing whitespace exactly so the round-trip is byte-exact.
        command = Command("", "", trailing, 0, parsed=False)
        commands.append(command)
    return commands


def _resolve_keyword(word: str) -> str | None:
    """Resolve a command keyword, honouring Thermo-Calc's abbreviations.

    Real TDB files abbreviate commands -- ``CONST`` for ``CONSTITUENT``,
    ``PARA`` for ``PARAMETER``, ``TYPE_DEF`` for ``TYPE_DEFINITION``,
    ``TEMP_LIM`` for ``TEMPERATURE_LIMITS``.  ``COST507.tdb`` uses ``CONST`` for
    228 of its constituent lines; treating that as an unknown command silently
    dropped every one of them and left 486 sublattices with no constituents.

    An abbreviation is accepted only when it is a prefix of exactly one known
    keyword, so genuinely ambiguous forms stay unparsed rather than being
    guessed at.
    """
    upper = word.upper()
    all_keywords = KNOWN_COMMANDS | PROSE_COMMANDS
    if upper in all_keywords:
        return upper
    matches = [k for k in all_keywords if k.startswith(upper)]
    if len(matches) == 1:
        return matches[0]
    return None


def _build_command(joined: str, line: int) -> Command:
    match = re.match(r"([A-Za-z_][A-Za-z_0-9]*)\s*(.*)$", joined, re.S)
    if not match:
        return Command("", joined, joined, line, parsed=False)
    word = match.group(1)
    body = match.group(2).strip()
    keyword = _resolve_keyword(word)
    if keyword is None:
        # Unknown command: keep it verbatim rather than discarding it.
        return Command(word.upper(), body, joined, line, parsed=False)
    return Command(keyword, body, joined, line, parsed=True)


# ---------------------------------------------------------------------------
# field parsers
# ---------------------------------------------------------------------------

_ELEMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_+\-/]+)\s+"
    r"(?P<ref>\S+)\s+"
    r"(?P<mass>[-\d.EeDd+]+)\s+"
    r"(?P<h>[-\d.EeDd+]+)\s+"
    r"(?P<s>[-\d.EeDd+]+)$"
)

_RANGE_RE = re.compile(
    r"^\s*(?P<expr>.*?);\s*(?P<tmax>[-\d.EeDd+]+)\s+(?P<term>[YN])\s*$",
    re.S,
)

_PARAMETER_RE = re.compile(
    r"^(?P<kind>[A-Za-z_][A-Za-z_0-9]*)\s*\((?P<inner>[^)]*)\)\s*(?P<rest>.*)$",
    re.S,
)


def _to_float(token: str, *, line: int, what: str) -> float:
    """Parse a Fortran-ish float, accepting ``D`` exponents and leading zeros."""
    cleaned = token.strip().replace("D", "E").replace("d", "e")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ParseError(f"malformed {what}: {token!r}", line=line) from exc


def _split_top_level(text: str, separator: str) -> List[str]:
    """Split on ``separator`` at parenthesis depth zero."""
    out: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == separator and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    out.append("".join(current))
    return out


def _parse_element(command: Command) -> Element:
    match = _ELEMENT_RE.match(command.body)
    if not match:
        raise ParseError(
            f"malformed ELEMENT command: {command.text!r}", line=command.line
        )
    return Element(
        name=match.group("name").upper(),
        reference_phase=match.group("ref"),
        mass=_to_float(match.group("mass"), line=command.line, what="element mass"),
        h298=_to_float(match.group("h"), line=command.line, what="H298"),
        s298=_to_float(match.group("s"), line=command.line, what="S298"),
    )


def _parse_species(command: Command) -> Species:
    parts = command.body.split()
    if not parts:
        raise ParseError(
            f"malformed SPECIES command: {command.text!r}", line=command.line
        )
    name = parts[0].upper()
    charge = 0.0
    if len(parts) > 2:
        charge = _to_float(parts[2], line=command.line, what="species charge")
    return Species(name=name, stoichiometry={}, charge=charge)


def _parse_function(command: Command) -> Function:
    parts = command.body.split(None, 1)
    if len(parts) < 2:
        raise ParseError(
            f"malformed FUNCTION command: {command.text!r}", line=command.line
        )
    return Function(
        name=parts[0].upper(), expression=parts[1].strip(), source_line=command.line
    )


def _parse_type_definition(command: Command) -> TypeDefinition:
    match = re.match(
        r"^(?P<symbol>\S+)\s+(?P<kind>\S+)\s*(?P<body>.*)$", command.body, re.S
    )
    if not match:
        raise ParseError(
            f"malformed TYPE_DEFINITION command: {command.text!r}", line=command.line
        )
    symbol = match.group("symbol")
    kind = match.group("kind").upper()
    body = match.group("body").strip()
    options: Tuple[str, ...] = ()
    if kind == "GES":
        options = tuple(body.split())
    return TypeDefinition(
        symbol=symbol, kind=kind, body=body, options=options, source_line=command.line
    )


def _parse_phase(command: Command) -> Phase:
    tokens = command.body.split()
    if len(tokens) < 4:
        raise ParseError(
            f"malformed PHASE command (expected name, type code, sublattice count "
            f"and ratios): {command.text!r}",
            line=command.line,
        )
    name = tokens[0]
    # A phase name may carry an atom count after a colon, e.g. "LIQUID:L" or
    # "SIGMA:30".  The suffix is part of the model, not the name, and parameters
    # refer to the bare name.
    bare_name = name.split(":", 1)[0]
    type_code = tokens[1]
    try:
        count = int(tokens[2])
    except ValueError as exc:
        raise ParseError(
            f"malformed PHASE sublattice count: {tokens[2]!r}", line=command.line
        ) from exc
    ratios_raw = tokens[3 : 3 + count]
    if len(ratios_raw) != count:
        raise ParseError(
            f"PHASE {name!r} declares {count} sublattices but gives "
            f"{len(ratios_raw)} ratios",
            line=command.line,
        )
    ratios = [
        _to_float(t, line=command.line, what="sublattice ratio") for t in ratios_raw
    ]
    return Phase(
        name=bare_name,
        type_code=type_code,
        model="",
        sublattices=tuple(
            Sublattice(constituents=(), ratio=r) for r in ratios
        ),
    )


def _parse_constituent(command: Command) -> Tuple[str, Tuple[Sublattice, ...]]:
    match = re.match(r"^(\S+)\s*:\s*(.*)$", command.body, re.S)
    if not match:
        raise ParseError(
            f"malformed CONSTITUENT command: {command.text!r}", line=command.line
        )
    # The atom-count suffix appears on the CONSTITUENT line too, e.g.
    # "CONSTITUENT BCC_4SL:B :AL,FE:...", and must be stripped the same way as
    # on the PHASE line or the lookup will not match.
    name = match.group(1).split(":", 1)[0]
    rest = match.group(2).strip()
    if rest.endswith(":"):
        rest = rest[:-1]
    sublattices: List[Sublattice] = []
    for chunk in rest.split(":"):
        names = tuple(c.strip() for c in chunk.split(",") if c.strip())
        sublattices.append(Sublattice(constituents=names, ratio=1.0))
    return name, tuple(sublattices)


def _parse_parameter(command: Command) -> Parameter:
    match = _PARAMETER_RE.match(command.body)
    if not match:
        raise ParseError(
            f"malformed PARAMETER command: {command.text!r}", line=command.line
        )
    kind = match.group("kind").upper()
    inner = match.group("inner")
    rest = match.group("rest").strip()

    pieces = _split_top_level(inner, ",")
    phase_name = pieces[0].strip()
    tail = ",".join(pieces[1:]).strip()

    order: int | None = None
    if ";" in tail:
        constituents_part, _, order_text = tail.partition(";")
        tail = constituents_part.strip()
        order_text = order_text.strip()
        if order_text:
            try:
                order = int(order_text)
            except ValueError as exc:
                raise ParseError(
                    f"malformed parameter order {order_text!r} in {command.text!r}",
                    line=command.line,
                ) from exc

    constituents: Tuple[Tuple[str, ...], ...] = tuple(
        tuple(c.strip() for c in chunk.split(",") if c.strip())
        for chunk in tail.split(":")
    )
    return Parameter(
        kind=kind,
        phase=phase_name,
        constituents=constituents,
        order=order,
        expression=rest,
        source_line=command.line,
    )


# ---------------------------------------------------------------------------
# reader
# ---------------------------------------------------------------------------


def read(text: str, *, source: str | None = None) -> Database:
    """Parse TDB source text into a :class:`~calphad_io.model.Database`.

    Raises
    ------
    ParseError
        If a command this library claims to understand is malformed.  Commands
        it does not understand are preserved verbatim instead of raising.
    """
    commands = split_commands(text)

    if not any(c.parsed for c in commands):
        raise ParseError(
            "no TDB commands found; this does not look like a TDB file",
            line=1,
            source=source,
        )

    elements: Dict[str, Element] = {}
    species: Dict[str, Species] = {}
    functions: Dict[str, Function] = {}
    type_definitions: Dict[str, TypeDefinition] = {}
    phases: Dict[str, Phase] = {}
    parameters: List[Parameter] = []
    default_commands: List[str] = []
    system_default: str | None = None
    pending_constituent: Tuple[str, Tuple[Sublattice, ...]] | None = None
    # The lookup dicts dedupe silently, so duplicates are counted here.  A file
    # that declares the same phase twice is a real defect a solver will trip on.
    duplicate_counts: Dict[str, int] = {}

    for command in commands:
        if not command.parsed or command.keyword not in KNOWN_COMMANDS:
            continue
        try:
            if command.keyword == "ELEMENT":
                element = _parse_element(command)
                if element.name in elements:
                    duplicate_counts[f"element:{element.name}"] = (
                        duplicate_counts.get(f"element:{element.name}", 1) + 1
                    )
                elements[element.name] = element
            elif command.keyword == "SPECIES":
                sp = _parse_species(command)
                species[sp.name] = sp
            elif command.keyword == "FUNCTION":
                fn = _parse_function(command)
                if fn.name in functions:
                    duplicate_counts[f"function:{fn.name}"] = (
                        duplicate_counts.get(f"function:{fn.name}", 1) + 1
                    )
                functions[fn.name] = fn
            elif command.keyword == "TYPE_DEFINITION":
                td = _parse_type_definition(command)
                if td.symbol in type_definitions:
                    duplicate_counts[f"type_definition:{td.symbol}"] = (
                        duplicate_counts.get(f"type_definition:{td.symbol}", 1) + 1
                    )
                type_definitions[td.symbol] = td
            elif command.keyword == "PHASE":
                phase = _parse_phase(command)
                key = phase.name.upper()
                if key in phases:
                    duplicate_counts[f"phase:{key}"] = (
                        duplicate_counts.get(f"phase:{key}", 1) + 1
                    )
                phases[key] = phase
            elif command.keyword == "CONSTITUENT":
                # Apply immediately: PHASE always precedes its CONSTITUENT in
                # every file we have seen, and deferring would mean only the
                # last CONSTITUENT in the file took effect.
                cname, sublattices = _parse_constituent(command)
                key = cname.upper()
                existing = phases.get(key)
                if existing is not None and len(existing.sublattices) == len(
                    sublattices
                ):
                    merged = tuple(
                        Sublattice(constituents=s.constituents, ratio=old.ratio)
                        for s, old in zip(
                            sublattices, existing.sublattices, strict=False
                        )
                    )
                    phases[key] = replace(existing, sublattices=merged)
                else:
                    pending_constituent = (cname, sublattices)
            elif command.keyword == "PARAMETER":
                parameters.append(_parse_parameter(command))
            elif command.keyword == "DEFINE_SYSTEM_DEFAULT":
                system_default = command.body.strip()
            elif command.keyword == "DEFAULT_COMMAND":
                default_commands.append(command.body.strip())
        except ParseError:
            raise
        except (ValueError, IndexError) as exc:  # pragma: no cover - defensive
            raise ParseError(
                f"failed to parse command: {command.text!r}",
                line=command.line,
            ) from exc

    # A CONSTITUENT line carries the ratios from its PHASE line, so it can only
    # be applied once the phase exists.  Real files always write PHASE first.
    if pending_constituent is not None:
        cname, sublattices = pending_constituent
        key = cname.upper()
        if key in phases:
            existing = phases[key]
            merged = tuple(
                Sublattice(constituents=s.constituents, ratio=old.ratio)
                for s, old in zip(sublattices, existing.sublattices, strict=False)
            )
            if len(merged) == len(existing.sublattices):
                phases[key] = replace(existing, sublattices=merged)

    header: Dict[str, object] = {
        "default_commands": tuple(default_commands),
        "system_default": system_default or "",
        "duplicate_declarations": dict(duplicate_counts),
    }

    return Database(
        format=Format.TDB,
        source=source,
        elements=tuple(elements.values()),
        species=tuple(species.values()),
        phases=tuple(phases.values()),
        parameters=tuple(parameters),
        functions=tuple(functions.values()),
        type_definitions=tuple(type_definitions.values()),
        header=header,
        raw=text,
    )


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------


def _wrap(prefix: str, body: str) -> List[str]:
    """Wrap a long command body at spaces, keeping operators with their terms."""
    lines: List[str] = []
    current = prefix
    for token in body.split():
        candidate = f"{current} {token}" if current.strip() else token
        if len(candidate) > _WRAP and current.strip():
            lines.append(current.rstrip())
            current = "    " + token
        else:
            current = candidate
    lines.append(current.rstrip())
    return lines


def _render_element(element: Element) -> str:
    return (
        f"ELEMENT {element.name} {element.reference_phase or 'BLANK'} "
        f"{element.mass:.4E} {element.h298:.4E} {element.s298:.4E}"
    )


def _render_species(species: Species) -> str:
    return f"SPECIES {species.name} {species.name} {species.charge:.6E}"


def _render_function(function: Function) -> str:
    return f"FUNCTION {function.name} {function.expression}"


def _render_type_definition(definition: TypeDefinition) -> str:
    return f"TYPE_DEFINITION {definition.symbol} {definition.kind} {definition.body}"


def _render_phase(phase: Phase) -> List[str]:
    ratios = " ".join(f"{s.ratio:g}" for s in phase.sublattices)
    lines = [
        f"PHASE {phase.name} {phase.type_code or '%'} "
        f"{len(phase.sublattices)} {ratios}"
    ]
    constituents = ":".join(
        ",".join(s.constituents) for s in phase.sublattices
    )
    if constituents.strip(","):
        lines.append(f"CONSTITUENT {phase.name} :{constituents}:")
    return lines


def _render_parameter(parameter: Parameter) -> str:
    constituents = ":".join(",".join(s) for s in parameter.constituents)
    order = "" if parameter.order is None else f";{parameter.order}"
    head = f"PARAMETER {parameter.kind}({parameter.phase},{constituents}{order})"
    return f"{head} {parameter.expression}"


def write(database: Database) -> str:
    """Render a :class:`~calphad_io.model.Database` back to TDB text.

    If ``database`` was read from a file and has not been modified, its original
    source text is returned unchanged and the result is byte-for-byte identical
    to the input.

    Otherwise every record is rendered from the object model in a canonical
    layout.  Commands that this library did not parse are **not** re-emitted in
    that path -- they are only preserved on the pristine path.
    """
    if database.format != Format.TDB:
        raise WriteError(
            f"cannot write a {database.format!r} database as TDB; "
            "convert it first"
        )

    if database.raw is not None:
        return database.raw

    lines: List[str] = []
    for element in database.elements:
        lines.extend(_wrap("", _render_element(element)))
    for species in database.species:
        lines.extend(_wrap("", _render_species(species)))
    for function in database.functions:
        lines.extend(_wrap("", _render_function(function)))
    for definition in database.type_definitions:
        lines.extend(_wrap("", _render_type_definition(definition)))

    system_default = str(database.header.get("system_default", "") or "")
    if system_default:
        lines.append(f"DEFINE_SYSTEM_DEFAULT {system_default}")
    defaults = database.header.get("default_commands") or ()
    for default in defaults if isinstance(defaults, (list, tuple)) else ():
        lines.append(f"DEFAULT_COMMAND {default}")

    for phase in database.phases:
        if not phase.sublattices:
            raise WriteError(
                f"phase {phase.name!r} has no sublattice model; "
                "it cannot be written as TDB"
            )
        lines.extend(_wrap("", _render_phase(phase)[0]))
        rendered = _render_phase(phase)
        for extra in rendered[1:]:
            lines.extend(_wrap("", extra))

    for parameter in database.parameters:
        if not parameter.expression.strip():
            raise WriteError(
                f"parameter {parameter.signature()} has an empty expression"
            )
        lines.extend(_wrap("", _render_parameter(parameter)))

    terminated = [f"{line} !" if line.strip() else line for line in lines]
    return "\n".join(terminated) + "\n"
