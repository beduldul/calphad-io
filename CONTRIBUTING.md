# Contributing

Thanks for considering a contribution. This library exists to make CALPHAD
databases safe to move between tools, and the most valuable contributions are
**real databases** and **bug reports backed by one**.

## Before you write code, read this

`calphad-io` is a workaround, not a destination. `pycalphad` PR
[#422](https://github.com/pycalphad/pycalphad/pull/422) already implements much
of the same thing and has been open, unmerged and dormant since 2022. If you are
here to improve `.DAT` writing in general, **your work is probably more valuable
applied there than here.**

Concretely, if you are about to implement a feature:

1. Check whether #422 already has it.
2. If it does, consider contributing the *evidence* instead — a test fixture, a
   round-trip case, a bug report with a real database — to that pull request.
3. Only add it here if the goal is specifically to keep a standalone,
   dependency-free library working.

Contributions that improve the format knowledge (new fixtures, newly documented
quirks, corrections to the `## Limitations` section) are welcome either way, and
are the ones most likely to outlive this project.

## Getting set up

```bash
git clone https://github.com/beduldul/calphad-io
cd calphad-io
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The suite runs in under a second and needs no network access.

## The two rules that matter

**1. Round-trip fidelity is the headline feature.** Any change to the readers or
writers must keep `pytest tests/test_roundtrip.py` green. If you add support for
a construct, add the file that exercises it to `tests/data/`.

**2. A validator that cries wolf is worse than none.** Every check must be
justified against a real file. Before adding a finding code, ask: *does a
healthy database ever trip this?* If yes, it is a `warning` or an `info`, not an
`error` -- or it is not a finding at all. Several checks in `validate.py` carry a
comment explaining the real file that taught us the rule; please do the same.

## Reporting a bug

The single most useful thing you can attach is a database that reproduces it. If
you cannot share the file, reduce it to a minimal snippet and say which construct
is involved.

For a round-trip bug, please include:

```python
import calphad_io
db = calphad_io.load("your-file.TDB")
print(calphad_io.dumps(db) == open("your-file.TDB").read())
```

## Adding a database fixture

1. Drop the file in `tests/data/`.
2. Check the licence permits redistribution, and note the source in your pull
   request. Databases from pycalphad and Thermochimica are already present.
3. Run the suite. A new file is automatically picked up by the parametrised
   tests in `tests/conftest.py`.
4. If the library refuses the file, add it to `UNSUPPORTED` in `conftest.py`
   with the reason, and open an issue.

## Style

- Typed throughout. `mypy --strict` and `ruff` are configured in
  `pyproject.toml`; both must pass.
- No runtime dependencies. The formats are text; do not reach for NumPy.
- Frozen dataclasses for anything in the object model. Return new objects
  instead of mutating.
- Comments should explain *why*, especially where a real file forced an
  unexpected decision. Those comments are the most valuable documentation in
  this codebase.

## What is out of scope

- Thermodynamic evaluation, phase-diagram calculation, model fitting. This is an
  I/O library; it deliberately has no opinion about whether a model is
  physically sensible.
- Cross-format conversion. See the README for why it is refused.
- FactSage 8.1 and later DAT files.

## Commits and pull requests

Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
Keep pull requests focused. If you change the public API or add a finding code,
update `README.md` in the same pull request.
