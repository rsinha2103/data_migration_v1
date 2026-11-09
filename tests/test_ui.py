import html
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_migration_concept.config import load_project
from data_migration_concept.ui.app import create_app


class PlainTextClient(TestClient):
    """Test client whose HTML responses are un-escaped, so assertions can quote messages."""

    def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().request(*args, **kwargs)
        if "text/html" in response.headers.get("content-type", ""):
            decoded = html.unescape(response.text)
            response._content = decoded.encode("utf-8")
            response.__dict__.pop("_text", None)  # httpx caches the decoded text
        return response


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DMC_WORKING_DB_URL", raising=False)
    return PlainTextClient(create_app(tmp_path / "ws"))


@pytest.fixture
def raw(client):
    """Same app, responses exactly as sent."""
    return TestClient(client.app)


@pytest.fixture
def demo(client):
    r = client.post("/projects/demo", data={"name": "demo"})
    assert r.status_code == 200 and "Demo project created" in r.text
    return client


def project_folder(tmp_path: Path, name: str) -> Path:
    return tmp_path / "ws" / name


CSV = b"ID,NAME,COUNTRY\n0001,Alpha Ltd,UK\n0002,Beta GmbH,DE\n0003,Gamma SA,FR\n"


def upload_object(
    client,
    name="customer",
    content=CSV,
    filename="customers.csv",
    project="p1",
    structure=True,
):
    """Create a project and an object from a file. By default also define a target structure
    by copying the source columns (the quick start), so the tests can map and run right away.
    Returns the response of the object creation."""
    client.post("/projects/create", data={"name": project})
    response = client.post(
        f"/p/{project}/objects/add",
        data={"name": name},
        files={"file": (filename, content, "text/csv")},
    )
    if structure and "Object created" in response.text:
        client.post(
            f"/p/{project}/o/{name}/structures/add",
            data={
                "name": "main",
                "target_table": f"{name}_main",
                "source": Path(filename).stem.lower(),
            },
        )
    return response


# --- pages and projects ---------------------------------------------------------------


def test_home_and_how_it_works(client):
    home = client.get("/")
    assert home.status_code == 200 and "Try the demo" in home.text
    assert (
        "Extract" in client.get("/how-it-works").text
        or "extract" in client.get("/how-it-works").text
    )


def test_create_project_validates_names(client):
    assert "Project created" in client.post("/projects/create", data={"name": "mine"}).text
    dup = client.post("/projects/create", data={"name": "mine"})
    assert "already exists" in dup.text
    bad = client.post("/projects/create", data={"name": "../evil"})
    assert "Project names start with a letter" in bad.text


def test_demo_project_pages_all_render(demo):
    paths = [
        "/p/demo",
        "/p/demo/o/supplier",
        "/p/demo/o/supplier/sources",
        "/p/demo/o/supplier/structures",
        "/p/demo/o/supplier/structures/general",
        "/p/demo/o/supplier/xref",
        "/p/demo/o/supplier/xref/country",
        "/p/demo/o/supplier/checks",
        "/p/demo/o/supplier/recon",
        "/p/demo/o/supplier/loads",
        "/p/demo/o/supplier/data",
        "/p/demo/o/supplier/results",
        "/p/demo/o/supplier/sql",
        "/p/demo/runs",
        "/p/demo/files",
        "/p/demo/files?path=project.yaml",
    ]
    for path in paths:
        r = demo.get(path)
        assert r.status_code == 200, path
    assert "CREATE TABLE" in demo.get("/p/demo/o/supplier/sql").text
    assert "LIFNR" in demo.get("/p/demo/o/supplier/structures/general").text


def test_run_everything_then_inspect(demo, tmp_path):
    r = demo.post("/p/demo/o/supplier/run/all")
    assert r.status_code == 200 and "Finished." in r.text
    assert "OK - extract" in r.text and "OK - load" in r.text
    assert "OK" in demo.get("/p/demo/o/supplier").text
    assert (project_folder(tmp_path, "demo") / "output" / "supplier_general.csv").is_file()
    assert (project_folder(tmp_path, "demo") / "reports" / "supplier_report.html").is_file()

    tables = demo.get("/p/demo/o/supplier/data")
    assert "stg_supplier_lfa1" in tables.text and "tgt_bp_general" in tables.text
    preview = demo.get("/p/demo/o/supplier/data/tgt_bp_general?q=Northwind")
    assert "Northwind Components GmbH" in preview.text and "Contoso" not in preview.text
    assert demo.get("/p/demo/o/supplier/data/not_a_table").status_code == 404

    csv_dl = demo.get("/p/demo/download?path=output/supplier_general.csv")
    assert csv_dl.status_code == 200 and b"0000100001" in csv_dl.content
    html = demo.get("/p/demo/download?path=reports/supplier_report.html&inline=1")
    assert "default-src 'none'" in html.headers["content-security-policy"]


def test_defects_block_the_load_and_the_ui_explains_why(client, tmp_path):
    client.post("/projects/demo", data={"name": "bad", "defects": "on"})
    r = client.post("/p/bad/o/supplier/run/all")
    assert "Finished with problems" in r.text
    assert "checks are blocking the load" in r.text
    flow = client.get("/p/bad/o/supplier")
    assert "Why the load is blocked" in flow.text and "partner_unique" in flow.text
    results = client.get("/p/bad/o/supplier/results?check=partner_unique")
    assert "0000100001" in results.text and "duplicate key" in results.text
    assert not (project_folder(tmp_path, "bad") / "output").exists()


def test_single_steps_report_clear_errors(client):
    upload_object(client)
    r = client.post("/p/p1/o/customer/run/transform")
    assert "run extract first" in r.text and "PROBLEM" not in r.text or "failed" in r.text.lower()
    assert "Unknown step" in client.post("/p/p1/o/customer/run/nope").text


# --- guided setup with uploaded data --------------------------------------------------


def test_new_object_has_a_source_but_no_target_structure_yet(client, tmp_path):
    r = upload_object(client, structure=False)
    assert r.status_code == 200 and "define the target structure" in r.text
    obj = load_project(project_folder(tmp_path, "p1")).objects["customer"]
    assert [s.name for s in obj.config.sources] == ["customers"]
    assert obj.config.structures == []
    out = client.post("/p/p1/o/customer/run/transform")
    assert "no target structure yet" in out.text
    assert "Define a target structure" in client.get("/p/p1/o/customer/structures").text


def test_upload_creates_object_with_first_mapping(client, tmp_path):
    r = upload_object(client)
    assert r.status_code == 200 and "Object created" in r.text
    folder = project_folder(tmp_path, "p1")
    project = load_project(folder)
    obj = project.objects["customer"]
    assert [s.name for s in obj.config.sources] == ["customers"]
    assert [f.target_field for f in obj.mappings["main"].fields] == ["ID", "NAME", "COUNTRY"]
    # first pass through the pipeline works on the uploaded data as is
    out = client.post("/p/p1/o/customer/run/all")
    assert "OK - extract" in out.text and "OK - transform" in out.text
    assert "0001" in client.get("/p/p1/o/customer/data/tgt_customer_main").text  # leading zeros


def test_upload_detects_semicolon_and_cp1252(client, tmp_path):
    content = "ID;NAME\n1;Müller\n".encode("cp1252")
    upload_object(client, content=content, filename="export.csv")
    source = load_project(project_folder(tmp_path, "p1")).objects["customer"].config.sources[0]
    assert source.delimiter == ";" and source.encoding == "cp1252"
    client.post("/p/p1/o/customer/run/extract")
    assert "Müller" in client.get("/p/p1/o/customer/data/stg_customer_export").text


def test_upload_rejects_bad_files_and_names(client, tmp_path):
    client.post("/projects/create", data={"name": "p1"})
    bad_ext = client.post(
        "/p/p1/objects/add", data={"name": "x"}, files={"file": ("a.exe", b"MZ", "application/x")}
    )
    assert "Upload a .csv" in bad_ext.text
    bad_name = client.post(
        "/p/p1/objects/add", data={"name": "../x"}, files={"file": ("a.csv", CSV, "text/csv")}
    )
    assert "Object names use letters" in bad_name.text
    empty = client.post(
        "/p/p1/objects/add", data={"name": "x"}, files={"file": ("a.csv", b"", "text/csv")}
    )
    assert "Could not create the object" in empty.text
    assert not (project_folder(tmp_path, "p1") / "objects" / "x").exists()
    assert not (project_folder(tmp_path, "p1") / ".dmc_probe").exists()


def test_mapping_edit_save_and_rollback(client, tmp_path):
    upload_object(client)
    url = "/p/p1/o/customer/structures/main"
    mapping_file = (
        project_folder(tmp_path, "p1") / "objects" / "customer" / "mappings" / "main.yaml"
    )
    form = {
        "r0_target": "CUSTOMER_ID", "r0_rule": "MOVE", "r0_src": "ID", "r0_mandatory": "on",
        "r0_length": "4", "r0_dtype": "string",
        "r1_target": "NAME", "r1_rule": "SUBSTRING", "r1_src": "NAME", "r1_a1": "1", "r1_a2": "5",
        "r2_target": "KIND", "r2_rule": "CONSTANT", "r2_a1": "CUST",
        "r3_target": "OLD", "r3_rule": "MOVE", "r3_src": "COUNTRY", "r3_delete": "on",
    }  # fmt: skip
    r = client.post(url, data=form)
    assert "Mapping saved" in r.text
    saved = mapping_file.read_text(encoding="utf-8")
    assert "CUSTOMER_ID" in saved and "OLD" not in saved and "SUBSTRING" in saved

    broken = dict(form, r1_a2="0")  # substring length must be >= 1
    r = client.post(url, data=broken)
    assert "Mapping not saved" in r.text
    assert mapping_file.read_text(encoding="utf-8") == saved  # untouched

    missing_src = dict(form, r2_rule="XREF", r2_src="COUNTRY", r2_a1="nope", r2_a2="error")
    r = client.post(url, data=missing_src)
    assert "Mapping not saved" in r.text and "xref" in r.text.lower()
    assert mapping_file.read_text(encoding="utf-8") == saved


def test_mapping_add_missing_columns(client, tmp_path):
    upload_object(client)
    form = {"r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID", "action": "add_missing"}
    client.post("/p/p1/o/customer/structures/main", data=form)
    mapping = (
        project_folder(tmp_path, "p1") / "objects" / "customer" / "mappings" / "main.yaml"
    ).read_text(encoding="utf-8")
    assert "NAME" in mapping and "COUNTRY" in mapping


def test_xref_workflow_with_unmapped_value_suggestions(client, tmp_path):
    upload_object(client)
    client.post("/p/p1/o/customer/run/extract")
    r = client.post("/p/p1/o/customer/xref/new", data={"name": "country", "content": "UK,GB\n"})
    assert "XREF created" in r.text

    form = {
        "r0_target": "ID", "r0_rule": "MOVE", "r0_src": "ID",
        "r1_target": "COUNTRY", "r1_rule": "XREF", "r1_src": "COUNTRY", "r1_a1": "country",
        "r1_a2": "error",
    }  # fmt: skip
    assert "Mapping saved" in client.post("/p/p1/o/customer/structures/main", data=form).text
    page = client.get("/p/p1/o/customer/xref/country").text
    assert "2 value(s)" in page and "DE" in page and "FR" in page  # found in the data
    out = client.post("/p/p1/o/customer/run/transform")
    assert "no entry in xref 'country'" in out.text  # DE and FR are not listed

    fixed = client.post("/p/p1/o/customer/xref/country", data={"content": "UK,GB\nDE,DE\nFR,FR\n"})
    assert "XREF saved" in fixed.text
    assert "OK - transform" in client.post("/p/p1/o/customer/run/transform").text
    assert "GB" in client.get("/p/p1/o/customer/data/tgt_customer_main").text


def test_xref_rejects_duplicates_and_refuses_to_delete_a_used_list(client):
    upload_object(client)
    dup = client.post("/p/p1/o/customer/xref/new", data={"name": "c", "content": "A,1\nA,2\n"})
    assert "mapped more than once" in dup.text
    client.post("/p/p1/o/customer/xref/new", data={"name": "c", "content": "UK,GB\n"})
    form = {
        "r0_target": "C",
        "r0_rule": "XREF",
        "r0_src": "COUNTRY",
        "r0_a1": "c",
        "r0_a2": "error",
    }
    client.post("/p/p1/o/customer/structures/main", data=form)
    gone = client.post("/p/p1/o/customer/xref/c/delete")
    assert "Could not delete it" in gone.text and "xref file" in gone.text


def test_checks_add_delete_and_validation(client, tmp_path):
    upload_object(client)
    base = {"name": "id_unique", "structure": "main", "type": "UNIQUE", "fields": "ID"}
    assert "Added" in client.post("/p/p1/o/customer/checks/add", data=base).text
    assert "id_unique" in client.get("/p/p1/o/customer/checks").text
    dup = client.post("/p/p1/o/customer/checks/add", data=base)
    assert "already an entry" in dup.text
    ghost = client.post("/p/p1/o/customer/checks/add", data=dict(base, name="g", fields="GHOST"))
    assert "no target field 'GHOST'" in ghost.text
    pattern = {"name": "p", "structure": "main", "type": "PATTERN", "field": "ID", "pattern": "("}
    assert (
        "invalid regular expression"
        in client.post("/p/p1/o/customer/checks/add", data=pattern).text
    )
    warn = {"name": "n", "structure": "main", "type": "NULL_RATE", "field": "NAME",
            "max_pct": "10", "severity": "warn"}  # fmt: skip
    assert "Added" in client.post("/p/p1/o/customer/checks/add", data=warn).text
    client.post("/p/p1/o/customer/run/extract")
    client.post("/p/p1/o/customer/run/transform")
    out = client.post("/p/p1/o/customer/run/check")
    assert "checks passed" in out.text
    assert "Deleted" in client.post("/p/p1/o/customer/checks/delete", data={"name": "n"}).text


def test_recon_and_loads_forms(client, tmp_path):
    upload_object(client)
    recon = {"name": "cnt", "structure": "main", "type": "COUNT"}
    assert "Added" in client.post("/p/p1/o/customer/recon/add", data=recon).text
    keys = {
        "name": "k",
        "structure": "main",
        "type": "KEYS",
        "source_key": "ID",
        "target_key": "ID",
    }
    assert "Added" in client.post("/p/p1/o/customer/recon/add", data=keys).text
    load = {
        "structure": "main",
        "kind": "file",
        "path": "output/c.csv",
        "format": "csv",
        "delimiter": ",",
    }
    assert "Load destination added" in client.post("/p/p1/o/customer/loads/add", data=load).text
    escape = dict(load, path="../../escape.csv")
    assert "leaves the project" in client.post("/p/p1/o/customer/loads/add", data=escape).text

    out = client.post("/p/p1/o/customer/run/all")
    assert "OK - load" in out.text and "reconciliations agree" in out.text
    assert (project_folder(tmp_path, "p1") / "output" / "c.csv").is_file()
    assert "Deleted" in client.post("/p/p1/o/customer/loads/delete", data={"index": "0"}).text


def test_sources_and_structures_management(client, tmp_path):
    upload_object(client)
    second = client.post(
        "/p/p1/o/customer/sources/upload",
        files={"file": ("regions.csv", b"CODE,REGION\nDE,EU\n", "text/csv")},
    )
    assert "Source added" in second.text
    added = client.post(
        "/p/p1/o/customer/structures/add",
        data={"name": "regions", "target_table": "cust_regions", "source": "regions"},
    )
    assert "Structure created" in added.text and "CODE" in added.text
    in_use = client.post("/p/p1/o/customer/sources/remove", data={"name": "regions"})
    assert "Could not remove" in in_use.text and "unknown source" in in_use.text
    assert (
        "Structure removed"
        in client.post("/p/p1/o/customer/structures/remove", data={"name": "regions"}).text
    )
    assert (
        "Source removed"
        in client.post("/p/p1/o/customer/sources/remove", data={"name": "regions"}).text
    )


# --- raw file editor and safety -------------------------------------------------------


def test_raw_editor_refuses_changes_that_break_the_project(client, tmp_path):
    upload_object(client)
    path = "objects/customer/object.yaml"
    folder = project_folder(tmp_path, "p1")
    original = (folder / path).read_text(encoding="utf-8")
    bad = client.post("/p/p1/files", data={"rel": path, "content": "name: [unclosed"})
    assert "Not valid YAML" in bad.text
    broken = original.replace("source: customers", "source: nowhere")
    r = client.post("/p/p1/files", data={"rel": path, "content": broken})
    assert "Not saved" in r.text and "unknown source 'nowhere'" in r.text
    assert (folder / path).read_text(encoding="utf-8") == original
    fine = original.replace("customer_main", "customer_main2")
    assert "Saved." in client.post("/p/p1/files", data={"rel": path, "content": fine}).text


def test_file_access_is_confined(client, tmp_path):
    upload_object(client)
    assert client.get("/p/p1/files?path=../../etc/passwd").status_code == 404
    assert client.get("/p/p1/files?path=work.db").status_code == 404
    assert client.get("/p/p1/download?path=project.yaml").status_code == 404
    assert client.get("/p/p1/download?path=../p1/project.yaml").status_code == 404
    assert client.get("/p/nope").status_code == 404
    assert client.get("/p/p1/o/ghost").status_code == 404
    bad = client.post("/p/p1/files", data={"rel": "../x.yaml", "content": "a: 1"})
    assert "cannot be edited" in bad.text


def test_only_localhost_and_same_origin(client):
    assert client.get("/", headers={"host": "evil.example.com"}).status_code == 403
    cross = client.post(
        "/projects/create", data={"name": "x"}, headers={"origin": "http://evil.example.com"}
    )
    assert cross.status_code == 403
    same = client.post(
        "/projects/create",
        data={"name": "x"},
        headers={"origin": "http://testserver", "host": "testserver"},
    )
    assert same.status_code == 200


def test_page_output_is_escaped(client, raw, tmp_path):
    evil = b"ID,NAME\n1,<script>alert(1)</script>\n"
    upload_object(client, content=evil, filename="evil.csv")
    client.post("/p/p1/o/customer/run/extract")
    page = raw.get("/p/p1/o/customer/data/stg_customer_evil").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_invalid_project_shows_problems_and_a_way_to_fix(client, tmp_path):
    upload_object(client)
    folder = project_folder(tmp_path, "p1")
    (folder / "objects" / "customer" / "data" / "customers.csv").unlink()
    r = client.get("/p/p1/o/customer")
    assert r.status_code == 422 and "not found" in r.text and "Open the project files" in r.text
    assert "object.yaml" in client.get("/p/p1/files").text  # the editor still opens


def test_add_existing_folder_and_forget(client, tmp_path):
    outside = tmp_path / "elsewhere" / "proj"
    outside.mkdir(parents=True)
    (outside / "project.yaml").write_text(
        "name: Outside\nworking_connection: w\n"
        "connections:\n  w:\n    url_env: DMC_WORKING_DB_URL\n",
        encoding="utf-8",
    )
    (outside / "objects").mkdir()
    assert "Project added" in client.post("/projects/add", data={"folder": str(outside)}).text
    assert "already listed" in client.post("/projects/add", data={"folder": str(outside)}).text
    assert "no project.yaml" in client.post("/projects/add", data={"folder": str(tmp_path)}).text
    gone = client.post("/p/proj/forget")
    assert "folder was not deleted" in gone.text and outside.is_dir()
