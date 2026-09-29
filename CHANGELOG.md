# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- README install instructions corrected: `calphad-io` is not yet published to
  PyPI, so the non-working `pip install calphad-io` command was replaced with a
  working install-from-source command.

## [0.1.0] - 2026-09-29

Initial release.

### Added

- **TDB reader and writer.** Parses `ELEMENT`, `SPECIES`, `FUNCTION`,
  `TYPE_DEFINITION`, `DEFINE_SYSTEM_DEFAULT`, `DEFAULT_COMMAND`, `PHASE`,
  `CONSTITUENT` and `PARAMETER`, honouring Thermo-Calc's abbreviated keywords
  (`CONST`, `PARA`, `TYPE_DEF`, `TEMP_LIM`) and its `$` comment syntax. Commands
  the library does not understand are preserved verbatim rather than discarded.
- **Byte-exact TDB round-trip.** A database that is read and written back
  without modification is emitted from its original source text and is
  byte-for-byte identical to the input.
- **DAT reader and writer.** Parses the ChemSage/FactSage format including
  `IDMX`, `RKMP`, `RKMPM`, `SUBL`, `SUBLM`, `QKTO`, `SUBQ` and `SUBG` phase
  models, with per-endmember thermodynamic data options, P-T molar-volume terms,
  magnetic factors, QKTO chemical groups, MQMQA quadruplet coordinations and
  chemical-group overrides.
- **Semantically exact DAT round-trip.** Every element, phase, endmember,
  interval coefficient, excess term and magnetic term is preserved exactly, and
  every number is emitted in a form that converts back to the identical
  `float`.
- **Validator.** 20 finding codes across `error`, `warning` and `info`
  severities, returned as structured findings. `validate()` never raises.
- **CLI** with `info`, `parse`, `validate` and `convert` subcommands, all
  supporting `--json`, with stable exit codes.
- **Zero runtime dependencies.** Pure standard library, Python 3.10+.
- Test suite built on 26 real databases from pycalphad and Thermochimica
  (9 TDB, 17 DAT; 25 round-trip cleanly and 1 is refused by design), plus
  nine deliberately corrupted fixtures.

### Notes

- Cross-format conversion (TDB to DAT and DAT to TDB) is deliberately refused.
  Neither direction can be done faithfully from the information the other format
  carries, and guessing would produce a file that computes the wrong energy.
- Round-trip fidelity is verified against `pycalphad` as an independent reader:
  it reports all 25 regenerated databases it can read as semantically identical
  to the originals, including model hints. (`FeMnCaS-1.dat` uses `SUBI`, which
  pycalphad cannot read either, so it is refused rather than compared.)
- That comparison is reproducible end to end via `scripts/generate.py` and
  `scripts/verify.py`; see `scripts/README.md`.
