"""Command-line interface.

Four subcommands, all of which accept ``--json``:

``info``      summarise a database
``parse``     parse it and report what was found (or fail loudly)
``validate``  report structural problems as structured findings
``convert``   re-render a database in its canonical layout

Exit codes are stable and meant for scripting::

    0   success, nothing wrong
    1   validation found errors, or conversion refused
    2   the file could not be parsed at all
    3   bad usage
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Sequence

from . import __version__, convert, load
from .errors import CalphadIOError, ParseError, UnsupportedFeatureError, WriteError
from .model import Database, Format
from .validate import Finding, Severity, counts_by_severity, validate

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_PARSE_ERROR = 2
EXIT_USAGE = 3


def _finding_dicts(findings: Sequence[Finding]) -> List[Dict[str, object]]:
    return [f.to_dict() for f in findings]


def _print_findings_text(findings: Sequence[Finding]) -> None:
    if not findings:
        print("no findings")
        return
    for finding in findings:
        location = f"  [{finding.location}]" if finding.location else ""
        print(f"{finding.severity.upper():<7} {finding.code:<32}{location}")
        print(f"        {finding.message}")


def _summary_dict(database: Database) -> Dict[str, object]:
    summary = dict(database.summary())
    summary["elements"] = list(database.element_names())
    summary["phase_names"] = [p.name for p in database.phases]
    return summary


def _emit(payload: Dict[str, object], as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_info(args: argparse.Namespace) -> int:
    database = load(args.path, format=args.format)
    summary = _summary_dict(database)
    findings = validate(database)
    tally = counts_by_severity(findings)

    if args.json:
        _emit(
            {
                "ok": True,
                "summary": summary,
                "validation": {"counts": tally, "findings": _finding_dicts(findings)},
            },
            True,
        )
        return EXIT_OK

    print(f"file       {database.source}")
    print(f"format     {database.format}")
    for key, value in summary.items():
        if key in ("format", "source", "elements", "phase_names"):
            continue
        print(f"{key:<26} {value}")
    print(f"elements   {', '.join(summary['elements'])}")  # type: ignore[arg-type]
    print()
    print(
        f"validation: {tally[Severity.ERROR]} error(s), "
        f"{tally[Severity.WARNING]} warning(s), {tally[Severity.INFO]} info"
    )
    return EXIT_OK


def cmd_parse(args: argparse.Namespace) -> int:
    database = load(args.path, format=args.format)
    phases = [
        {
            "name": p.name,
            "model": p.model or p.type_code,
            "sublattices": [
                {"ratio": s.ratio, "constituents": list(s.constituents)}
                for s in p.sublattices
            ],
            "is_stoichiometric": p.is_stoichiometric,
            "is_dummy": p.is_dummy,
            "parameters": len(database.parameters_for(p.name)),
        }
        for p in database.phases
    ]
    parameters = [
        {
            "kind": p.kind,
            "phase": p.phase,
            "constituents": [list(c) for c in p.constituents],
            "order": p.order,
            "expression": p.expression,
            "line": p.source_line,
        }
        for p in database.parameters
    ]

    if args.json:
        _emit(
            {
                "ok": True,
                "summary": _summary_dict(database),
                "phases": phases,
                "parameters": parameters,
                "functions": [f.name for f in database.functions],
                "type_definitions": [
                    {"symbol": t.symbol, "kind": t.kind}
                    for t in database.type_definitions
                ],
            },
            True,
        )
        return EXIT_OK

    print(f"{database.source}: {database.format}")
    print(f"{len(database.phases)} phase(s), {len(database.parameters)} parameter(s)")
    for phase in phases:
        model = phase["model"] or "-"
        print(f"  {phase['name']:<32} {model:<12} {phase['parameters']} parameter(s)")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    database = load(args.path, format=args.format)
    findings = validate(database)
    tally = counts_by_severity(findings)

    if args.json:
        _emit(
            {
                "ok": tally[Severity.ERROR] == 0,
                "file": database.source,
                "counts": tally,
                "findings": _finding_dicts(findings),
            },
            True,
        )
    else:
        _print_findings_text(findings)
        print()
        print(
            f"{tally[Severity.ERROR]} error(s), {tally[Severity.WARNING]} "
            f"warning(s), {tally[Severity.INFO]} info"
        )

    if args.strict and tally[Severity.ERROR] > 0:
        return EXIT_FINDINGS
    if tally[Severity.ERROR] > 0:
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_convert(args: argparse.Namespace) -> int:
    database = load(args.path, format=args.format)
    result = convert(database, args.to)
    text = result.text

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    if args.json:
        _emit(
            {
                "ok": True,
                "input": database.source,
                "from": database.format,
                "to": Format.normalise(args.to),
                "output": args.output,
                "bytes_in": len(database.raw) if database.raw else None,
                "bytes_out": len(text),
                "losses": list(result.losses),
            },
            True,
        )
    else:
        if not args.output:
            sys.stdout.write(text)
        else:
            print(f"wrote {args.output} ({len(text)} bytes)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser (exposed for tests and for ``--help``)."""
    parser = argparse.ArgumentParser(
        prog="calphad-io",
        description=(
            "Read, write and validate CALPHAD thermodynamic databases "
            "(Thermo-Calc .TDB and ChemSage/FactSage .DAT)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"calphad-io {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("path", help="path to a .TDB or .DAT file")
        sub.add_argument(
            "--format",
            choices=list(Format.ALL),
            default=None,
            help="override format detection",
        )
        sub.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON"
        )

    info = subparsers.add_parser("info", help="summarise a database")
    add_common(info)
    info.set_defaults(func=cmd_info)

    parse = subparsers.add_parser("parse", help="parse a database and list its contents")
    add_common(parse)
    parse.set_defaults(func=cmd_parse)

    validate_cmd = subparsers.add_parser(
        "validate", help="report structural problems as findings"
    )
    add_common(validate_cmd)
    validate_cmd.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when any error-level finding is present",
    )
    validate_cmd.set_defaults(func=cmd_validate)

    convert_cmd = subparsers.add_parser(
        "convert", help="re-render a database in its canonical layout"
    )
    add_common(convert_cmd)
    convert_cmd.add_argument(
        "--to",
        required=True,
        choices=list(Format.ALL),
        help="target format (only the database's own format is supported)",
    )
    convert_cmd.add_argument("-o", "--output", default=None, help="write to this file")
    convert_cmd.set_defaults(func=cmd_convert)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.  Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    try:
        return int(args.func(args))
    except (ParseError, UnsupportedFeatureError) as exc:
        if getattr(args, "json", False):
            json.dump(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "line": getattr(exc, "line", None),
                },
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_PARSE_ERROR
    except WriteError as exc:
        if getattr(args, "json", False):
            json.dump(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
        else:
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_FINDINGS
    except CalphadIOError as exc:  # pragma: no cover - defensive
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PARSE_ERROR
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
