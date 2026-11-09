"""Preview of a mapping's result before the transform runs."""

import html
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.ui.app import create_app

CSV = b"ID,NAME,COUNTRY\n0001,Alpha,UK\n0002,,DE\n0003,Gamma Company Ltd,Atlantis\n"
ADDR = b"ID,CITY\n0001,Paris\n0002,Rome\n"
BASE = "/p/p1/o/customer"
PASTED = "ID\tCustomer id\tText\t4\tX\nNAME\tName\tText\t10\tX\nCTRY\tCountry\tText\t2\t\nCITY\tCity\tText\t20\t\n"


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
    c.post(
        f"{BASE}/structures/g/joins", data={"source": "addresses", "main0": "ID", "other0": "ID"}
    )
    c.post(f"{BASE}/xref/new", data={"name": "iso", "content": "UK,GB\nDE,DE\n"})
    c.post(
        f"{BASE}/structures/g",
        data={
            "r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID", "r0_dtype": "string",
            "r1_target": "NAME", "r1_rule": "MOVE", "r1_src": "NAME", "r1_dtype": "string",
            "r1_mandatory": "on", "r1_length": "10",
            "r2_target": "CTRY", "r2_rule": "XREF", "r2_src": "COUNTRY", "r2_a1": "iso",
            "r2_a2": "blank", "r2_dtype": "string",
            "r3_target": "CITY", "r3_rule": "MOVE", "r3_src": "addresses.CITY", "r3_dtype": "string",
        },
    )  # fmt: skip
    return c


def test_preview_before_extract_says_what_to_do(client):
    page = client.get(f"{BASE}/structures/g/preview")
    assert "No preview yet" in page.text and "run Extract first" in page.text


def test_preview_shows_results_next_to_source_values(client, tmp_path):
    client.post(f"{BASE}/run/extract")
    page = client.get(f"{BASE}/structures/g/preview")
    assert "Preview: g" in page.text
    assert "Paris" in page.text and "Rome" in page.text  # from the joined source
    assert "GB" in page.text  # through the XREF
    assert "from: UK" in page.text  # the source value under the result
    assert "3 source row(s) in total" in page.text
    # nothing was built
    assert not list((tmp_path / "ws" / "p1").rglob("build/*.sql"))
    with closing(sqlite3.connect(tmp_path / "ws" / "p1" / "work.db")) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    assert "tgt_g_t" not in tables and "stg_customer_customers" in tables


def test_preview_flags_problems_and_unmapped_values(client):
    client.post(f"{BASE}/run/extract")
    page = client.get(f"{BASE}/structures/g/preview").text
    assert "mandatory, no value" in page  # row 2 has no name
    assert "too long" in page  # 'Gamma Company Ltd' exceeds 10
    assert "Atlantis (1x)" in page and "stays empty" in page


def test_preview_never_multiplies_rows_on_a_repeated_join_key(client, tmp_path):
    client.post(
        f"{BASE}/sources/upload",
        files={"file": ("addresses.csv", ADDR + b"0001,Lyon\n", "text/csv")},
    )
    client.post(f"{BASE}/run/extract")
    page = client.get(f"{BASE}/structures/g/preview").text
    assert "would multiply rows" in page
