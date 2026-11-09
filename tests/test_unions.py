"""UNION ALL: one structure fed by several row sets (e.g. one vendor row per company code and
one per purchasing organisation), each with its own source, joins and rules."""

import html
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.ui.app import create_app

LFA1 = b"LIFNR,NAME1\n1,Acme\n2,Beta\n3,Gamma\n"
LFB1 = b"LIFNR,BUKRS\n1,1000\n1,2000\n2,1000\n"
LFM1 = b"LIFNR,EKORG\n1,P100\n3,P100\n3,P200\n3,P300\n"
BASE = "/p/p1/o/vendor"
PASTED = "LIFNR\tVendor\tText\t10\tX\nNAME1\tName\tText\t30\t\nBP_ROLE\tRole\tText\t6\tX\nORG\tOrganisation\tText\t4\t\n"


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
        data={"name": "vendor"},
        files={"file": ("lfb1.csv", LFB1, "text/csv")},
    )
    c.post(f"{BASE}/sources/upload", files={"file": ("lfa1.csv", LFA1, "text/csv")})
    c.post(f"{BASE}/sources/upload", files={"file": ("lfm1.csv", LFM1, "text/csv")})
    page = c.post(f"{BASE}/structures/template", data={"pasted": PASTED, "structure_name": "g"})
    c.post(page.url.path, data={"inc_0": "on", "name_0": "g", "table_0": "g_t", "source_0": "lfb1"})
    c.post(
        f"{BASE}/structures/g/joins", data={"source": "lfa1", "main0": "LIFNR", "other0": "LIFNR"}
    )
    c.post(
        f"{BASE}/structures/g",
        data={
            "r0_target": "LIFNR", "r0_rule": "MOVE", "r0_src": "LIFNR", "r0_dtype": "string",
            "r1_target": "NAME1", "r1_rule": "MOVE", "r1_src": "lfa1.NAME1", "r1_dtype": "string",
            "r2_target": "BP_ROLE", "r2_rule": "CONSTANT", "r2_a1": "FLVN00", "r2_dtype": "string",
            "r3_target": "ORG", "r3_rule": "MOVE", "r3_src": "BUKRS", "r3_dtype": "string",
        },
    )  # fmt: skip
    return c


def add_vendor_row_set(client):
    r = client.post(
        f"{BASE}/structures/g/rowsets",
        data={
            "name": "FLVN01", "source": "lfm1", "join_source": "lfa1",
            "main0": "LIFNR", "other0": "LIFNR",
        },
    )  # fmt: skip
    assert "Row set added" in r.text
    for field, rule, src, a1 in [
        ("BP_ROLE", "CONSTANT", "", "FLVN01"),
        ("ORG", "MOVE", "EKORG", ""),
    ]:
        r = client.post(
            f"{BASE}/structures/g/rowsets/override",
            data={"row_set": "FLVN01", "field": field, "rule": rule, "src": src, "a1": a1},
        )
        assert "has its own rule" in r.text, r.text


def target_rows(tmp_path):
    with closing(sqlite3.connect(tmp_path / "ws" / "p1" / "work.db")) as db:
        return db.execute(
            'SELECT LIFNR, NAME1, BP_ROLE, ORG FROM "tgt_g_t" ORDER BY BP_ROLE, LIFNR, ORG'
        ).fetchall()


def test_transform_puts_both_row_sets_in_one_target_table(client, tmp_path):
    add_vendor_row_set(client)
    assert "OK - extract" in client.post(f"{BASE}/run/extract").text
    assert "OK - transform" in client.post(f"{BASE}/run/transform").text
    assert target_rows(tmp_path) == [
        ("1", "Acme", "FLVN00", "1000"),
        ("1", "Acme", "FLVN00", "2000"),
        ("2", "Beta", "FLVN00", "1000"),
        ("1", "Acme", "FLVN01", "P100"),
        ("3", "Gamma", "FLVN01", "P100"),
        ("3", "Gamma", "FLVN01", "P200"),
        ("3", "Gamma", "FLVN01", "P300"),
    ]


def test_generated_sql_is_a_union_all(client, tmp_path):
    add_vendor_row_set(client)
    project = load_project(tmp_path / "ws" / "p1")
    from data_migration_concept.transform import generate_object

    sql = generate_object(project, project.objects["vendor"])[0].render()
    assert sql.count("UNION ALL") == 1 and "'FLVN01'" in sql and "'FLVN00'" in sql


def test_preview_labels_the_row_sets(client):
    add_vendor_row_set(client)
    client.post(f"{BASE}/run/extract")
    page = client.get(f"{BASE}/structures/g/preview").text
    assert "Row set" in page and "FLVN01" in page and "P200" in page


def test_row_count_reconciliation_adds_up_all_row_sets(client, tmp_path):
    add_vendor_row_set(client)
    (tmp_path / "ws" / "p1" / "objects" / "vendor" / "recon.yaml").write_text(
        "recons:\n  - {name: rows, structure: g, type: COUNT}\n", encoding="utf-8"
    )
    client.post(f"{BASE}/run/extract")
    client.post(f"{BASE}/run/transform")
    out = client.post(f"{BASE}/run/recon").text
    assert "OK - recon" in out


def test_a_row_set_source_is_checked_at_extract(client):
    client.post(
        f"{BASE}/structures/g/rowsets",
        data={"name": "bad", "source": "lfm1"},
    )
    client.post(
        f"{BASE}/structures/g/rowsets/override",
        data={"row_set": "bad", "field": "ORG", "rule": "MOVE", "src": "NOPE"},
    )
    out = client.post(f"{BASE}/run/extract").text
    assert "row set 'bad'" in out and "NOPE" in out


def test_overrides_must_name_a_real_field_and_agree_on_not_migrated(client):
    client.post(f"{BASE}/structures/g/rowsets", data={"name": "x", "source": "lfm1"})
    r = client.post(
        f"{BASE}/structures/g/rowsets/override",
        data={"row_set": "x", "field": "ORG", "rule": "NOT_MIGRATED"},
    )
    assert "NOT_MIGRATED in every row set" in r.text


def test_row_set_can_be_removed_and_needs_join_keys(client, tmp_path):
    add_vendor_row_set(client)
    assert (
        "Row set removed"
        in client.post(f"{BASE}/structures/g/rowsets/remove", data={"name": "FLVN01"}).text
    )
    assert not load_project(tmp_path / "ws" / "p1").objects["vendor"].config.structures[0].unions
    r = client.post(
        f"{BASE}/structures/g/rowsets", data={"name": "y", "source": "lfm1", "join_source": "lfa1"}
    )
    assert "Row set not added" in r.text and "key columns" in r.text


def test_row_set_cannot_be_called_main_or_join_its_own_source(client):
    assert (
        "Row set not added"
        in client.post(f"{BASE}/structures/g/rowsets", data={"name": "main", "source": "lfm1"}).text
    )
    r = client.post(
        f"{BASE}/structures/g/rowsets",
        data={"name": "z", "source": "lfm1", "join_source": "lfm1", "main0": "A", "other0": "A"},
    )
    assert "Row set not added" in r.text


def test_config_error_type_is_exported():
    assert ConfigError
