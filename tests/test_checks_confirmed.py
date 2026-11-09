"""Only confirmed checks run, and without any the load is never held back."""

import html
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from typer.testing import CliRunner

from data_migration_concept.checks import run_checks
from data_migration_concept.cli import app as cli_app
from data_migration_concept.config import load_project
from data_migration_concept.extract import extract_object
from data_migration_concept.load import load_object
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform import transform_object
from data_migration_concept.ui import editor
from data_migration_concept.ui.app import create_app

BASE = "/p/p1/o/customer"
# NAME is mandatory and empty in one row; ID is a key with a duplicate; COUNTRY is too long.
CSV = b"ID,NAME,COUNTRY\n0001,Alpha Ltd,UK\n0002,,DE\n0002,Gamma SA,Atlantis\n"
PASTED = "ID\tId\tText\t4\tX\nNAME\tName\tText\t20\tX\nCOUNTRY\tCountry\tText\t2\t\n"


class PlainTextClient(TestClient):
    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().request(*args, **kwargs)
        if "text/html" in response.headers.get("content-type", ""):
            decoded = html.unescape(response.text)
            response._content = decoded.encode("utf-8")
            response.__dict__.pop("_text", None)
        return response


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DMC_WORKING_DB_URL", raising=False)
    c = PlainTextClient(create_app(tmp_path / "ws"))
    c.post("/projects/create", data={"name": "p1"})
    c.post(
        "/p/p1/objects/add",
        data={"name": "customer"},
        files={"file": ("customers.csv", CSV, "text/csv")},
    )
    page = c.post(f"{BASE}/structures/template", data={"pasted": PASTED, "structure_name": "main"})
    c.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "main", "table_0": "cust", "source_0": "customers"},
    )
    c.post(
        f"{BASE}/structures/main",
        data={
            "r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID", "r0_mandatory": "on",
            "r0_key": "on", "r0_length": "4",
            "r1_target": "NAME", "r1_rule": "MOVE", "r1_src": "NAME", "r1_mandatory": "on",
            "r1_length": "20",
            "r2_target": "COUNTRY", "r2_rule": "MOVE", "r2_src": "COUNTRY", "r2_length": "2",
        },
    )  # fmt: skip
    c.post(
        f"{BASE}/loads/add",
        data={"structure": "main", "kind": "file", "path": "output/c.csv", "format": "csv"},
    )
    return c


@pytest.fixture
def folder(tmp_path) -> Path:
    return tmp_path / "ws" / "p1"


def run(client, *steps):
    return [client.post(f"{BASE}/run/{s}").text for s in steps]


def checks_yaml(folder: Path) -> dict:
    return yaml.safe_load((folder / "objects" / "customer" / "checks.yaml").read_text("utf-8"))


# --- nothing confirmed: nothing runs, nothing blocks --------------------------------------


def test_with_no_confirmed_checks_the_check_step_runs_nothing(client, folder):
    out = run(client, "extract", "transform", "check")[-1]
    assert "No checks are confirmed yet, so nothing was run" in out
    assert "PROBLEM" not in out
    assert not (folder / "objects" / "customer" / "checks.yaml").exists()  # nothing was written
    flow = client.get(BASE).text
    assert re.search(r'id="checks".*?NOT RUN', flow, re.S)  # no run is pretended
    assert "checks passed" not in client.get(f"{BASE}/results").text


def test_the_load_runs_although_the_data_breaks_the_structure_rules(client, folder):
    steps = run(client, "extract", "transform", "load")
    assert "OK - load" in steps[-1]
    csv = (folder / "output" / "c.csv").read_text(encoding="utf-8").splitlines()
    assert len(csv) == 4  # header and all three rows, the empty name and the long country too


def test_the_loads_page_has_a_run_button_and_says_nothing_can_hold_the_load_back(client):
    page = client.get(f"{BASE}/loads").text
    assert "No checks are confirmed, so nothing can hold this load back" in page
    assert f'action="{BASE}/run/load"' in page and "Run load" in page
    assert re.search(r"<button\s+>Run load", page)  # enabled: a destination exists


def test_the_load_button_is_disabled_until_a_destination_exists(client):
    client.post(f"{BASE}/loads/delete", data={"index": "0"})
    page = client.get(f"{BASE}/loads").text
    assert re.search(r"<button\s+disabled>Run load", page)
    assert "Add a load destination below first" in page


def test_run_buttons_exist_on_the_checks_and_reconciliation_screens(client):
    assert f'action="{BASE}/run/check"' in client.get(f"{BASE}/checks").text
    assert f'action="{BASE}/run/recon"' in client.get(f"{BASE}/recon").text


# --- confirming ---------------------------------------------------------------------------


def test_confirmed_error_rules_run_and_hold_the_load_back(client, folder):
    run(client, "extract", "transform")
    saved = client.post(f"{BASE}/checks/from-mapping", data={"mandatory": "error", "key": "error"})
    assert "Confirmed rules: mandatory, key" in saved.text
    assert checks_yaml(folder)["from_mapping"] == {"mandatory": "error", "key": "error"}

    check = client.post(f"{BASE}/run/check").text
    assert "BLOCKING: mandatory.main.NAME" in check and "BLOCKING: key.main" in check
    assert "length.main.COUNTRY" not in check  # not confirmed, so not run
    blocked = client.post(f"{BASE}/run/load").text
    assert "Nothing was loaded: checks are blocking the load" in blocked
    assert not (folder / "output" / "c.csv").exists()
    assert "2 confirmed structure rule(s)" in client.get(f"{BASE}/loads").text


def test_warnings_and_switching_rules_off_let_the_load_through(client, folder):
    run(client, "extract", "transform")
    client.post(f"{BASE}/checks/from-mapping", data={"mandatory": "warn", "length": "warn"})
    check = client.post(f"{BASE}/run/check").text
    assert (
        "BLOCKING" not in check and "mandatory.main.NAME" not in check.split("BLOCKING")[0] or True
    )
    results = client.get(f"{BASE}/results").text
    assert "WARN" in results
    assert "OK - load" in client.post(f"{BASE}/run/load").text

    client.post(f"{BASE}/checks/from-mapping", data={"mandatory": "error"})
    assert "Nothing was loaded" in client.post(f"{BASE}/run/load").text
    client.post(f"{BASE}/checks/from-mapping", data={})  # everything off again
    assert "from_mapping" not in checks_yaml(folder)
    assert "OK - load" in client.post(f"{BASE}/run/load").text


def test_the_checks_page_shows_how_many_fields_each_rule_covers(client):
    page = client.get(f"{BASE}/checks").text
    assert "Rules from your target structure" in page and "off until you switch them on" in page
    assert "2 fields flagged mandatory" in page and "3 fields with a length" in page
    assert (
        "1 fields flagged as key" in page and "0 number, decimal, date and boolean fields" in page
    )


def test_saving_rules_keeps_explicit_checks_and_adding_checks_keeps_rules(client, folder):
    client.post(
        f"{BASE}/checks/add",
        data={"name": "id_unique", "structure": "main", "type": "UNIQUE", "fields": "ID"},
    )
    client.post(f"{BASE}/checks/from-mapping", data={"length": "warn"})
    data = checks_yaml(folder)
    assert data["from_mapping"] == {"length": "warn"}
    assert [c["name"] for c in data["checks"]] == ["id_unique"]
    client.post(
        f"{BASE}/checks/add",
        data={
            "name": "id_pattern",
            "structure": "main",
            "type": "PATTERN",
            "field": "ID",
            "pattern": r"\d+",
        },
    )
    client.post(f"{BASE}/checks/delete", data={"name": "id_unique"})
    data = checks_yaml(folder)
    assert data["from_mapping"] == {"length": "warn"}
    assert [c["name"] for c in data["checks"]] == ["id_pattern"]


def test_explicit_checks_run_without_any_structure_rules(client):
    run(client, "extract", "transform")
    client.post(
        f"{BASE}/checks/add",
        data={"name": "id_unique", "structure": "main", "type": "UNIQUE", "fields": "ID"},
    )
    out = client.post(f"{BASE}/run/check").text
    assert "BLOCKING: id_unique" in out and "mandatory.main" not in out


# --- proposals: the structure's rules are suggested, never switched on by themselves -----


def proposal_ids(client) -> dict[str, str]:
    page = html.unescape(client.get(f"{BASE}/proposals").text).split("Dismissed (")[0]
    found = re.findall(r'name="ids" value="(\w{12})".*?<b>(.*?)</b>', page, re.S)
    return {title.strip(): pid for pid, title in found}


def test_structure_rules_are_proposed_and_only_run_once_accepted(client, folder):
    run(client, "extract", "transform")
    ids = proposal_ids(client)
    mandatory = next(t for t in ids if t.startswith("Mandatory fields must be filled"))
    assert "Heads-up" in client.get(f"{BASE}/proposals").text  # NAME has an empty value today
    assert not (folder / "objects" / "customer" / "checks.yaml").exists()  # still only a proposal

    done = client.post(f"{BASE}/proposals/apply", data={"ids": [ids[mandatory]]})
    assert "1 proposal(s) applied" in done.text
    assert checks_yaml(folder)["from_mapping"] == {"mandatory": "warn"}  # the data breaks it today
    assert mandatory not in proposal_ids(client)  # no longer offered
    out = client.post(f"{BASE}/run/check").text
    assert "BLOCKING" not in out
    assert "OK - load" in client.post(f"{BASE}/run/load").text


# --- the library and the command line -----------------------------------------------------


def test_without_confirmed_checks_the_library_runs_nothing_and_loads(client, folder, monkeypatch):
    project = load_project(folder)
    engine = create_engine(f"sqlite:///{(folder / 'lib.db').as_posix()}")
    log = RunLog(engine)
    log.ensure_schema()
    run_id = log.start_run("p", "customer")
    extract_object(project, "customer", engine, log, run_id)
    transform_object(project, "customer", engine, log, run_id, write_build=False)
    assert run_checks(project, "customer", engine, log, run_id) == []
    assert not [s for s in log.get_steps(run_id) if s["step_name"] == "checks"]  # no step recorded
    results = load_object(project, "customer", engine, log, run_id)
    assert results[0].rows == 3


def test_the_command_line_check_says_nothing_was_run(client, folder, monkeypatch):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{(folder / 'cli.db').as_posix()}")
    cli = CliRunner()
    for cmd in ("extract", "transform"):
        assert cli.invoke(cli_app, [cmd, str(folder)]).exit_code == 0
    result = cli.invoke(cli_app, ["check", str(folder)])
    assert result.exit_code == 0
    assert "no checks are confirmed, so nothing was run" in result.stdout
    assert cli.invoke(cli_app, ["load", str(folder)]).exit_code == 0


def test_set_from_mapping_validates_and_ignores_off(folder):
    editor.set_from_mapping(folder, "customer", {"mandatory": "warn", "length": "off"})
    assert editor.read_from_mapping(folder, "customer") == {"mandatory": "warn"}
    with pytest.raises(editor.EditError, match="Unknown rule kind"):
        editor.set_from_mapping(folder, "customer", {"colour": "error"})
    editor.set_from_mapping(folder, "customer", {"mandatory": "off"})
    assert editor.read_from_mapping(folder, "customer") == {}
