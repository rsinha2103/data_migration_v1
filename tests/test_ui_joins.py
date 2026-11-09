"""Joining several source tables into one structure, through the web app."""

import html

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.config import load_project
from data_migration_concept.ui.app import create_app

CSV = b"ID,NAME\n0001,Alpha\n0002,Beta\n"
ADDR = b"ID,CITY\n0001,Paris\n0002,Rome\n"
BASE = "/p/p1/o/customer"
PASTED = "ID\tCustomer id\tText\t4\tX\nNAME\tName\tText\t20\tX\nCITY\tCity\tText\t20\t\n"


class PlainTextClient(TestClient):
    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().request(*args, **kwargs)
        if "text/html" in response.headers.get("content-type", ""):
            response._content = html.unescape(response.text).encode("utf-8")
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
    c.post(f"{BASE}/sources/upload", files={"file": ("addresses.csv", ADDR, "text/csv")})
    page = c.post(f"{BASE}/structures/template", data={"pasted": PASTED, "structure_name": "g"})
    c.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "g", "table_0": "g_t", "source_0": "customers"},
    )
    return c


def join_form(**extra):
    data = {"source": "addresses", "type": "left", "main0": "ID", "other0": "ID"}
    data.update(extra)
    return data


def test_join_can_be_added_used_and_removed(client, tmp_path):
    r = client.post(f"{BASE}/structures/g/joins", data=join_form())
    assert "Source joined" in r.text
    page = client.get(f"{BASE}/structures/g")
    assert "Joined sources" in page.text and "ID = ID" in page.text

    project = load_project(tmp_path / "ws" / "p1")
    st = project.objects["customer"].config.structures[0]
    assert [j.source for j in st.joins] == ["addresses"]

    # the joined column is usable in a rule and survives validation
    form = {
        "r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID", "r0_dtype": "string",
        "r1_target": "NAME", "r1_rule": "MOVE", "r1_src": "NAME", "r1_dtype": "string",
        "r2_target": "CITY", "r2_rule": "MOVE", "r2_src": "addresses.CITY", "r2_dtype": "string",
    }  # fmt: skip
    assert "Mapping saved" in client.post(f"{BASE}/structures/g", data=form).text

    r = client.post(f"{BASE}/structures/g/joins/remove", data={"source": "addresses"})
    assert "Join removed" in r.text
    project = load_project(tmp_path / "ws" / "p1")
    mapping = next(iter(project.objects["customer"].mappings.values()))
    city = next(f for f in mapping.fields if f.target_field == "CITY")
    assert city.rule.type == "UNMAPPED"
    assert not project.objects["customer"].config.structures[0].joins


def test_join_needs_key_columns(client):
    r = client.post(f"{BASE}/structures/g/joins", data=join_form(main0="", other0=""))
    assert "Join not added" in r.text and "key columns" in r.text


def test_join_with_the_structures_own_source_is_refused(client):
    r = client.post(f"{BASE}/structures/g/joins", data=join_form(source="customers"))
    assert "Join not added" in r.text
