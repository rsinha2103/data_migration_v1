from pathlib import Path

import polars as pl
import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, text

from data_migration_concept.config import load_project
from data_migration_concept.extract import ExtractError, extract_object
from data_migration_concept.runlog import RunLog


@pytest.fixture
def working():
    engine = create_engine("sqlite://")
    log = RunLog(engine)
    log.ensure_schema()
    return engine, log


def _extract(project_dir: Path, engine, log):
    project = load_project(project_dir)
    run_id = log.start_run(project.config.name, "supplier")
    return project, run_id, extract_object(project, "supplier", engine, log, run_id)


def test_csv_extract_stages_text_with_leading_zeros_and_lineage(example_project, working):
    engine, log = working
    _, run_id, results = _extract(example_project, engine, log)

    assert results[0].table_name == "stg_supplier_lfa1"
    assert (results[0].rows_read, results[0].rows_written) == (4, 4)
    with engine.connect() as conn:
        rows = conn.execute(
            text("select LIFNR, _dmc_row_no, _dmc_run_id from stg_supplier_lfa1 order by 2")
        ).all()
    assert rows[0] == ("0000100001", 1, run_id)  # leading zeros survive
    assert [r[1] for r in rows] == [1, 2, 3, 4]


def test_run_log_gets_step_counts_and_schema(example_project, working):
    engine, log = working
    _, run_id, _ = _extract(example_project, engine, log)
    step = log.get_steps(run_id)[0]
    assert (step["step_name"], step["status"], step["rows_in"], step["rows_out"]) == (
        "extract:lfa1",
        "SUCCESS",
        4,
        4,
    )
    cols = [c for c, _ in log.get_schema(run_id, "stg_supplier_lfa1")]
    assert cols == ["LIFNR", "NAME1", "LAND1"]


def test_reextract_replaces_staging_table(example_project, working):
    engine, log = working
    _extract(example_project, engine, log)
    _extract(example_project, engine, log)
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from stg_supplier_lfa1")).scalar() == 4


def test_mapping_referencing_missing_column_fails_and_keeps_old_staging(project_copy, working):
    engine, log = working
    _extract(project_copy, engine, log)
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    data.write_text("LIFNR,NAME1\n1,A\n", encoding="utf-8")  # LAND1 dropped
    project = load_project(project_copy)
    run_id = log.start_run("p", "supplier")
    with pytest.raises(ExtractError, match="missing source column.*LAND1"):
        extract_object(project, "supplier", engine, log, run_id)
    assert log.get_steps(run_id)[0]["status"] == "FAILED"
    with engine.connect() as conn:  # earlier staging is untouched
        assert conn.execute(text("select count(*) from stg_supplier_lfa1")).scalar() == 4


def test_duplicate_and_reserved_columns_are_rejected(project_copy, working):
    engine, log = working
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    data.write_text("LIFNR,NAME1,LAND1,_dmc_row_no\n1,A,DE,9\n", encoding="utf-8")
    with pytest.raises(ExtractError, match="reserved column"):
        _extract(project_copy, engine, log)


def test_unknown_object(example_project, working):
    engine, log = working
    project = load_project(example_project)
    with pytest.raises(ExtractError, match="unknown object 'nope'"):
        extract_object(project, "nope", engine, log, log.start_run("p", "nope"))


def test_csv_custom_delimiter_and_encoding(project_copy, working):
    engine, log = working
    obj = project_copy / "objects" / "supplier"
    (obj / "data" / "lfa1.csv").write_bytes("LIFNR;NAME1;LAND1\n1;Müller;DE\n".encode("cp1252"))
    cfg = (obj / "object.yaml").read_text(encoding="utf-8")
    cfg = cfg.replace("format: csv", "format: csv\n    delimiter: ';'\n    encoding: cp1252")
    (obj / "object.yaml").write_text(cfg, encoding="utf-8")
    _extract(project_copy, engine, log)
    with engine.connect() as conn:
        assert conn.execute(text("select NAME1 from stg_supplier_lfa1")).scalar() == "Müller"


def test_xlsx_extract_keeps_text_and_skips_blank_rows(project_copy, working):
    engine, log = working
    obj = project_copy / "objects" / "supplier"
    wb = Workbook()
    ws = wb.active
    ws.append(["LIFNR", "NAME1", "LAND1", None])
    ws.append(["0000100001", "Northwind", "DE", None])
    ws.append([None, None, None, None])
    ws.append(["0000100002", "Contoso", "UK", None])
    wb.save(obj / "data" / "lfa1.xlsx")
    cfg = (obj / "object.yaml").read_text(encoding="utf-8")
    cfg = cfg.replace("data/lfa1.csv", "data/lfa1.xlsx").replace("format: csv", "format: xlsx")
    (obj / "object.yaml").write_text(cfg, encoding="utf-8")
    _, _, results = _extract(project_copy, engine, log)
    assert results[0].rows_written == 2
    with engine.connect() as conn:
        assert conn.execute(
            text("select LIFNR from stg_supplier_lfa1 order by 1")
        ).scalars().all() == [
            "0000100001",
            "0000100002",
        ]


def test_parquet_keeps_native_types(project_copy, working):
    engine, log = working
    obj = project_copy / "objects" / "supplier"
    pl.DataFrame({"LIFNR": [1, 2], "NAME1": ["A", "B"], "LAND1": ["DE", "US"]}).write_parquet(
        obj / "data" / "lfa1.parquet"
    )
    cfg = (obj / "object.yaml").read_text(encoding="utf-8")
    cfg = cfg.replace("data/lfa1.csv", "data/lfa1.parquet").replace(
        "format: csv", "format: parquet"
    )
    (obj / "object.yaml").write_text(cfg, encoding="utf-8")
    _, run_id, _ = _extract(project_copy, engine, log)
    assert dict(log.get_schema(run_id, "stg_supplier_lfa1"))["LIFNR"].startswith("Int")


def test_sql_source_table_and_query(project_copy, working, tmp_path, monkeypatch):
    engine, log = working
    legacy = tmp_path / "legacy.db"
    legacy_engine = create_engine(f"sqlite:///{legacy}")
    with legacy_engine.begin() as conn:
        conn.execute(text("create table lfa1 (LIFNR text, NAME1 text, LAND1 text)"))
        conn.execute(text("insert into lfa1 values ('0001','A','DE'),('0002','B','US')"))
    monkeypatch.setenv("LEGACY_DB_URL", f"sqlite:///{legacy}")

    (project_copy / "project.yaml").write_text(
        (project_copy / "project.yaml").read_text(encoding="utf-8")
        + "  legacy:\n    url_env: LEGACY_DB_URL\n",
        encoding="utf-8",
    )
    obj = project_copy / "objects" / "supplier"
    (obj / "object.yaml").write_text(
        "name: supplier\n"
        "sources:\n"
        "  - kind: sql\n    name: lfa1\n    connection: legacy\n"
        "    query: select * from lfa1 where LAND1 = 'DE'\n"
        "structures:\n"
        "  - name: general\n    target_table: bp_general\n    source: lfa1\n"
        "    mapping: mappings/general.yaml\n",
        encoding="utf-8",
    )
    _, _, results = _extract(project_copy, engine, log)
    assert results[0].rows_written == 1

    cfg = (
        (obj / "object.yaml")
        .read_text(encoding="utf-8")
        .replace("query: select * from lfa1 where LAND1 = 'DE'", "table: lfa1")
    )
    (obj / "object.yaml").write_text(cfg, encoding="utf-8")
    _, _, results = _extract(project_copy, engine, log)
    assert results[0].rows_written == 2


def test_sql_source_with_no_rows_still_creates_table(project_copy, working, tmp_path, monkeypatch):
    engine, log = working
    legacy = tmp_path / "legacy.db"
    legacy_engine = create_engine(f"sqlite:///{legacy}")
    with legacy_engine.begin() as conn:
        conn.execute(text("create table lfa1 (LIFNR text, NAME1 text, LAND1 text)"))
    monkeypatch.setenv("LEGACY_DB_URL", f"sqlite:///{legacy}")
    (project_copy / "project.yaml").write_text(
        (project_copy / "project.yaml").read_text(encoding="utf-8")
        + "  legacy:\n    url_env: LEGACY_DB_URL\n",
        encoding="utf-8",
    )
    obj = project_copy / "objects" / "supplier"
    cfg = (obj / "object.yaml").read_text(encoding="utf-8")
    cfg = cfg.replace(
        "kind: file\n    name: lfa1\n    path: data/lfa1.csv\n    format: csv",
        "kind: sql\n    name: lfa1\n    connection: legacy\n    table: lfa1",
    )
    (obj / "object.yaml").write_text(cfg, encoding="utf-8")
    _, _, results = _extract(project_copy, engine, log)
    assert results[0].rows_written == 0
