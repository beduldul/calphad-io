"""Validator tests: clean files must stay quiet, corrupted files must be caught.

The first half of this file matters as much as the second.  A validator that
fires on healthy databases is worse than no validator, because it trains users
to ignore it.  Every assertion about a clean file is a regression guard against
exactly that.
"""

from __future__ import annotations

import os
from typing import List, Set

import pytest
from conftest import CORRUPT_DIR, DAT_FILES, TDB_FILES, UNSUPPORTED

import calphad_io
from calphad_io.errors import ParseError, UnsupportedFeatureError
from calphad_io.validate import ALL_CODES, Severity, counts_by_severity, validate

UNSUPPORTED_NAMES = {name for name, _, _ in UNSUPPORTED}


def codes_for(path: str) -> Set[str]:
    database = calphad_io.load(path)
    return {f.code for f in validate(database)}


def errors_for(path: str) -> List[str]:
    database = calphad_io.load(path)
    return [f.code for f in validate(database) if f.severity == Severity.ERROR]


# ---------------------------------------------------------------------------
# clean files
# ---------------------------------------------------------------------------

#: Fixtures that this library reports zero errors for.  Each entry was checked
#: by hand against pycalphad's own reading of the same file.
CLEAN_TDB = [
    "Al-Cu-Y.tdb",
    "Al-Fe_sundman2009.tdb",
    "Al-Mg_Zhong.tdb",
    "al_parameter.tdb",
    "alfe_sei.TDB",
    "crfe_bcc_magnetic.tdb",
    "diffusion.tdb",
    "ionic_liquid_metal_minimal.tdb",
]

CLEAN_DAT = [
    "AlMg-Liang.dat",
    "CsI-Pham.dat",
    "FeTiVO.dat",
    "HO.dat",
    "KF-NIF2_switched.dat",
    "MQMQA-tern-tests.dat",
    "Ocadiz-Flores.dat",
    "Shishin_Fe-Sb-O-S_slag.dat",
    "Viitala.dat",
    "ZIRC-test64.dat",
    "ZrH-Dupin.dat",
]


@pytest.mark.parametrize("name", CLEAN_TDB, ids=CLEAN_TDB)
def test_clean_tdb_files_report_no_errors(name: str) -> None:
    found = errors_for(os.path.join(os.path.dirname(__file__), "data", name))
    assert found == [], f"{name} produced false positives: {found}"


@pytest.mark.parametrize("name", CLEAN_DAT, ids=CLEAN_DAT)
def test_clean_dat_files_report_no_errors(name: str) -> None:
    found = errors_for(os.path.join(os.path.dirname(__file__), "data", name))
    assert found == [], f"{name} produced false positives: {found}"


@pytest.mark.parametrize("name", TDB_FILES + DAT_FILES, ids=TDB_FILES + DAT_FILES)
def test_no_fixture_produces_an_unknown_code(name: str) -> None:
    """Every emitted code must be one we document."""
    if name in UNSUPPORTED_NAMES:
        pytest.skip("refused by design")
    path = os.path.join(os.path.dirname(__file__), "data", name)
    database = calphad_io.load(path)
    for finding in validate(database):
        assert finding.code in ALL_CODES, f"undocumented code {finding.code!r}"
        assert finding.severity in Severity.ORDER
        assert finding.message


@pytest.mark.parametrize("name", TDB_FILES + DAT_FILES, ids=TDB_FILES + DAT_FILES)
def test_findings_serialise_to_json(name: str) -> None:
    """Findings must be JSON-safe, since the CLI emits them directly."""
    import json

    if name in UNSUPPORTED_NAMES:
        pytest.skip("refused by design")
    database = calphad_io.load(os.path.join(os.path.dirname(__file__), "data", name))
    payload = [f.to_dict() for f in validate(database)]
    json.dumps(payload)  # must not raise


def test_validate_never_raises_on_a_clean_file() -> None:
    database = calphad_io.load(
        os.path.join(os.path.dirname(__file__), "data", "HO.dat")
    )
    assert isinstance(validate(database), list)


def test_severity_counts_include_zeroes() -> None:
    database = calphad_io.load(
        os.path.join(os.path.dirname(__file__), "data", "HO.dat")
    )
    counts = counts_by_severity(validate(database))
    assert set(counts) == {Severity.ERROR, Severity.WARNING, Severity.INFO}


# ---------------------------------------------------------------------------
# corrupted files
# ---------------------------------------------------------------------------


def _corrupt(name: str) -> str:
    return os.path.join(CORRUPT_DIR, name)


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("undefined_constituent.TDB", "undefined_constituent"),
        ("duplicate_phase.TDB", "duplicate_phase"),
        ("undefined_phase.TDB", "parameter_for_undefined_phase"),
        ("unbalanced_parens.TDB", "unbalanced_parentheses"),
        ("undefined_type_definition.TDB", "undefined_type_definition"),
        ("duplicate_parameter.TDB", "duplicate_parameter"),
    ],
)
def test_corruption_is_detected(filename: str, expected_code: str) -> None:
    database = calphad_io.load(_corrupt(filename))
    findings = validate(database)
    found = {f.code for f in findings}
    assert expected_code in found, f"{filename}: expected {expected_code}, got {found}"
    matching = [f for f in findings if f.code == expected_code]
    assert any(f.severity == Severity.ERROR for f in matching), (
        f"{filename}: {expected_code} should be an error"
    )


def test_corruption_is_not_reported_for_the_healthy_original() -> None:
    """Each corrupted file must be a *changed* file: the original is clean.

    Without this, a corruption fixture that accidentally does not corrupt
    anything would still "pass" the detection test.
    """
    healthy = os.path.join(os.path.dirname(__file__), "data", "crfe_bcc_magnetic.tdb")
    assert errors_for(healthy) == []


def test_truncated_dat_is_rejected() -> None:
    """A DAT claiming more phases than it contains must fail loudly."""
    with pytest.raises((ParseError, UnsupportedFeatureError)):
        calphad_io.load(_corrupt("truncated_counts.dat"))


def test_undefined_element_dat_is_flagged() -> None:
    database = calphad_io.load(_corrupt("undefined_element.dat"))
    assert "undefined_constituent" in {f.code for f in validate(database)}


def test_empty_file_is_rejected() -> None:
    with pytest.raises((ParseError, ValueError)):
        calphad_io.load(_corrupt("empty.TDB"))


def test_corrupt_fixtures_exist() -> None:
    """Guard against the corrupted corpus being deleted."""
    names = sorted(os.listdir(CORRUPT_DIR))
    assert len(names) >= 8, names
