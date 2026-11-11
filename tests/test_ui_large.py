"""Structures with hundreds of fields: the mapping form has thousands of inputs."""

import html

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.ui.app import create_app

BASE = "/p/p1/o/big"
FIELDS = 400  # about 3,600 form fields on the mapping page, far above Starlette's default of 1,000


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
    header = ",".join(f"COL{i:03d}" for i in range(FIELDS))
    row = ",".join(str(i) for i in range(FIELDS))
    c.post(
        "/p/p1/objects/add",
        data={"name": "big"},
        files={"file": ("big.csv", f"{header}\n{row}\n".encode(), "text/csv")},
    )
    pasted = "\n".join(f"COL{i:03d}\tField {i}\tText\t10\t" for i in range(FIELDS))
    page = c.post(f"{BASE}/structures/template", data={"pasted": pasted, "structure_name": "wide"})
    c.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "wide", "table_0": "wide_t", "source_0": "big"},
    )
    return c


def mapping_form(rule: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for i in range(FIELDS):
        data[f"r{i}_target"] = f"COL{i:03d}"
        data[f"r{i}_rule"] = rule
        data[f"r{i}_dtype"] = "string"
        data[f"r{i}_length"] = "10"
        data[f"r{i}_desc"] = f"Field {i}"
        data[f"r{i}_note"] = ""
        data[f"r{i}_review"] = ""
        if rule == "MOVE":
            data[f"r{i}_src"] = f"COL{i:03d}"
    return data


def test_a_mapping_with_hundreds_of_fields_can_be_saved(client):
    form = mapping_form("MOVE")
    assert len(form) > 1000
    r = client.post(f"{BASE}/structures/wide", data=form)
    assert "Mapping saved" in r.text, r.text[:300]
    page = client.get(f"{BASE}/structures/wide").text
    assert f"{FIELDS} of {FIELDS}</span> target fields mapped" in page


def test_auto_map_works_on_a_wide_structure(client):
    form = mapping_form("UNMAPPED")
    form["action"] = "auto_map"
    r = client.post(f"{BASE}/structures/wide", data=form)
    assert f"{FIELDS} field(s) mapped by matching names" in r.text, r.text[:300]
