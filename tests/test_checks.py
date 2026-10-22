from pathlib import Path

import pytest
from sqlalchemy import create_engine

from data_migration_concept.checks import (
    CheckResult,
    CheckRunError,
    CheckStatus,
    blocking_failures,
    run_checks,
)
from data_migration_concept.checks import base as checks_base
from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.extract import extract_object
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform import transform_object


@pytest.fixture
def working():
    engine = create_engine("sqlite://")
    log = RunLog(engine)
    log.ensure_schema()
    return engine, log


def _edit(project_dir: Path, rel: str, old: str, new: str):
    path = project_dir / "objects" / "supplier" / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _pipeline(project_dir: Path, engine, log):
    project = load_project(project_dir)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    transform_object(project, "supplier", engine, log, run_id, write_build=False)
    check_run = log.start_run("p", "supplier")
    return run_checks(project, "supplier", engine, log, check_run), check_run


def _by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.name: r for r in results}


def test_example_passes_every_check_and_results_are_persisted(example_project, working):
    engine, log = working
    results, run_id = _pipeline(example_project, engine, log)
    assert results and all(r.status == CheckStatus.PASS for r in results)
    assert not blocking_failures(results)
    stored = log.get_checks(run_id)
    assert len(stored) == len(results)
    assert {c["status"] for c in stored} == {"PASS"}
    assert stored[0]["checked_count"] == 4
    step = log.get_steps(run_id)[0]
    assert step["step_name"] == "checks" and step["status"] == "SUCCESS"


def test_mandatory_failure_points_at_the_source_row(project_copy, working):
    engine, log = working
    _edit(project_copy, "data/lfa1.csv", "0000100003,Fabrikam Industrial Supply", "0000100003,")
    results, run_id = _pipeline(project_copy, engine, log)
    r = _by_name(results)["mandatory.general.NAME_ORG1"]
    assert (r.status, r.failed_count, r.blocking) == (CheckStatus.FAIL, 1, True)
    assert r.failures[0].row_no == 3
    rejects = log.get_rejects(run_id, "mandatory.general.NAME_ORG1")
    assert [(x["row_no"], x["field"]) for x in rejects] == [(3, "NAME_ORG1")]
    assert log.get_steps(run_id)[0]["status"] == "FAILED"


def test_length_check(project_copy, working):
    engine, log = working
    _edit(project_copy, "data/lfa1.csv", "0000100001,", "00001000019,")
    results, _ = _pipeline(project_copy, engine, log)
    r = _by_name(results)["length.general.PARTNER"]
    assert (r.failed_count, r.failures[0].value) == (1, "00001000019")


def test_data_type_check_reports_bad_values(project_copy, working):
    engine, log = working
    _edit(
        project_copy,
        "mappings/general.yaml",
        "mandatory: true\n    length: 10",
        "data_type: integer\n    length: 10",
    )
    _edit(project_copy, "data/lfa1.csv", "0000100002,", "10X,")
    results, _ = _pipeline(project_copy, engine, log)
    r = _by_name(results)["datatype.general.PARTNER"]
    assert [(f.row_no, f.value) for f in r.failures] == [(2, "10X")]


@pytest.mark.parametrize(
    ("data_type", "good", "bad"),
    [
        ("decimal", "12.50", "12,5"),
        ("date", "2026-10-22", "22.10.2026"),
        ("boolean", "true", "maybe"),
    ],
)
def test_data_type_validators(data_type, good, bad):
    from data_migration_concept.checks.builtin import TYPE_VALIDATORS

    valid, _ = TYPE_VALIDATORS[data_type]
    assert valid(good) and not valid(bad)


def test_unique_check_flags_every_duplicate_row(project_copy, working):
    engine, log = working
    _edit(project_copy, "data/lfa1.csv", "0000100002,", "0000100001,")
    results, _ = _pipeline(project_copy, engine, log)
    r = _by_name(results)["partner_unique"]
    assert [f.row_no for f in r.failures] == [1, 2]
    assert "PARTNER=0000100001" in r.failures[0].message


def test_warn_severity_fails_without_blocking(project_copy, working):
    engine, log = working
    _edit(
        project_copy, "checks.yaml", "values: [DE, GB, US]", "values: [DE, US]\n    severity: warn"
    )
    results, run_id = _pipeline(project_copy, engine, log)
    r = _by_name(results)["country_allowed"]
    assert (r.status, r.failed_count, r.blocking) == (CheckStatus.FAIL, 1, False)
    assert not blocking_failures(results)
    assert log.get_steps(run_id)[0]["status"] == "SUCCESS"


def _add_roles_structure(project_dir: Path, extra_check: str):
    obj = project_dir / "objects" / "supplier"
    (obj / "sql").mkdir(exist_ok=True)
    (obj / "sql" / "roles.sql").write_text(
        'SELECT "_dmc_row_no", "_dmc_run_id", "LIFNR" AS "PARTNER" FROM {{ stg.lfa1 }}\n'
        "UNION ALL\n"
        "SELECT 99, 'x', 'ZZZ'\n",
        encoding="utf-8",
    )
    _edit(
        project_dir,
        "object.yaml",
        "    mapping: mappings/general.yaml\n",
        "    mapping: mappings/general.yaml\n"
        "  - name: roles\n    target_table: bp_roles\n    source: lfa1\n"
        "    custom_sql: sql/roles.sql\n",
    )
    with (obj / "checks.yaml").open("a", encoding="utf-8") as fh:
        fh.write(extra_check)


def test_referential_check_finds_orphans(project_copy, working):
    engine, log = working
    _add_roles_structure(
        project_copy,
        "  - name: roles_partner_exists\n    structure: roles\n    type: REFERENTIAL\n"
        "    field: PARTNER\n    ref_structure: general\n    ref_field: PARTNER\n",
    )
    results, _ = _pipeline(project_copy, engine, log)
    r = _by_name(results)["roles_partner_exists"]
    assert [(f.row_no, f.value) for f in r.failures] == [(99, "ZZZ")]


def test_check_on_missing_column_of_custom_sql_structure_is_an_error_result(project_copy, working):
    engine, log = working
    _add_roles_structure(
        project_copy,
        "  - name: roles_ghost\n    structure: roles\n    type: UNIQUE\n    fields: [GHOST]\n",
    )
    results, _ = _pipeline(project_copy, engine, log)
    r = _by_name(results)["roles_ghost"]
    assert (r.status, r.blocking) == (CheckStatus.ERROR, True)
    assert "GHOST" in r.message


def test_broken_sql_assert_is_an_error_result_not_a_crash(project_copy, working):
    engine, log = working
    _edit(project_copy, "checks.yaml", "WHERE LENGTH", "WHERE NOPE_FN(")
    results, _ = _pipeline(project_copy, engine, log)
    r = _by_name(results)["name_not_too_short"]
    assert r.status == CheckStatus.ERROR and r.blocking


def test_rejects_are_capped_but_counts_are_exact(project_copy, working, monkeypatch):
    engine, log = working
    monkeypatch.setattr(checks_base, "MAX_REJECTS_PER_CHECK", 2)
    _edit(project_copy, "checks.yaml", "values: [DE, GB, US]", "values: [XX]")
    results, run_id = _pipeline(project_copy, engine, log)
    r = _by_name(results)["country_allowed"]
    assert (r.failed_count, len(r.failures), r.truncated) == (4, 2, True)
    assert len(log.get_rejects(run_id, "country_allowed")) == 2
    stored = {c["check_name"]: c for c in log.get_checks(run_id)}
    assert stored["country_allowed"]["truncated"] in (True, 1)
    assert stored["country_allowed"]["failed_count"] == 4


def test_checks_need_transformed_tables(example_project, working):
    engine, log = working
    project = load_project(example_project)
    with pytest.raises(CheckRunError, match="run transform first"):
        run_checks(project, "supplier", engine, log, log.start_run("p", "supplier"))


def test_unknown_object(example_project, working):
    engine, log = working
    project = load_project(example_project)
    with pytest.raises(CheckRunError, match="unknown object"):
        run_checks(project, "nope", engine, log, log.start_run("p", "nope"))


def test_checks_yaml_is_validated_at_load_time(project_copy):
    _edit(project_copy, "checks.yaml", "fields: [PARTNER]", "fields: [NOT_A_FIELD]")
    with pytest.raises(ConfigError, match="no target field 'NOT_A_FIELD'"):
        load_project(project_copy)


def test_checks_yaml_unknown_structure_and_duplicate_names(project_copy):
    _edit(
        project_copy,
        "checks.yaml",
        "structure: general\n    type: UNIQUE",
        "structure: nope\n    type: UNIQUE",
    )
    with pytest.raises(ConfigError, match="unknown structure 'nope'"):
        load_project(project_copy)
    _edit(project_copy, "checks.yaml", "name: country_allowed", "name: partner_unique")
    with pytest.raises(ConfigError, match="duplicate check name"):
        load_project(project_copy)


def test_referential_check_needs_existing_ref_structure(project_copy):
    with (project_copy / "objects" / "supplier" / "checks.yaml").open("a", encoding="utf-8") as fh:
        fh.write(
            "  - name: bad_ref\n    structure: general\n    type: REFERENTIAL\n"
            "    field: PARTNER\n    ref_structure: ghost\n    ref_field: X\n"
        )
    with pytest.raises(ConfigError, match="unknown ref_structure 'ghost'"):
        load_project(project_copy)
