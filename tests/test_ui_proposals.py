"""Proposals through the web app: always visible, never applied unless accepted."""

import html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.config import load_project
from data_migration_concept.proposals import Proposal
from data_migration_concept.ui import proposals_service
from data_migration_concept.ui.app import create_app

CSV = (
    b"ID,NAME,COUNTRY,AMOUNT\n"
    b"0001,Alpha Ltd,UK,10.5\n0002,Beta GmbH,DE,20\n0003,Gamma SA,Atlantis,30.25\n"
)
BASE = "/p/p1/o/customer"
PASTED = "ID\tCustomer id\tText\t4\tX\nNAME\tName\tText\t20\tX\nCOUNTRY\tCountry\tText\t2\t\nAMOUNT\tAmount\tDEC\t10\t\n"


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
    r = c.post(
        "/p/p1/objects/add",
        data={"name": "customer"},
        files={"file": ("customers.csv", CSV, "text/csv")},
    )
    assert "Object created" in r.text
    return c


@pytest.fixture
def folder(tmp_path) -> Path:
    return tmp_path / "ws" / "p1"


def define_structure(client, name="general"):
    """Define the target by pasting a field list, as a user would; every field is unmapped."""
    page = client.post(
        f"{BASE}/structures/template", data={"pasted": PASTED, "structure_name": name}
    )
    done = client.post(
        page.url.path,
        data={"inc_0": "on", "name_0": name, "table_0": f"{name}_t", "source_0": "customers"},
    )
    assert "target structure(s) created" in done.text


def snapshot(folder: Path) -> dict[str, bytes]:
    skip = {"work.db"}
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name not in skip and "reports" not in p.parts
    }


def ids_by_title(page_text: str) -> dict[str, str]:
    """Titles of the proposals on offer (not the dismissed ones listed at the end)."""
    page_text = page_text.split("Dismissed (")[0]
    found = re.findall(r'name="ids" value="(\w{12})".*?<b>(.*?)</b>', page_text, re.S)
    return {title.strip(): pid for pid, title in found}


def apply(client, *ids):
    return client.post(f"{BASE}/proposals/apply", data={"ids": list(ids)})


# --- proposals are there from the start, and reading them changes nothing --------------


def test_proposals_are_ready_as_soon_as_the_structure_exists_and_nothing_is_applied(client, folder):
    define_structure(client)
    before = snapshot(folder)
    page = client.get(f"{BASE}/proposals")
    assert page.status_code == 200
    assert "nothing here has been applied" in page.text.lower()
    titles = ids_by_title(page.text)
    assert "Fill ID from ID" in titles and "Fill NAME from NAME" in titles
    assert "Write general to output/general.csv" in titles
    assert any(t.startswith("general: the target has as many rows") for t in titles)
    assert "Run Extract to get proposals based on your data" in page.text
    assert snapshot(folder) == before  # looking, even repeatedly, writes nothing
    client.get(f"{BASE}/proposals")
    assert snapshot(folder) == before
    obj = load_project(folder).objects["customer"]
    assert obj.checks == [] and obj.recons == [] and obj.config.loads == []
    assert all(type(f.rule).__name__ == "UnmappedRule" for f in obj.mappings["general"].fields)


def test_other_pages_point_to_the_proposals(client):
    define_structure(client)
    for path in ("checks", "recon", "xref", "loads"):
        assert "/proposals" in client.get(f"{BASE}/{path}").text
    assert "Proposals" in client.get(BASE).text


def test_proposals_need_a_target_structure(client):
    page = client.get(f"{BASE}/proposals")
    assert "Define one first" in page.text and "Fill " not in page.text


# --- accepting ---------------------------------------------------------------------------


def test_only_the_selected_proposals_are_applied(client, folder):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    done = apply(client, ids["Fill ID from ID"], ids["Fill NAME from NAME"])
    assert "2 proposal(s) applied" in done.text and "applied: Fill ID from ID" in done.text
    obj = load_project(folder).objects["customer"]
    rules = {f.target_field: f.rule.type for f in obj.mappings["general"].fields}
    assert rules == {"ID": "MOVE", "NAME": "MOVE", "COUNTRY": "UNMAPPED", "AMOUNT": "UNMAPPED"}
    assert obj.checks == [] and obj.recons == [] and obj.config.loads == []
    # applied proposals are not offered again; the others still are
    again = ids_by_title(client.get(f"{BASE}/proposals").text)
    assert "Fill ID from ID" not in again and "Fill COUNTRY from COUNTRY" in again


def test_value_list_from_your_data_is_applied_together_with_the_rule_switch(client, folder):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    apply(client, *(ids[t] for t in ids if t.startswith("Fill ")))
    client.post(f"{BASE}/run/extract")

    page = client.get(f"{BASE}/proposals")
    titles = ids_by_title(page.text)
    xref_title = "Translate general.COUNTRY through a country list"
    assert xref_title in titles
    assert "Not recognised, so left out: Atlantis" in page.text
    assert not (folder / "objects" / "customer" / "xref").exists()  # still only a proposal

    done = apply(client, titles[xref_title])
    assert "1 proposal(s) applied" in done.text
    xref = (folder / "objects" / "customer" / "xref" / "country.csv").read_text(encoding="utf-8")
    assert xref.splitlines() == ["legacy_value,target_value", "DE,DE", "UK,GB"]
    mapping = load_project(folder).objects["customer"].mappings["general"]
    country = next(f for f in mapping.fields if f.target_field == "COUNTRY")
    assert country.rule.type == "XREF" and country.rule.on_unmapped == "error"

    # the value that was not recognised is not hidden: the transform stops and names it
    out = client.post(f"{BASE}/run/transform")
    assert "no entry in xref 'country'" in out.text and "Atlantis" in out.text


def test_checks_reconciliations_and_loads_can_be_accepted_and_then_run(client, folder):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    apply(client, *(ids[t] for t in ids if t.startswith("Fill ")))
    client.post(f"{BASE}/run/extract")
    page = client.get(f"{BASE}/proposals")
    titles = ids_by_title(page.text)
    wanted = [
        t for t in titles
        if t.startswith("Write general") or "as many rows" in t or t.startswith("COUNTRY:")
    ]  # fmt: skip
    assert len(wanted) >= 3
    done = apply(client, *(titles[t] for t in wanted))
    assert "proposal(s) applied" in done.text and "refused:" not in done.text
    obj = load_project(folder).objects["customer"]
    assert [c.type for c in obj.checks] == ["PATTERN"] and obj.checks[0].field == "COUNTRY"
    assert [r.type for r in obj.recons] == ["COUNT"]
    assert obj.config.loads[0].path == "output/general.csv"
    assert "OK - extract" in client.post(f"{BASE}/run/extract").text
    assert "OK - transform" in client.post(f"{BASE}/run/transform").text


# --- dismissing --------------------------------------------------------------------------


def test_dismissed_proposals_are_hidden_remembered_and_can_come_back(client, folder):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    keep, drop = ids["Fill ID from ID"], ids["Fill NAME from NAME"]
    gone = client.post(f"{BASE}/proposals/dismiss", data={"ids": [drop]})
    assert "1 proposal(s) dismissed" in gone.text
    after = ids_by_title(client.get(f"{BASE}/proposals").text)
    assert "Fill NAME from NAME" not in after
    visible = client.get(f"{BASE}/proposals").text
    assert "Dismissed (1)" in visible and drop in visible  # listed only under Dismissed
    assert (folder / "objects" / "customer" / "proposals_dismissed.json").is_file()
    assert keep in after.values()

    back = client.post(f"{BASE}/proposals/restore", data={"ids": [drop]})
    assert "1 proposal(s) brought back" in back.text
    assert "Dismissed (" not in client.get(f"{BASE}/proposals").text
    assert not (folder / "objects" / "customer" / "proposals_dismissed.json").exists()


def test_dismissing_does_not_touch_the_project_configuration(client, folder):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    config_before = {k: v for k, v in snapshot(folder).items() if not k.endswith(".json")}
    client.post(f"{BASE}/proposals/dismiss", data={"ids": list(ids.values())})
    config_after = {k: v for k, v in snapshot(folder).items() if not k.endswith(".json")}
    assert config_before == config_after
    assert "No proposals right now" in client.get(f"{BASE}/proposals").text


# --- what the server accepts -------------------------------------------------------------


def test_the_server_applies_what_it_proposes_not_what_a_form_claims(client, folder):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    sneaky = client.post(
        f"{BASE}/proposals/apply",
        data={
            "ids": [ids["Fill ID from ID"]],
            "payload": '{"field": "NAME", "rule": {"type": "CONSTANT", "value": "x"}}',
            "field": "NAME", "type": "CONSTANT",
        },
    )  # fmt: skip
    assert "1 proposal(s) applied" in sneaky.text
    rules = {
        f.target_field: f.rule.type
        for f in load_project(folder).objects["customer"].mappings["general"].fields
    }
    assert rules["ID"] == "MOVE" and rules["NAME"] == "UNMAPPED"


def test_unknown_or_stale_ids_and_empty_selections_are_refused(client):
    define_structure(client)
    assert "Nothing was selected" in client.post(f"{BASE}/proposals/apply", data={}).text
    assert "no longer available" in apply(client, "deadbeef0000").text
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    apply(client, ids["Fill ID from ID"])
    assert "no longer available" in apply(client, ids["Fill ID from ID"]).text  # already applied
    assert "Nothing was selected" in client.post(f"{BASE}/proposals/dismiss", data={}).text


def test_a_proposal_that_would_break_the_project_is_refused_and_changes_nothing(client, folder):
    define_structure(client)
    before = snapshot(folder)
    broken = Proposal(
        "x" * 12, "check", "general", "Bad check", "why", "high", "library", "UNIQUE",
        {"entries": [{"name": "bad_unique", "structure": "general", "type": "UNIQUE", "fields": ["NO_SUCH_FIELD"]}]},
    )  # fmt: skip
    result = proposals_service.apply_proposals(folder, "customer", [broken])
    assert result.applied == [] and len(result.failed) == 1
    assert "NO_SUCH_FIELD" in " ".join(result.failed[0][1])
    assert snapshot(folder) == before


def test_applying_a_list_that_already_exists_is_refused(client, folder):
    define_structure(client)
    clash = Proposal(
        "y" * 12, "xref", "general", "Country list", "why", "high", "library", "list",
        {"xref": "country", "rows": [("DE", "DE")], "fields": []},
    )  # fmt: skip
    client.post(f"{BASE}/xref/new", data={"name": "country", "content": "DE,DE\n"})
    result = proposals_service.apply_proposals(folder, "customer", [clash])
    assert result.applied == [] and "already exists" in " ".join(result.failed[0][1])


# --- a proposed check the data already breaks must not block the load -------------------


def map_and_extract(client):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    apply(client, *(ids[t] for t in ids if t.startswith("Fill ")))
    client.post(f"{BASE}/run/extract")


def test_a_proposed_check_that_would_fail_today_is_offered_as_a_warning_and_does_not_block(
    client, folder
):
    data = folder / "objects" / "customer" / "data" / "customers.csv"
    data.write_text(data.read_text(encoding="utf-8").replace("Atlantis", "uk"), encoding="utf-8")
    map_and_extract(client)
    page = client.get(f"{BASE}/proposals")
    assert "Heads-up: with your current data COUNTRY would flag 1 of 3 rows" in page.text
    assert "(e.g. uk)" in page.text  # the offending value is named before anything is applied
    titles = ids_by_title(page.text)
    title = next(t for t in titles if t.startswith("COUNTRY: Country codes"))
    done = apply(client, titles[title])
    assert "proposal(s) applied. Re-run Transform, then Check" in done.text
    check = load_project(folder).objects["customer"].checks[0]
    assert check.severity == "warn" and check.field == "COUNTRY"

    assert "OK - transform" in client.post(f"{BASE}/run/transform").text
    out = client.post(f"{BASE}/run/check")
    assert "BLOCKING" not in out.text and "checks passed" in out.text
    assert "PROBLEM" not in out.text  # the run is not reported as a failure
    results = client.get(f"{BASE}/results")
    assert "WARN" in results.text and "1 row(s) failed (e.g. uk)" in results.text


def test_the_check_step_says_why_a_mandatory_field_blocks(client):
    define_structure(client)
    ids = ids_by_title(client.get(f"{BASE}/proposals").text)
    apply(client, ids["Fill ID from ID"])  # NAME is mandatory but still not mapped
    client.post(f"{BASE}/run/extract")
    client.post(f"{BASE}/run/transform")
    client.post(f"{BASE}/checks/from-mapping", data={"mandatory": "error"})  # confirmed by the user
    out = client.post(f"{BASE}/run/check")
    assert (
        "BLOCKING: mandatory.general.NAME - 3 row(s) failed - the field is mandatory but not mapped yet"
        in out.text
    )


def test_mandatory_fields_without_a_mapping_are_listed_before_anything_is_run(client):
    define_structure(client)
    page = client.get(f"{BASE}/proposals")
    assert "Mandatory fields that are still not mapped" in page.text
    assert "general</a>: ID, NAME" in page.text
    ids = ids_by_title(page.text)
    apply(client, ids["Fill ID from ID"], ids["Fill NAME from NAME"])
    assert "Mandatory fields that are still not mapped" not in client.get(f"{BASE}/proposals").text
