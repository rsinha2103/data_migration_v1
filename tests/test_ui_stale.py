"""A step whose inputs were edited after it ran is shown as out of date."""

import html
import re

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.ui.app import create_app

BASE = "/p/p1/o/vendor"
PASTED = "LIFNR\tSupplier\tText\t80\tX\nBP_ROLE\tBP Role\tText\t80\tX\n"


class PlainTextClient(TestClient):
    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().request(*args, **kwargs)
        if "text/html" in response.headers.get("content-type", ""):
            response._content = html.unescape(response.text).encode("utf-8")
            response.__dict__.pop("_text", None)
        return response


def mapping(role_rule: str) -> dict[str, str]:
    data = {
        "r0_target": "LIFNR", "r0_rule": "MOVE", "r0_src": "LIFNR", "r0_dtype": "string",
        "r1_target": "BP_ROLE", "r1_dtype": "string",
    }  # fmt: skip
    if role_rule == "CONSTANT":
        data.update({"r1_rule": "CONSTANT", "r1_a1": "FLVN00"})
    else:
        data.update({"r1_rule": "UNMAPPED"})
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DMC_WORKING_DB_URL", raising=False)
    c = PlainTextClient(create_app(tmp_path / "ws"))
    c.post("/projects/create", data={"name": "p1"})
    c.post(
        "/p/p1/objects/add",
        data={"name": "vendor"},
        files={"file": ("lfa1.csv", b"LIFNR\n1\n2\n", "text/csv")},
    )
    page = c.post(f"{BASE}/structures/template", data={"pasted": PASTED, "structure_name": "g"})
    c.post(page.url.path, data={"inc_0": "on", "name_0": "g", "table_0": "g_t", "source_0": "lfa1"})
    c.post(f"{BASE}/structures/g", data=mapping("UNMAPPED"))
    c.post(
        f"{BASE}/loads/add",
        data={"structure": "g", "kind": "file", "path": "output/v.csv", "format": "csv"},
    )
    return c


def stale(client):
    page = client.get(BASE).text
    return re.findall(
        r'<h3>\d\. (\w+)</h3>\s*<span class="badge \w+">\w+(?: \w+)?</span>\s*<span class="badge STALE"[^>]*title="([^"]*)"',
        page,
    )


def test_editing_the_mapping_after_transform_marks_transform_and_load_out_of_date(client, tmp_path):
    for step in ("extract", "transform", "load"):
        client.post(f"{BASE}/run/{step}")
    assert stale(client) == []
    client.post(f"{BASE}/structures/g", data=mapping("CONSTANT"))
    found = dict(stale(client))
    assert "transform" in found and "load" in found
    assert "mappings/g.yaml was changed after this step ran" in found["transform"]
    assert "extract" not in found  # the mapping does not affect the staged data
    # the stale target really has no role: that is what the warning protects against
    assert (tmp_path / "ws" / "p1" / "output" / "v.csv").read_text().splitlines()[1] == "1,"
    client.post(f"{BASE}/run/transform")
    client.post(f"{BASE}/run/load")
    assert stale(client) == []
    assert (tmp_path / "ws" / "p1" / "output" / "v.csv").read_text().splitlines()[1] == "1,FLVN00"


def test_a_new_source_file_makes_extract_out_of_date(client):
    client.post(f"{BASE}/run/extract")
    assert stale(client) == []
    client.post(f"{BASE}/sources/upload", files={"file": ("more.csv", b"X\n1\n", "text/csv")})
    assert "extract" in dict(stale(client))
