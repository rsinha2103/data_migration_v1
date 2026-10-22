from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from data_migration_concept.config import load_project
from data_migration_concept.extract import extract_object
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform import (
    TransformError,
    generate_object,
    to_dialect,
    transform_object,
)

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def working():
    engine = create_engine("sqlite://")
    log = RunLog(engine)
    log.ensure_schema()
    return engine, log


def _run(project_dir: Path, engine, log, *, build=False):
    project = load_project(project_dir)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    results = transform_object(project, "supplier", engine, log, run_id, write_build=build)
    return project, run_id, results


def _rows(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).all()


def _edit(project_dir: Path, rel: str, old: str, new: str):
    path = project_dir / "objects" / "supplier" / rel
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_generated_sql_matches_golden_file(example_project):
    project = load_project(example_project)
    generated = generate_object(project, project.objects["supplier"])
    assert generated[0].render() == (GOLDEN / "supplier_general.sql").read_text(encoding="utf-8")


def test_example_transforms_correctly_on_sqlite(example_project, working):
    engine, log = working
    _, run_id, results = _run(example_project, engine, log)
    assert (results[0].rows_in, results[0].rows_out) == (4, 4)
    row = _rows(
        engine,
        'select "PARTNER","BU_GROUP","COUNTRY","SEARCH_TERM","LEGACY_REF","_dmc_row_no" '
        'from tgt_bp_general order by "_dmc_row_no"',
    )[1]
    assert row == (
        "0000100002",
        "SUPL",
        "GB",  # UK mapped through the XREF
        "Contoso Lo",
        "0000100002-Contoso Logistics Ltd",
        2,
    )
    steps = [s["step_name"] for s in log.get_steps(run_id)]
    assert steps == ["extract:lfa1", "xref:country", "transform:general"]


def test_retransform_replaces_target(example_project, working):
    engine, log = working
    _run(example_project, engine, log)
    _run(example_project, engine, log)
    assert _rows(engine, "select count(*) from tgt_bp_general")[0][0] == 4


def test_build_file_is_written(project_copy, working):
    engine, log = working
    _, _, results = _run(project_copy, engine, log, build=True)
    build = results[0].build_file
    assert build == project_copy / "build" / "supplier" / "general.sql"
    assert build.read_text(encoding="utf-8").startswith("-- structure: general -> tgt_bp_general")


def test_unmapped_xref_value_fails_and_keeps_old_target(project_copy, working):
    engine, log = working
    _run(project_copy, engine, log)
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    data.write_text("LIFNR,NAME1,LAND1\n1,A,FR\n2,B,IT\n3,C,\n", encoding="utf-8")
    project = load_project(project_copy)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    with pytest.raises(TransformError, match="no entry in xref 'country'.*'FR', 'IT'"):
        transform_object(project, "supplier", engine, log, run_id, write_build=False)
    assert log.get_steps(run_id)[-1]["status"] == "FAILED"
    assert _rows(engine, "select count(*) from tgt_bp_general")[0][0] == 4  # untouched


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("on_unmapped: default, default: XX", "XX"), ("on_unmapped: blank", None)],
)
def test_unmapped_policies_default_and_null(project_copy, working, policy, expected):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        "on_unmapped: error",
        policy,
    )
    _edit(project_copy, "data/lfa1.csv", "0000100004,Tailspin Packaging AG,DE", "0000100004,T,FR")
    _run(project_copy, engine, log)
    assert _rows(engine, 'select "COUNTRY" from tgt_bp_general where "PARTNER"=\'0000100004\'') == [
        (expected,)
    ]


def test_not_migrated_field_is_omitted(project_copy, working):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        'rule: { type: CONSTANT, value: "SUPL" }',
        "rule: { type: NOT_MIGRATED }",
    )
    _run(project_copy, engine, log)
    cols = [r[1] for r in _rows(engine, "pragma table_info(tgt_bp_general)")]
    assert "BU_GROUP" not in cols and "PARTNER" in cols


def test_sql_rule_and_constant_quoting(project_copy, working):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        'rule: { type: CONSTANT, value: "SUPL" }',
        'rule: { type: SQL, expression: "UPPER(s.\\"NAME1\\")" }',
    )
    _edit(
        project_copy,
        "mappings/general.yaml",
        "- target_field: LEGACY_REF",
        "- target_field: NOTE\n"
        '    rule: { type: CONSTANT, value: "it\'s" }\n'
        "  - target_field: LEGACY_REF",
    )
    _run(project_copy, engine, log)
    row = _rows(engine, 'select "BU_GROUP","NOTE" from tgt_bp_general order by "_dmc_row_no"')[0]
    assert row == ("NORTHWIND COMPONENTS GMBH", "it's")


def test_custom_sql_structure_with_jinja_names(project_copy, working):
    engine, log = working
    obj = project_copy / "objects" / "supplier"
    (obj / "sql").mkdir()
    (obj / "sql" / "de_only.sql").write_text(
        'SELECT "LIFNR" AS "PARTNER", "NAME1" AS "NAME"\n'
        "FROM {{ stg.lfa1 }}\nWHERE \"LAND1\" = 'DE';\n",
        encoding="utf-8",
    )
    _edit(
        project_copy,
        "object.yaml",
        "    mapping: mappings/general.yaml\n",
        "    mapping: mappings/general.yaml\n"
        "  - name: de_only\n    target_table: bp_de\n    source: lfa1\n"
        "    custom_sql: sql/de_only.sql\n",
    )
    _, _, results = _run(project_copy, engine, log)
    assert {r.target_table: r.rows_out for r in results} == {"tgt_bp_general": 4, "tgt_bp_de": 2}


def test_custom_sql_with_unknown_template_name_fails(project_copy, working):
    engine, log = working
    obj = project_copy / "objects" / "supplier"
    (obj / "sql").mkdir()
    (obj / "sql" / "bad.sql").write_text("SELECT 1 FROM {{ stg.nope }}", encoding="utf-8")
    _edit(
        project_copy,
        "object.yaml",
        "    mapping: mappings/general.yaml\n",
        "    mapping: mappings/general.yaml\n"
        "  - name: bad\n    target_table: bp_bad\n    source: lfa1\n"
        "    custom_sql: sql/bad.sql\n",
    )
    with pytest.raises(TransformError, match="bad.sql"):
        _run(project_copy, engine, log)


def test_transform_without_extract_asks_for_extract(example_project, working):
    engine, log = working
    project = load_project(example_project)
    with pytest.raises(TransformError, match="run extract first"):
        transform_object(
            project, "supplier", engine, log, log.start_run("p", "s"), write_build=False
        )


def test_xref_with_duplicate_legacy_value_is_rejected(project_copy, working):
    engine, log = working
    xref = project_copy / "objects" / "supplier" / "xref" / "country.csv"
    xref.write_text("legacy_value,target_value\nDE,DE\nDE,AT\n", encoding="utf-8")
    with pytest.raises(TransformError, match="mapped more than once: DE"):
        _run(project_copy, engine, log)


def test_xref_missing_column_is_rejected(project_copy, working):
    engine, log = working
    xref = project_copy / "objects" / "supplier" / "xref" / "country.csv"
    xref.write_text("legacy,target\nDE,DE\n", encoding="utf-8")
    with pytest.raises(TransformError, match="missing column"):
        _run(project_copy, engine, log)


def test_to_dialect_transpiles_for_other_databases_and_rejects_unknown():
    sql = 'SELECT SUBSTR(CAST(s."A" AS TEXT), 1, 2) AS "B" FROM "t" AS s'
    assert to_dialect(sql, "sqlite") == sql
    assert "SUBSTRING" in to_dialect(sql, "mssql")
    with pytest.raises(TransformError, match="unsupported SQL dialect"):
        to_dialect(sql, "db2")
