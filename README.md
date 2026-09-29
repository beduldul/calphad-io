# calphad-io

**Read, write and validate CALPHAD thermodynamic databases — Thermo-Calc `.TDB`
and ChemSage/FactSage `.DAT` — with zero runtime dependencies.**

## Read this first: this library is not the first attempt

Before you adopt `calphad-io`, you should know what it is and is not.

**There is already a ChemSage `.DAT` writer for pycalphad. It was written, and
then it was abandoned before it could be merged.**

* [`pycalphad` PR **#422**, "ENH: Implement writing of ChemSage DAT files"](https://github.com/pycalphad/pycalphad/pull/422)
  is **open and unmerged**. It was opened **2022-06-17**, contains **97 commits,
  all of them from 2022**, and is **+1042/−3 to a single file**.
* Its first review was `CHANGES_REQUESTED`, asking for tests. A maintainer
  replied *"this is still high on my list"* in **August 2022**, and it has sat
  since. The pull request has been dormant for four years.

So the honest framing is **not** "no tool exists". It is:

> **The tool was written and abandoned before merge, and is not installable by
> anyone.**

That distinction matters, and it changes what you should do with this project.

### If you need a `.DAT` writer, consider helping #422 first

`calphad-io` exists because a usable artifact did not. It is not an argument that
the existing work should be discarded — it is a workaround for the fact that
#422 never landed. **If you use this library, please also consider contributing
to #422 or linking it from there.** The goal is for the CALPHAD community to have
a working DAT writer, not for this library to win. A revived #422 in pycalphad —
where the maintainers, the reviewers and the test suite already are — is a better
outcome than a parallel implementation.

The one thing `calphad-io` can offer that discussion is *evidence*: a corpus of
real databases and a round-trip harness that reads, writes and re-verifies them.
That evidence is more useful applied to #422 than hoarded here.

## The problem

CALPHAD databases are the input to every thermodynamic calculation, and both
formats in circulation are text. Yet there is no general-purpose,
dependency-free library that reads *and writes* both:

* `pycalphad` reads `.TDB` and `.DAT` and writes `.TDB`, but has no `.DAT`
  writer. Its maintainer explained why in
  [pycalphad#413](https://github.com/pycalphad/pycalphad/issues/413) (open since
  2022-05-12): *"That was my main deterrent for not implementing a DAT writer, as
  I don't have FactSage to test against as a reference implementation."*
* `Thermochimica` consumes `.DAT` and has no general writer.
* `OpenCalphad`'s `save_datformat` routine is marked in its own source as
  *"writes a SOLGASMIX DAT format file. **not (ever?) finished**"*.
* The only widely-used tooling around the format is a syntax highlighter
  ([`amkrajewski/TDB-Highlighter`](https://github.com/amkrajewski/TDB-Highlighter),
  13 stars).

`calphad-io` fills that gap. It is a parser, a writer and a validator. It does
**not** compute thermodynamics — it gets your database *to* the solver intact.

## Scope, stated honestly

| | `.TDB` | `.DAT` |
|---|---|---|
| Read | yes | yes (subset — see Limitations) |
| Write | **byte-for-byte identical** round-trip | semantically identical round-trip |
| Validate | yes | yes |
| Convert to the other format | **refused, deliberately** | **refused, deliberately** |

* **TDB round-trip is byte-exact.** Nine real databases — including the 296 KB
  `COST507.tdb` — parse and re-emit to the identical bytes.
* **DAT round-trip is semantically exact.** Every element, phase, endmember,
  interval coefficient, excess term and magnetic term is preserved, and every
  number is emitted in a form that converts back to the identical `float`.
  Formatting and comments are not preserved: DAT files are hand-edited in
  practice and no reader in the ecosystem depends on column positions.
* **Both are verified against an independent implementation.** `pycalphad` reads
  the regenerated files as *identical* — same elements, species, phases,
  sublattice constituents, model hints and every parameter expression — for
  **all 25 regenerated files** — every file this library can read. For three of
  them the computed Gibbs energy was also
  compared across their temperature ranges and was **bit-identical**
  (relative difference exactly `0.00e+00`).

## Install

```bash
pip install calphad-io
```

No dependencies. Python 3.10+.

## Use

### Library

```python
import calphad_io

db = calphad_io.load("steel.TDB")
print(db.summary())
# {'format': 'tdb', 'elements': 29, 'phases': 243, 'parameters': 1907, ...}

for phase in db.phases:
    print(phase.name, phase.constituent_names())

# Round-trip: TDB comes back byte-for-byte
assert calphad_io.dumps(db) == open("steel.TDB").read()

# Validate before it reaches a solver
for finding in calphad_io.validate(db):
    print(finding)
```

### Command line

```bash
calphad-io info     steel.TDB
calphad-io parse    steel.TDB
calphad-io validate steel.TDB
calphad-io validate steel.TDB --json
calphad-io convert  steel.TDB --to tdb -o canonical.TDB
```

Exit codes are stable: `0` clean, `1` validation errors or a refused conversion,
`2` unparseable, `3` bad usage.

## The object model

Eight immutable frozen dataclasses in `calphad_io.model` (`Format` is a plain
class of string constants, not a dataclass), shared by both formats. Nothing is
mutated after construction; transformations return new objects.

```
Database
├── format: "tdb" | "dat"
├── elements:  tuple[Element]        name, reference_phase, mass, h298, s298
├── species:   tuple[Species]        name, stoichiometry, charge
├── phases:    tuple[Phase]          name, sublattices, type_code, model,
│                                    kind, is_dummy, is_stoichiometric
│     └── sublattices: tuple[Sublattice]   constituents, ratio
├── parameters: tuple[Parameter]     kind, phase, constituents, order,
│                                    expression, source_line
├── functions: tuple[Function]       name, expression            (TDB)
└── type_definitions: tuple[TypeDefinition]  symbol, kind, options (TDB)
```

Two design choices are worth calling out.

**Parameter expressions are text, never evaluated.** `calphad-io` checks that an
expression is well-formed enough to re-emit; it has no opinion about whether it
is thermodynamically sensible. That is the solver's job.

**`Database` is not the only record.** For DAT, the file's full structure — the
per-endmember thermodynamic data options, P-T molar-volume terms, magnetic
factors, QKTO chemical groups, MQMQA quadruplet coordinations — lives in a
`DatLayout` under `db.header["layout"]`. The generic `Parameter` list is a
*projection for inspection*, and the writer uses the layout. This is documented
in `calphad_io.dat.layout`.

## Validation

`validate()` never raises. It returns `Finding` objects with a severity, a
stable code, a message and structured detail:

```
ERROR   duplicate_phase                  [phase BCC_A2]
        phase 'BCC_A2' is declared 2 times
ERROR   undefined_constituent            [phase FCC_A1]
        phase 'FCC_A1' references constituent 'XX', which is neither a
        declared element nor a declared species
WARNING element_mass_missing             [element CR]
        element 'CR' has a mass of zero
```

Run `calphad-io validate --json` for the same findings as machine-readable data.

The codes are: `duplicate_element`, `duplicate_phase`, `duplicate_parameter`,
`duplicate_function`, `duplicate_type_definition`, `no_phases`, `no_elements`,
`undefined_constituent`, `undefined_type_definition`, `unused_type_definition`,
`parameter_for_undefined_phase`, `empty_parameter_expression`,
`unbalanced_parentheses`, `constituent_count_mismatch`,
`phase_without_constituents`, `parameter_sublattice_mismatch`,
`suspicious_constituent_name`, `suspiciously_named_phase`,
`element_mass_missing`, `phase_has_no_parameters`.

## Limitations

**Not supported at all:**

* **`SUBI` (ionic two-sublattice)** — not read, not written. Affects
  `FeMnCaS-1.dat`, which this library refuses with a clear
  `UnsupportedFeatureError`. `pycalphad` cannot read it either (pycalphad#418).
* **`IDVD` (real gas) and `IDWZ` (aqueous/Pitzer)** phase models.
* **Heat-capacity thermodynamic data options** (7–12). These express an
  endmember as heat-capacity coefficients rather than a Gibbs energy
  polynomial. The reader rejects them explicitly rather than mis-reading them;
  `pycalphad` raises `NotImplementedError` here too.
* **Constant molar-volume options** (2, 5, 8, 11) and **P-T molar-volume
  terms** are parsed but not interpreted.
* **FactSage ≥ 8.1 DAT.** Out of scope by design; the format changed and 8.0-era
  tools cannot read the new files (pycalphad#413).
* **`DAT → TDB` and `TDB → DAT` conversion.** Refused, with an explanation of
  exactly what would be lost. A TDB records no per-endmember thermodynamic data
  option, no P-T molar-volume terms and no sublattice atom count; a DAT writer
  needs all three. Guessing produces a file that looks right and computes the
  wrong energy.

**Known caveats:**

* **No FactSage reference.** Correctness is evidenced by `pycalphad` reading the
  output as identical and by bit-identical computed energies — *not* by FactSage
  accepting the file. This is the same blocker the pycalphad maintainer named in
  2022. It is reduced but not eliminated, and the writer emits FactSage-8.0-style
  layout as faithfully as the evidence allows.
* **The validator still over-reports in a few DAT cases.** Constituent names in
  DAT are arbitrary human-readable labels with no enforced grammar
  (pycalphad#419), so a name that is neither an element nor a resolvable formula
  is reported as `undefined_constituent` even when a solver would accept it.
  Treat DAT `undefined_constituent` findings as *review this* rather than
  *this is broken*.
* **`duplicate_phase` for DAT is reported as `info`** when blocks differ, because
  real databases (`Kaye_Pd-Ru-Tc-Mo.dat`, `PdRuTcMo.dat`) legitimately contain
  several blocks with the same phase name.
* **No thermodynamic evaluation**, no phase-diagram calculation, no model
  fitting. This is an I/O library.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The test suite runs against **26 real databases** committed under `tests/data/`,
sourced from `pycalphad` and `Thermochimica`. It asserts, for every one of them,
that a parse → write → re-parse cycle preserves the model, and that TDB files
come back byte-for-byte. Deliberately corrupted copies under
`tests/data/corrupt/` exercise the validator.

## Prior art and credits

Built on the format knowledge accumulated in
[pycalphad](https://github.com/pycalphad/pycalphad) (Richard Otis, Brandon
Bocklund and contributors), particularly `pycalphad/io/cs_dat.py` and
`pycalphad/io/tdb.py`, and on the databases published by
[Thermochimica](https://github.com/ORNL-CEES/thermochimica) (ORNL-CEES). The
ChemSage `.DAT` layout was also cross-checked against OpenCalphad's unfinished
`save_datformat` routine.

## License

MIT. See [LICENSE](LICENSE).
