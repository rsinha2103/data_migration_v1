"""The 'define the target structure first' screen, through the web app."""

import html
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.config import load_project
from data_migration_concept.ui.app import create_app
from target_files import cockpit_xml, cockpit_zip

CSV = b"ID,NAME,COUNTRY\n0001,Alpha Ltd,UK\n0002,Beta GmbH,DE\n0003,Gamma SA,FR\n"

CUSTOMER_ROWS = [
    ["", "Customer data (mandatory)"],
    ["", "", "Key", "Customer id", "mandatory for sheet", "Text", "4", "", "S_CUST", "ID"],
    ["", "", "", "Name", "mandatory for sheet", "Text", "20", "", "S_CUST", "NAME"],
    ["", "", "", "Country", "", "Text", "2", "", "S_CUST", "COUNTRY"],
    ["", "", "", "Extra", "", "Text", "10", "", "S_CUST", "EXTRA"],
    ["", "Other data"],
    ["", "", "", "Other", "", "Text", "1", "", "S_OTHER", "X"],
]
BASE = "/p/p1/o/customer"


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


def structures_of(tmp_path: Path):
    return load_project(tmp_path / "ws" / "p1").objects["customer"]


def upload(client, filename, content, mime="application/octet-stream"):
    return client.post(f"{BASE}/structures/template", files={"file": (filename, content, mime)})


def create_cockpit_structure(client):
    page = upload(client, "template.xml", cockpit_xml(CUSTOMER_ROWS))
    assert "2 structure(s) found" in page.text
    return client.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "S_CUST", "table_0": "s_cust", "source_0": "customers"},
    )


def unmapped_form(**overrides):
    fields = [("ID", "4", True, True), ("NAME", "20", True, False), ("COUNTRY", "2", False, False),
              ("EXTRA", "10", False, False)]  # fmt: skip
    form = {}
    for i, (name, length, mandatory, key) in enumerate(fields):
        form[f"r{i}_target"] = name
        form[f"r{i}_rule"] = "UNMAPPED"
        form[f"r{i}_length"] = length
        if mandatory or overrides.get(f"mandatory_{name}"):
            form[f"r{i}_mandatory"] = "on"
        if key:
            form[f"r{i}_key"] = "on"
    return form


# --- creating structures ----------------------------------------------------------------


def test_structures_page_offers_the_three_ways_in(client):
    page = client.get(f"{BASE}/structures")
    assert "No target structure yet" in page.text
    for text in ("A. Upload a template file", "B. Type or paste the fields", "C. Copy the source"):
        assert text in page.text
    assert "/templates/target-structure.xlsx" in page.text


def test_cockpit_xml_preview_then_create_only_what_you_tick(client, tmp_path):
    page = upload(client, "template.xml", cockpit_xml(CUSTOMER_ROWS))
    assert page.status_code == 200
    assert "SAP Migration Cockpit Excel XML template" in page.text
    assert "2 structure(s) found" in page.text and "mandatory structure" in page.text
    assert "S_CUST" in page.text and "S_OTHER" in page.text

    done = client.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "S_CUST", "table_0": "s_cust", "source_0": "customers"},
    )
    assert "1 target structure(s) created" in done.text
    obj = structures_of(tmp_path)
    assert [s.name for s in obj.config.structures] == ["S_CUST"]
    fields = {f.target_field: f for f in obj.mappings["S_CUST"].fields}
    assert list(fields) == ["ID", "NAME", "COUNTRY", "EXTRA"]
    assert fields["ID"].key and fields["ID"].mandatory and fields["ID"].length == 4
    assert all(type(f.rule).__name__ == "UnmappedRule" for f in fields.values())

    listing = client.get(f"{BASE}/structures")
    assert "0 of 4" in listing.text  # nothing mapped yet
    mapping = client.get(f"{BASE}/structures/S_CUST")
    assert "not mapped" in mapping.text and "0 of 4" in mapping.text
    assert "2 mandatory field(s) not mapped" in mapping.text


def test_zip_of_csv_templates_works_too(client, tmp_path):
    zipped = cockpit_zip({"Pkg/S_CUST#FreeText_Mandatory.csv": "ID(k/*),NAME(*),COUNTRY\n"})
    page = upload(client, "templates.zip", zipped)
    assert (
        "SAP Migration Cockpit CSV templates" in page.text and "1 structure(s) found" in page.text
    )
    client.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "S_CUST", "table_0": "s_cust", "source_0": "customers"},
    )
    fields = {f.target_field: f for f in structures_of(tmp_path).mappings["S_CUST"].fields}
    assert fields["ID"].key and fields["ID"].mandatory and fields["NAME"].mandatory
    assert not fields["COUNTRY"].mandatory and not fields["COUNTRY"].key


def test_pasted_field_list_without_a_file(client, tmp_path):
    pasted = "ID\tCustomer id\tText\t4\tX\nNAME\tName\tText\t20\tX\nBORN\tBorn on\tDATS\t8\t\n"
    page = client.post(
        f"{BASE}/structures/template", data={"pasted": pasted, "structure_name": "people"}
    )
    assert "1 structure(s) found" in page.text and "people" in page.text
    client.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "people", "table_0": "people_t", "source_0": "customers"},
    )
    fields = {f.target_field: f for f in structures_of(tmp_path).mappings["people"].fields}
    assert fields["BORN"].data_type == "date" and fields["ID"].mandatory


def test_plain_csv_with_column_picker(client, tmp_path):
    content = b"Technical name,Meaning,Max length,Required\nID,Customer,4,X\nNAME,Name,20,\n"
    page = upload(client, "mine.csv", content, "text/csv")
    assert "Which column is which?" in page.text and "Technical name" in page.text
    assert "1 structure(s) found" in page.text
    # the user changes the picker: 'Meaning' is the field name instead
    again = client.get(
        page.url.path, params={"role_field": "1", "role_length": "2", "structure_name": "mine"}
    )
    assert "Customer" in again.text
    client.post(
        page.url.path,
        data={
            "role_field": "1", "role_length": "2", "structure_name": "mine",
            "inc_0": "on", "name_0": "mine", "table_0": "mine_t", "source_0": "customers",
        },
    )  # fmt: skip
    fields = {f.target_field: f for f in structures_of(tmp_path).mappings["mine"].fields}
    assert list(fields) == ["Customer", "Name"] and fields["Customer"].length == 4


def test_blank_templates_can_be_downloaded_filled_in_and_uploaded(client):
    xlsx = client.get("/templates/target-structure.xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    assert "attachment" in xlsx.headers["content-disposition"]
    csv = client.get("/templates/target-structure.csv")
    assert csv.text.splitlines()[0] == "Structure,Field,Description,Data type,Length,Mandatory,Key"
    assert client.get("/templates/target-structure.pdf").status_code == 404
    page = upload(client, "template.xlsx", xlsx.content)
    assert "EXAMPLE_general" in page.text and "1 structure(s) found" in page.text


def test_copy_source_columns_still_works(client, tmp_path):
    client.post(
        f"{BASE}/structures/add",
        data={"name": "main", "target_table": "cust_main", "source": "customers"},
    )
    fields = structures_of(tmp_path).mappings["main"].fields
    assert [f.target_field for f in fields] == ["ID", "NAME", "COUNTRY"]
    assert all(type(f.rule).__name__ == "MoveRule" for f in fields)


# --- problems ---------------------------------------------------------------------------


def test_bad_files_and_expired_previews_are_explained(client):
    bad = upload(client, "template.exe", b"MZ")
    assert "Could not read the target structure" in bad.text and "Upload a .csv" in bad.text
    nothing = client.post(f"{BASE}/structures/template", data={"pasted": ""})
    assert "Choose a file or paste" in nothing.text
    gone = client.get(f"{BASE}/structures/template/not-a-real-token")
    assert "expired" in gone.text
    broken = upload(client, "template.xml", b"<Workbook><oops")
    assert "could not be read" in broken.text


def test_name_clashes_are_flagged_and_nothing_is_half_created(client, tmp_path):
    create_cockpit_structure(client)
    page = upload(client, "template.xml", cockpit_xml(CUSTOMER_ROWS))
    assert "name already used" in page.text
    assert 'name="inc_0" checked' not in page.text  # not pre-ticked
    both = client.post(
        page.url.path,
        data={
            "inc_0": "on", "name_0": "S_CUST", "table_0": "s_cust", "source_0": "customers",
            "inc_1": "on", "name_1": "S_OTHER", "table_1": "s_other", "source_1": "customers",
        },
    )  # fmt: skip
    assert "Nothing was created" in both.text and "duplicate" in both.text
    assert [s.name for s in structures_of(tmp_path).config.structures] == ["S_CUST"]


def test_bad_names_and_unknown_sources_are_refused(client, tmp_path):
    page = upload(client, "template.xml", cockpit_xml(CUSTOMER_ROWS))
    bad_name = client.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "1 bad!", "table_0": "t", "source_0": "customers"},
    )
    assert "names use letters" in bad_name.text
    ghost = client.post(
        page.url.path, data={"inc_0": "on", "name_0": "S_CUST", "table_0": "t", "source_0": "ghost"}
    )
    assert "Nothing was created" in ghost.text and "unknown source" in ghost.text
    none = client.post(page.url.path, data={})
    assert "Nothing was selected" in none.text
    assert structures_of(tmp_path).config.structures == []


# --- mapping afterwards -----------------------------------------------------------------


def test_auto_map_run_and_unmapped_fields_stay_in_the_result(client, tmp_path):
    create_cockpit_structure(client)
    done = client.post(f"{BASE}/structures/S_CUST", data={**unmapped_form(), "action": "auto_map"})
    assert "3 field(s) mapped by matching names" in done.text
    fields = {f.target_field: f for f in structures_of(tmp_path).mappings["S_CUST"].fields}
    assert fields["NAME"].rule.type == "MOVE" and fields["EXTRA"].rule.type == "UNMAPPED"
    assert fields["ID"].key and fields["ID"].mandatory  # flags survive the save

    assert "OK - extract" in client.post(f"{BASE}/run/extract").text
    assert "OK - transform" in client.post(f"{BASE}/run/transform").text
    data = client.get(f"{BASE}/data/tgt_s_cust")
    assert "EXTRA" in data.text and "Alpha Ltd" in data.text  # empty column is part of the result
    # nothing is confirmed, so nothing is checked
    assert "No checks are confirmed yet" in client.post(f"{BASE}/run/check").text

    # the same field made mandatory: leaving it unmapped now blocks the load
    mandatory = unmapped_form(mandatory_EXTRA=True)
    for i, name in enumerate(["ID", "NAME", "COUNTRY"]):
        mandatory[f"r{i}_rule"] = "MOVE"
        mandatory[f"r{i}_src"] = name
    client.post(f"{BASE}/structures/S_CUST", data={**mandatory, "action": "save"})
    client.post(f"{BASE}/run/transform")
    client.post(f"{BASE}/checks/from-mapping", data={"mandatory": "error"})  # now confirmed
    out = client.post(f"{BASE}/run/check")
    assert "BLOCKING: mandatory.S_CUST.EXTRA" in out.text


def test_updating_a_structure_from_a_newer_template_keeps_the_mapping(client, tmp_path):
    create_cockpit_structure(client)
    client.post(f"{BASE}/structures/S_CUST", data={**unmapped_form(), "action": "auto_map"})
    newer = cockpit_zip({"Pkg/S_CUST#FreeText.csv": "ID(k/*),NAME(*),COUNTRY(*),EXTRA,NEW_FIELD\n"})
    page = client.post(
        f"{BASE}/structures/S_CUST/template",
        files={"file": ("newer.zip", newer, "application/zip")},
    )
    assert "Update S_CUST from a template" in page.text and "Use which structure" in page.text
    assert 'value="0" checked' in page.text  # matched by name
    done = client.post(page.url.path, data={"pick": "0"})
    assert "1 field(s) added" in done.text and "not in the template" in done.text
    fields = {f.target_field: f for f in structures_of(tmp_path).mappings["S_CUST"].fields}
    assert list(fields) == ["ID", "NAME", "COUNTRY", "EXTRA", "NEW_FIELD"]
    assert fields["NAME"].rule.type == "MOVE"  # the mapping rules are untouched
    assert fields["COUNTRY"].mandatory and fields["NEW_FIELD"].rule.type == "UNMAPPED"


def test_update_with_a_bad_pick_and_a_bad_file(client):
    create_cockpit_structure(client)
    newer = cockpit_zip({"Pkg/S_CUST#FreeText.csv": "ID\n"})
    page = client.post(
        f"{BASE}/structures/S_CUST/template", files={"file": ("n.zip", newer, "application/zip")}
    )
    assert "Choose which structure" in client.post(page.url.path, data={"pick": "7"}).text
    bad = client.post(f"{BASE}/structures/S_CUST/template", files={"file": ("n.exe", b"MZ", "x/y")})
    assert "Could not read the template" in bad.text


def test_change_the_source_of_a_structure(client, tmp_path):
    create_cockpit_structure(client)
    client.post(
        f"{BASE}/sources/upload",
        files={"file": ("regions.csv", b"CODE,REGION\nDE,EU\n", "text/csv")},
    )
    done = client.post(f"{BASE}/structures/S_CUST/source", data={"source": "regions"})
    assert "now reads from that source" in done.text
    assert structures_of(tmp_path).config.structures[0].source == "regions"
    bad = client.post(f"{BASE}/structures/S_CUST/source", data={"source": "ghost"})
    assert "Source not changed" in bad.text
    assert structures_of(tmp_path).config.structures[0].source == "regions"


def test_structure_removal_and_flow_page_with_no_structures(client):
    assert client.get(BASE).status_code == 200  # an object without structures still opens
    create_cockpit_structure(client)
    assert (
        "Structure removed"
        in client.post(f"{BASE}/structures/remove", data={"name": "S_CUST"}).text
    )
