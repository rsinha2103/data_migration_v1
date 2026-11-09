"""The mapping as an Excel specification: export it, review it, import the changes."""

import html
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from data_migration_concept.config import load_project
from data_migration_concept.ui.app import create_app

CSV = b"ID,NAME,COUNTRY\n0001,Alpha,UK\n0002,Beta,DE\n"
BASE = "/p/p1/o/customer"
PASTED = "ID\tCustomer id\tText\t4\tX\nNAME\tName\tText\t20\tX\nCOUNTRY\tCountry\tText\t2\t\nNOTE\tNote\tText\t10\t\n"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    page = c.post(f"{BASE}/structures/template", data={"pasted": PASTED, "structure_name": "g"})
    c.post(
        page.url.path,
        data={"inc_0": "on", "name_0": "g", "table_0": "g_t", "source_0": "customers"},
    )
    # map two fields so there is something to review
    c.post(
        f"{BASE}/structures/g",
        data={
            "r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID", "r0_dtype": "string",
            "r1_target": "NAME", "r1_rule": "MOVE", "r1_src": "NAME", "r1_dtype": "string",
            "r2_target": "COUNTRY", "r2_rule": "UNMAPPED", "r2_dtype": "string",
            "r3_target": "NOTE", "r3_rule": "UNMAPPED", "r3_dtype": "string",
        },
    )  # fmt: skip
    return c


def sheet_bytes(rows, headers=("Target field", "Rule", "Source", "Setting 1", "Note", "Review")):
    wb = Workbook()
    ws = wb.active
    ws.title = "Mapping"
    ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(client, content, name="spec.xlsx"):
    return client.post(f"{BASE}/structures/g/spec", files={"file": (name, content, XLSX)})


def mapping_fields(tmp_path):
    project = load_project(tmp_path / "ws" / "p1")
    mapping = next(iter(project.objects["customer"].mappings.values()))
    return {f.target_field: f for f in mapping.fields}


def test_export_has_one_row_per_field_in_plain_words(client):
    r = client.get(f"{BASE}/structures/g/spec.xlsx")
    assert r.status_code == 200 and r.headers["content-type"] == XLSX
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[:2] == ["Mapping", "How to read"]
    ws = wb["Mapping"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Target field" and rows[0][-1] == "Review"
    by_target = {row[0]: row for row in rows[1:]}
    assert set(by_target) == {"ID", "NAME", "COUNTRY", "NOTE"}
    assert by_target["NAME"][6] == "MOVE" and by_target["NAME"][7] == "NAME"
    assert by_target["NAME"][11] == "Copy NAME."
    assert by_target["COUNTRY"][6] == "(not mapped)"


def test_export_then_import_unchanged_has_nothing_to_do(client):
    exported = client.get(f"{BASE}/structures/g/spec.xlsx").content
    r = upload(client, exported)
    assert "nothing to import" in r.text


def test_import_shows_changes_first_and_applies_only_on_confirmation(client, tmp_path):
    content = sheet_bytes(
        [
            ["COUNTRY", "XREF", "COUNTRY", "country_iso", "Check with finance", "changes"],
            ["NAME", "MOVE", "NAME", "", "", "approved"],
            ["NOTE", "CONSTANT", "", "n/a", "", ""],
            ["NOT_IN_STRUCTURE", "MOVE", "X", "", "", ""],
        ]
    )
    client.post(f"{BASE}/xref/new", data={"name": "country_iso", "content": "UK,GB\nDE,DE\n"})
    preview = upload(client, content)
    assert "Import mapping from spec.xlsx" in preview.text
    assert "Translate COUNTRY through the list" in preview.text
    assert "Check with finance" in preview.text
    assert "NOT_IN_STRUCTURE" in preview.text  # reported as ignored
    assert mapping_fields(tmp_path)["COUNTRY"].rule.type == "UNMAPPED"  # still untouched

    token = preview.url.path.rsplit("/", 1)[1]
    done = client.post(f"{BASE}/structures/g/spec/{token}")
    assert "change(s) imported" in done.text
    fields = mapping_fields(tmp_path)
    assert fields["COUNTRY"].rule.type == "XREF" and fields["COUNTRY"].review == "changes"
    assert fields["COUNTRY"].note == "Check with finance"
    assert fields["NAME"].review == "approved"
    assert fields["NOTE"].rule.type == "CONSTANT" and fields["NOTE"].rule.value == "n/a"


def test_import_reports_rows_it_cannot_use_and_keeps_their_rule(client, tmp_path):
    content = sheet_bytes(
        [
            ["COUNTRY", "MOVE", "", "", "", ""],  # no source
            ["NOTE", "TELEPORT", "X", "", "", ""],  # no such rule
        ]
    )
    preview = upload(client, content)
    assert "Rows that cannot be imported" in preview.text
    assert "a source column is required" in preview.text
    assert "unknown rule" in preview.text
    assert mapping_fields(tmp_path)["COUNTRY"].rule.type == "UNMAPPED"


def test_a_simple_csv_with_rule_and_source_is_enough(client, tmp_path):
    csv = b"Target field,Rule,Source\nCOUNTRY,MOVE,COUNTRY\n"
    preview = client.post(f"{BASE}/structures/g/spec", files={"file": ("m.csv", csv, "text/csv")})
    token = preview.url.path.rsplit("/", 1)[1]
    client.post(f"{BASE}/structures/g/spec/{token}")
    assert mapping_fields(tmp_path)["COUNTRY"].rule.source_field == "COUNTRY"


def test_a_file_without_a_target_column_is_refused(client):
    r = upload(client, sheet_bytes([["x"]], headers=("Foo",)))
    assert "Could not read the mapping file" in r.text


def test_notes_and_review_survive_saving_in_the_editor(client, tmp_path):
    client.post(
        f"{BASE}/structures/g",
        data={
            "r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID", "r0_dtype": "string",
            "r0_note": "business ok", "r0_review": "approved",
        },
    )  # fmt: skip
    f = mapping_fields(tmp_path)["ID"]
    assert f.note == "business ok" and f.review == "approved"
    page = client.get(f"{BASE}/structures/g")
    assert "business ok" in page.text and "1 approved" in page.text
