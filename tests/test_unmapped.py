"""Target fields that are defined but not mapped yet, and key fields."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from data_migration_concept.checks import CheckStatus, run_checks
from data_migration_concept.config import load_project
from data_migration_concept.config.models import UnmappedRule
from data_migration_concept.extract import extract_object
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform import generate_object, transform_object


@pytest.fixture
def working():
    engine = create_engine("sqlite://")
    log = RunLog(engine)
    log.ensure_schema()
    return engine, log


def _edit(project_dir: Path, rel: str, old: str, new: str):
    path = project_dir / "objects" / "supplier" / rel
    content = path.read_text(encoding="utf-8")
    assert old in content, f"{old!r} not in {rel}"
    path.write_text(content.replace(old, new), encoding="utf-8")


def _build(project_dir: Path, engine, log):
    project = load_project(project_dir)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    transform_object(project, "supplier", engine, log, run_id, write_build=False)
    results = run_checks(project, "supplier", engine, log, log.start_run("p", "supplier"))
    return project, {r.name: r for r in results}


def test_unmapped_field_becomes_an_empty_column_in_template_order(project_copy, working):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        "  - target_field: SEARCH_TERM",
        "  - target_field: EXTRA\n    description: Not mapped yet\n    rule: { type: UNMAPPED }\n"
        "  - target_field: SEARCH_TERM",
    )
    project, _ = _build(project_copy, engine, log)
    rule = project.objects["supplier"].mappings["general"].fields[4].rule
    assert isinstance(rule, UnmappedRule)
    sql = generate_object(project, project.objects["supplier"])[0].render()
    assert 'CAST(NULL AS TEXT) AS "EXTRA"' in sql
    with engine.connect() as conn:
        columns = [r[1] for r in conn.execute(text("pragma table_info(tgt_bp_general)"))]
        values = conn.execute(text('select "EXTRA" from tgt_bp_general')).scalars().all()
    assert columns.index("EXTRA") == columns.index("COUNTRY") + 1  # keeps the template order
    assert values == [None] * 4


def test_mandatory_field_left_unmapped_is_flagged_by_the_checks(project_copy, working):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        "  - target_field: SEARCH_TERM",
        "  - target_field: EXTRA\n    mandatory: true\n    rule: { type: UNMAPPED }\n"
        "  - target_field: SEARCH_TERM",
    )
    _, results = _build(project_copy, engine, log)
    check = results["mandatory.general.EXTRA"]
    assert (check.status, check.failed_count, check.blocking) == (CheckStatus.FAIL, 4, True)


def test_key_fields_get_a_uniqueness_check(project_copy, working):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        "    mandatory: true\n    length: 10",
        "    mandatory: true\n    key: true\n    length: 10",
    )
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    data.write_text(data.read_text(encoding="utf-8") + "0000100001,Twin,DE\n", encoding="utf-8")
    _, results = _build(project_copy, engine, log)
    assert "key.general" in results
    key = results["key.general"]
    assert (key.status, key.failed_count) == (CheckStatus.FAIL, 2)
    assert "PARTNER=0000100001" in key.failures[0].message


def test_no_key_flag_means_no_key_check(project_copy, working):
    engine, log = working
    _, results = _build(project_copy, engine, log)
    assert not any(name.startswith("key.") for name in results)
