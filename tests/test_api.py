"""Tests for the public API, the CLI and the object model."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

import pytest
from conftest import DATA_DIR, read_text

import calphad_io
from calphad_io import Format, WriteError
from calphad_io.cli import EXIT_FINDINGS, EXIT_OK, EXIT_PARSE_ERROR, main
from calphad_io.model import Element, Phase, Sublattice

HO_DAT = os.path.join(DATA_DIR, "HO.dat")
CRFE_TDB = os.path.join(DATA_DIR, "crfe_bcc_magnetic.tdb")


# ---------------------------------------------------------------------------
# format detection
# ---------------------------------------------------------------------------


def test_detect_format_uses_the_extension() -> None:
    assert calphad_io.detect_format("", filename="x.TDB") == Format.TDB
    assert calphad_io.detect_format("", filename="x.dat") == Format.DAT


def test_detect_format_sniffs_tdb_content() -> None:
    assert calphad_io.detect_format(read_text("crfe_bcc_magnetic.tdb")) == Format.TDB


def test_detect_format_sniffs_dat_content() -> None:
    assert calphad_io.detect_format(read_text("HO.dat")) == Format.DAT


def test_detect_format_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        calphad_io.detect_format("   \n  ")


def test_format_normalise_accepts_aliases() -> None:
    assert Format.normalise(".TDB") == Format.TDB
    assert Format.normalise("thermo-calc") == Format.TDB
    assert Format.normalise("factsage") == Format.DAT
    assert Format.normalise("chemsage") == Format.DAT
    with pytest.raises(ValueError):
        Format.normalise("xml")


# ---------------------------------------------------------------------------
# object model
# ---------------------------------------------------------------------------


def test_database_is_immutable() -> None:
    """Frozen dataclasses must reject attribute assignment."""
    database = calphad_io.load(HO_DAT)
    with pytest.raises(dataclasses.FrozenInstanceError):
        database.format = "tdb"  # type: ignore[misc]


def test_with_phase_returns_a_new_database() -> None:
    database = calphad_io.load(HO_DAT)
    new_phase = Phase(
        name="MADE_UP", sublattices=(Sublattice(constituents=("H",), ratio=1.0),)
    )
    updated = database.with_phase(new_phase)
    assert database is not updated
    assert updated.phase("MADE_UP") is not None
    assert database.phase("MADE_UP") is None
    assert len(updated.phases) == len(database.phases) + 1


def test_with_phase_replaces_an_existing_phase() -> None:
    database = calphad_io.load(HO_DAT)
    name = database.phases[0].name
    updated = database.with_phase(Phase(name=name, kind="cef"))
    assert len(updated.phases) == len(database.phases)
    assert updated.phase(name).kind == "cef"


def test_phase_lookup_is_case_insensitive() -> None:
    database = calphad_io.load(HO_DAT)
    name = database.phases[0].name
    assert database.phase(name.lower()) is not None
    assert database.phase(name.upper()) is not None
    assert database.phase("NO_SUCH_PHASE") is None


def test_element_is_pseudo() -> None:
    assert Element(name="VA").is_pseudo
    assert Element(name="/-").is_pseudo
    assert not Element(name="FE").is_pseudo


def test_summary_reports_counts() -> None:
    summary = calphad_io.load(HO_DAT).summary()
    assert summary["format"] == Format.DAT
    assert summary["phases"] == len(calphad_io.load(HO_DAT).phases)
    assert summary["elements"] == 2


def test_dumps_rejects_a_cross_format_request() -> None:
    database = calphad_io.load(HO_DAT)
    with pytest.raises(WriteError):
        calphad_io.dumps(database, format="tdb")


def test_convert_refuses_dat_to_tdb() -> None:
    database = calphad_io.load(HO_DAT)
    with pytest.raises(WriteError) as info:
        calphad_io.convert(database, "tdb")
    assert "no representation in TDB" in str(info.value)


def test_convert_refuses_tdb_to_dat() -> None:
    database = calphad_io.load(CRFE_TDB)
    with pytest.raises(WriteError) as info:
        calphad_io.convert(database, "dat")
    assert "will not guess" in str(info.value)


def test_convert_to_own_format_canonicalises() -> None:
    database = calphad_io.load(CRFE_TDB)
    result = calphad_io.convert(database, "tdb")
    assert result.text
    assert result.losses == ()


def test_loads_with_explicit_format() -> None:
    database = calphad_io.loads(read_text("HO.dat"), format="dat")
    assert database.format == Format.DAT


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_info_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", HO_DAT]) == EXIT_OK
    out = capsys.readouterr().out
    assert "format" in out
    assert "elements" in out


def test_cli_info_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", HO_DAT, "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["format"] == "dat"


def test_cli_parse_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["parse", CRFE_TDB, "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert any(p["name"] == "BCC_A2" for p in payload["phases"])
    assert payload["parameters"]


def test_cli_validate_clean_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", HO_DAT]) == EXIT_OK
    assert "0 error(s)" in capsys.readouterr().out


def test_cli_validate_corrupt_file_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    corrupt = os.path.join(DATA_DIR, "corrupt", "undefined_constituent.TDB")
    assert main(["validate", corrupt]) == EXIT_FINDINGS


def test_cli_validate_json_reports_ok_false(
    capsys: pytest.CaptureFixture[str],
) -> None:
    corrupt = os.path.join(DATA_DIR, "corrupt", "duplicate_phase.TDB")
    assert main(["validate", corrupt, "--json"]) == EXIT_FINDINGS
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["counts"]["error"] >= 1


def test_cli_parse_error_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    corrupt = os.path.join(DATA_DIR, "corrupt", "empty.TDB")
    assert main(["validate", corrupt]) == EXIT_PARSE_ERROR


def test_cli_parse_error_json(capsys: pytest.CaptureFixture[str]) -> None:
    corrupt = os.path.join(DATA_DIR, "corrupt", "empty.TDB")
    assert main(["parse", corrupt, "--json"]) == EXIT_PARSE_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_cli_convert_writes_a_file(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    target = os.path.join(str(tmp_path), "out.TDB")
    assert main(["convert", CRFE_TDB, "--to", "tdb", "-o", target]) == EXIT_OK
    assert os.path.getsize(target) > 0
    capsys.readouterr()


def test_cli_convert_refusal_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["convert", HO_DAT, "--to", "tdb"]) == EXIT_FINDINGS
    assert "error:" in capsys.readouterr().err


def test_cli_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 3
    assert "usage" in capsys.readouterr().out.lower()


def test_cli_is_runnable_as_a_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "calphad_io.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "calphad-io" in result.stdout


# ---------------------------------------------------------------------------
# documented exit-code contract
# ---------------------------------------------------------------------------
#
# The README publishes exit codes 0/1/2/3. Nothing asserted the usage code, so
# the CLI drifted to argparse's default 2 while the README (and the unused
# EXIT_USAGE constant) said 3. These run the real CLI in a subprocess and assert
# the real process exit code, so the contract is pinned end to end.


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "calphad_io.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_contract_clean_exits_zero() -> None:
    assert _cli("info", HO_DAT).returncode == 0


def test_contract_refused_conversion_exits_one() -> None:
    result = _cli("convert", HO_DAT, "--to", "tdb")
    assert result.returncode == 1
    assert "error:" in result.stderr


def test_contract_unparseable_exits_two() -> None:
    corrupt = os.path.join(DATA_DIR, "corrupt", "empty.TDB")
    assert _cli("info", corrupt).returncode == 2


def test_contract_bad_usage_exits_three_not_two() -> None:
    """A missing argument must exit 3, as the README documents — not argparse's 2."""
    result = _cli("info")  # no path argument
    assert result.returncode == 3
    assert "usage:" in result.stderr


def test_contract_help_still_exits_zero() -> None:
    assert _cli("--help").returncode == 0
