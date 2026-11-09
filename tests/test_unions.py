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


def test_row_set_grid_shows_every_field_and_stores_only_differences(client, tmp_path):
    client.post(
        f"{BASE}/structures/g/rowsets",
        data={"name": "FLVN01", "source": "lfm1", "join_source": "lfa1", "main0": "LIFNR",
              "other0": "LIFNR"},
    )  # fmt: skip
    page = client.get(f"{BASE}/structures/g").text
    for field in ("LIFNR", "NAME1", "BP_ROLE", "ORG"):
        assert (
            f'name="o{["LIFNR", "NAME1", "BP_ROLE", "ORG"].index(field)}_field" value="{field}"'
            in page
        )
    grid = {
        "o0_field": "LIFNR", "o0_rule": "MOVE", "o0_src": "LIFNR",
        "o1_field": "NAME1", "o1_rule": "MOVE", "o1_src": "lfa1.NAME1",  # same as main
        "o2_field": "BP_ROLE", "o2_rule": "CONSTANT", "o2_a1": "FLVN01",  # differs
        "o3_field": "ORG", "o3_rule": "MOVE", "o3_src": "EKORG",  # differs
    }  # fmt: skip
    r = client.post(f"{BASE}/structures/g/rowsets/FLVN01/rules", data=grid)
    assert "2 field(s) with their own rule" in r.text
    union = load_project(tmp_path / "ws" / "p1").objects["vendor"].config.structures[0].unions[0]
    assert set(union.overrides) == {"BP_ROLE", "ORG"}
    # putting a rule back to the main one removes the override
    grid.update({"o2_a1": "FLVN00"})
    client.post(f"{BASE}/structures/g/rowsets/FLVN01/rules", data=grid)
    union = load_project(tmp_path / "ws" / "p1").objects["vendor"].config.structures[0].unions[0]
    assert set(union.overrides) == {"ORG"}
    assert "own rule" in client.get(f"{BASE}/structures/g").text


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def spec_with_row_set(client):
    add_vendor_row_set(client)
    return client.get(f"{BASE}/structures/g/spec.xlsx").content


def rewrite(content, sheet, edits):
    """Change cells of an exported workbook: edits = {(target, header): value}."""
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content))
    ws = wb[sheet]
    headers = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        target = row[0].value
        for (t, header), value in edits.items():
            if t == target:
                row[headers.index(header)].value = value
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_excel_spec_has_a_sheet_per_row_set(client):
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(spec_with_row_set(client)))
    assert wb.sheetnames[:3] == ["Mapping", "Rows FLVN01", "How to read"]
    rows = {r[0]: r for r in wb["Rows FLVN01"].iter_rows(min_row=2, values_only=True)}
    assert set(rows) == {"LIFNR", "NAME1", "BP_ROLE", "ORG"}
    headers = [c.value for c in wb["Rows FLVN01"][1]]
    assert headers[-1] == "Own rule" and "Review" not in headers
    assert rows["BP_ROLE"][headers.index("Source")] in (None, "")
    assert rows["BP_ROLE"][headers.index("Setting 1")] == "FLVN01"
    assert rows["BP_ROLE"][-1] == "own rule" and rows["LIFNR"][-1] == "as main"
    guide = [r[0] for r in wb["How to read"].iter_rows(values_only=True) if r[0]]
    assert any("Rows FLVN01: reads lfm1, joined with lfa1 on LIFNR = LIFNR" in g for g in guide)


def test_excel_spec_round_trip_has_nothing_to_import(client):
    content = spec_with_row_set(client)
    r = client.post(f"{BASE}/structures/g/spec", files={"file": ("s.xlsx", content, XLSX)})
    assert "nothing to import" in r.text


def test_changes_on_a_row_set_sheet_are_imported_as_overrides(client, tmp_path):
    content = rewrite(
        spec_with_row_set(client),
        "Rows FLVN01",
        {("BP_ROLE", "Setting 1"): "FLVN09", ("ORG", "Source"): "BUKRS"},  # back to the main rule
    )
    preview = client.post(f"{BASE}/structures/g/spec", files={"file": ("s.xlsx", content, XLSX)})
    assert "Rows FLVN01" in preview.text and "FLVN09" in preview.text
    token = preview.url.path.rsplit("/", 1)[1]
    assert "2 change(s) imported" in client.post(f"{BASE}/structures/g/spec/{token}").text
    union = load_project(tmp_path / "ws" / "p1").objects["vendor"].config.structures[0].unions[0]
    assert union.overrides["BP_ROLE"].value == "FLVN09"
    assert "ORG" not in union.overrides  # equal to the main rule again, so no override


def test_a_main_only_change_does_not_leak_into_row_sets_but_followers_follow(client, tmp_path):
    content = rewrite(
        spec_with_row_set(client),
        "Mapping",
        {("NAME1", "Source"): "lfa1.NAME1", ("LIFNR", "Rule"): "MOVE"},
    )
    # the main mapping gets a different constant; the row set sheet still shows the old main rule
    content = rewrite(content, "Mapping", {("BP_ROLE", "Setting 1"): "FLVN07"})
    preview = client.post(f"{BASE}/structures/g/spec", files={"file": ("s.xlsx", content, XLSX)})
    token = preview.url.path.rsplit("/", 1)[1]
    client.post(f"{BASE}/structures/g/spec/{token}")
    cfg = load_project(tmp_path / "ws" / "p1").objects["vendor"]
    mapping = {f.target_field: f for f in next(iter(cfg.mappings.values())).fields}
    assert mapping["BP_ROLE"].rule.value == "FLVN07"
    union = cfg.config.structures[0].unions[0]
    assert union.overrides["BP_ROLE"].value == "FLVN01"  # its own rule is kept
    assert "LIFNR" not in union.overrides  # still follows the main rule


def save_name_rule(client, sql):
    client.post(
        f"{BASE}/structures/g",
        data={
            "r0_target": "LIFNR", "r0_rule": "MOVE", "r0_src": "LIFNR", "r0_dtype": "string",
            "r1_target": "NAME1", "r1_rule": "SQL", "r1_a1": sql, "r1_dtype": "string",
            "r2_target": "BP_ROLE", "r2_rule": "CONSTANT", "r2_a1": "X", "r2_dtype": "string",
            "r3_target": "ORG", "r3_rule": "MOVE", "r3_src": "BUKRS", "r3_dtype": "string",
        },
    )  # fmt: skip


def test_sql_rule_with_an_unknown_table_gives_a_readable_message(client):
    save_name_rule(client, "select NAME1 from lfa1 where lifnr = LIFNR")
    client.post(f"{BASE}/run/extract")
    out = client.post(f"{BASE}/run/transform").text
    assert "database rejected the generated SQL" in out and "no such table: lfa1" in out
    assert "stg." in out  # the hint says how to name other sources
    assert "database rejected" in client.get(f"{BASE}/structures/g/preview").text


def test_sql_rule_can_name_other_sources_with_stg(client, tmp_path):
    save_name_rule(
        client,
        'select a."NAME1" from {{ stg.lfa1 }} a where a."LIFNR" = s."LIFNR"',
    )
    client.post(f"{BASE}/run/extract")
    assert "OK - transform" in client.post(f"{BASE}/run/transform").text
    assert [r[:2] for r in target_rows(tmp_path)] == [("1", "Acme"), ("1", "Acme"), ("2", "Beta")]


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
