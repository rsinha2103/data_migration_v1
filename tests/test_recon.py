from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from data_migration_concept.checks import CheckRunError, CheckStatus
from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.extract import extract_object
from data_migration_concept.recon import run_recons
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
    content = path.read_text(encoding="utf-8")
    assert old in content, f"{old!r} not in {rel}"
    path.write_text(content.replace(old, new), encoding="utf-8")


def _set_recons(project_dir: Path, yaml_text: str):
    (project_dir / "objects" / "supplier" / "recon.yaml").write_text(
        "recons:\n" + yaml_text, encoding="utf-8"
    )


def _prepare(project_dir: Path, engine, log):
    project = load_project(project_dir)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    transform_object(project, "supplier", engine, log, run_id, write_build=False)
    return project


def _recon(project, engine, log):
    run_id = log.start_run("p", "supplier")
    results = run_recons(project, "supplier", engine, log, run_id)
    return {r.name: r for r in results}, run_id


def _sql(engine, statement: str):
    with engine.begin() as conn:
        conn.execute(text(statement))


def test_example_reconciles_cleanly_and_is_persisted(example_project, working):
    engine, log = working
    project = _prepare(example_project, engine, log)
    results, run_id = _recon(project, engine, log)
    assert set(results) == {
        "supplier_row_count",
        "supplier_keys",
        "supplier_control_total",
        "supplier_values",
    }
    assert all(r.status == CheckStatus.PASS for r in results.values())
    stored = {c["check_name"]: c for c in log.get_checks(run_id)}
    assert stored["supplier_keys"]["check_type"] == "RECON_KEYS"
    assert log.get_steps(run_id)[0]["step_name"] == "recon"


def test_count_difference_and_tolerance(project_copy, working):
    engine, log = working
    project = _prepare(project_copy, engine, log)
    _sql(engine, "DELETE FROM tgt_bp_general WHERE \"PARTNER\" = '0000100004'")
    results, _ = _recon(project, engine, log)
    r = results["supplier_row_count"]
    assert (r.status, r.blocking) == (CheckStatus.FAIL, True)
    assert "target has 3" in r.failures[0].message and "-1" in r.failures[0].message

    _set_recons(
        project_copy,
        "  - name: supplier_row_count\n    structure: general\n    type: COUNT\n    tolerance: 1\n",
    )
    results, _ = _recon(load_project(project_copy), engine, log)
    assert results["supplier_row_count"].status == CheckStatus.PASS


def test_intentional_exclusions_are_accounted_for(project_copy, working):
    engine, log = working
    project = _prepare(project_copy, engine, log)
    _sql(engine, "DELETE FROM tgt_bp_general WHERE \"COUNTRY\" = 'US'")  # 1 US supplier
    results, _ = _recon(project, engine, log)
    assert results["supplier_row_count"].status == CheckStatus.FAIL

    _set_recons(
        project_copy,
        "  - name: c\n    structure: general\n    type: COUNT\n"
        '    exclude_source: "\\"LAND1\\" = \'US\'"\n'
        "  - name: k\n    structure: general\n    type: KEYS\n"
        "    source_key: [LIFNR]\n    target_key: [PARTNER]\n"
        '    exclude_source: "\\"LAND1\\" = \'US\'"\n',
    )
    results, _ = _recon(load_project(project_copy), engine, log)
    assert results["c"].status == CheckStatus.PASS
    assert results["k"].status == CheckStatus.PASS


def test_keys_report_missing_and_extra_rows(project_copy, working):
    engine, log = working
    project = _prepare(project_copy, engine, log)
    _sql(engine, "DELETE FROM tgt_bp_general WHERE \"PARTNER\" = '0000100002'")
    _sql(
        engine,
        'INSERT INTO tgt_bp_general (_dmc_row_no, _dmc_run_id, "PARTNER") '
        "VALUES (77, 'x', '0000999999')",
    )
    results, _ = _recon(project, engine, log)
    r = results["supplier_keys"]
    got = sorted((f.row_no, f.value, f.message.split(" is ", 1)[1]) for f in r.failures)
    assert got == [
        (2, "0000100002", "in the source but missing in the target"),
        (77, "0000999999", "in the target but not in the source"),
    ]


def test_totals_with_grouping_and_tolerance(project_copy, working):
    engine, log = working
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    lines = data.read_text(encoding="utf-8").splitlines()
    amounts = ["10.50", "20", "30", "40"]
    data.write_text(
        "\n".join(
            [lines[0] + ",AMT"] + [f"{ln},{a}" for ln, a in zip(lines[1:], amounts, strict=True)]
        )
        + "\n",
        encoding="utf-8",
    )
    _edit(
        project_copy,
        "mappings/general.yaml",
        "  - target_field: LEGACY_REF",
        "  - target_field: AMOUNT\n    rule: { type: MOVE, source_field: AMT }\n"
        "  - target_field: LEGACY_REF",
    )
    _set_recons(
        project_copy,
        "  - name: total\n    structure: general\n    type: TOTALS\n"
        "    source_field: AMT\n    target_field: AMOUNT\n"
        "  - name: by_country\n    structure: general\n    type: TOTALS\n"
        "    source_field: AMT\n    target_field: AMOUNT\n"
        "    group_by_source: [LAND1]\n    group_by_target: [COUNTRY]\n",
    )
    project = _prepare(project_copy, engine, log)
    results, _ = _recon(project, engine, log)
    assert results["total"].status == CheckStatus.PASS
    # LAND1 UK became GB through the XREF, so the per-country totals legitimately differ
    r = results["by_country"]
    assert r.status == CheckStatus.FAIL
    assert any("group UK" in f.message for f in r.failures)
    assert any("group GB" in f.message for f in r.failures)

    _sql(engine, "UPDATE tgt_bp_general SET \"AMOUNT\" = '31' WHERE \"PARTNER\" = '0000100003'")
    results, _ = _recon(project, engine, log)
    f = results["total"].failures[0]
    assert f.value == "100.50 vs 101.50"

    _set_recons(
        project_copy,
        "  - name: total\n    structure: general\n    type: TOTALS\n"
        "    source_field: AMT\n    target_field: AMOUNT\n    tolerance: 1\n",
    )
    results, _ = _recon(load_project(project_copy), engine, log)
    assert results["total"].status == CheckStatus.PASS


def test_totals_count_and_non_numeric_sum(project_copy, working):
    engine, log = working
    _prepare(project_copy, engine, log)
    _set_recons(
        project_copy,
        "  - name: names_filled\n    structure: general\n    type: TOTALS\n"
        "    source_field: NAME1\n    target_field: NAME_ORG1\n    agg: count\n"
        "  - name: bad_sum\n    structure: general\n    type: TOTALS\n"
        "    source_field: NAME1\n    target_field: NAME_ORG1\n",
    )
    results, _ = _recon(load_project(project_copy), engine, log)
    assert results["names_filled"].status == CheckStatus.PASS
    assert results["bad_sum"].status == CheckStatus.ERROR
    assert "non-numeric" in results["bad_sum"].message


def test_fields_auto_finds_changed_values(project_copy, working):
    engine, log = working
    project = _prepare(project_copy, engine, log)
    _sql(
        engine,
        "UPDATE tgt_bp_general SET \"NAME_ORG1\" = 'Changed' WHERE \"PARTNER\" = '0000100001'",
    )
    results, _ = _recon(project, engine, log)
    r = results["supplier_values"]
    assert [(f.row_no, f.field) for f in r.failures] == [(1, "NAME1 -> NAME_ORG1")]
    assert (
        "'Northwind Components GmbH'" in r.failures[0].message
        and "'Changed'" in r.failures[0].message
    )


def test_fields_explicit_pairs_case_and_whitespace_options(project_copy, working):
    engine, log = working
    _prepare(project_copy, engine, log)
    _sql(
        engine,
        "UPDATE tgt_bp_general SET \"NAME_ORG1\" = '  CONTOSO LOGISTICS LTD ' "
        "WHERE \"PARTNER\" = '0000100002'",
    )
    base = (
        "  - name: v\n    structure: general\n    type: FIELDS\n"
        "    source_key: [LIFNR]\n    target_key: [PARTNER]\n"
        "    fields: [{source: NAME1, target: NAME_ORG1}]\n"
    )
    _set_recons(project_copy, base)
    results, _ = _recon(load_project(project_copy), engine, log)
    assert results["v"].status == CheckStatus.FAIL  # case differs

    _set_recons(project_copy, base + "    ignore_case: true\n")
    results, _ = _recon(load_project(project_copy), engine, log)
    assert results["v"].status == CheckStatus.PASS


def test_numeric_tolerance_applies_to_fields(project_copy, working):
    engine, log = working
    _prepare(project_copy, engine, log)
    _sql(
        engine,
        "UPDATE tgt_bp_general SET \"SEARCH_TERM\" = '10.004' WHERE \"PARTNER\" = '0000100001'",
    )
    _sql(engine, "UPDATE stg_supplier_lfa1 SET \"NAME1\" = '10.001' WHERE \"LIFNR\" = '0000100001'")
    spec = (
        "  - name: v\n    structure: general\n    type: FIELDS\n"
        "    source_key: [LIFNR]\n    target_key: [PARTNER]\n"
        "    fields: [{source: NAME1, target: SEARCH_TERM}]\n"
    )
    _set_recons(project_copy, spec)
    results, _ = _recon(load_project(project_copy), engine, log)
    assert 1 in {f.row_no for f in results["v"].failures}

    _set_recons(project_copy, spec + "    tolerance: 0.01\n")
    results, _ = _recon(load_project(project_copy), engine, log)
    # row 1 is within tolerance now; the other rows are plain text that really differs
    assert 1 not in {f.row_no for f in results["v"].failures}


def test_compare_against_another_table(project_copy, working):
    engine, log = working
    project = _prepare(project_copy, engine, log)
    _sql(
        engine,
        "CREATE TABLE s4_export AS SELECT * FROM tgt_bp_general WHERE \"PARTNER\" <> '0000100004'",
    )
    _set_recons(
        project_copy,
        "  - name: vs_export\n    structure: general\n    type: KEYS\n"
        "    source_key: [LIFNR]\n    target_key: [PARTNER]\n    target_table: s4_export\n",
    )
    project = load_project(project_copy)
    results, _ = _recon(project, engine, log)
    assert [f.value for f in results["vs_export"].failures] == ["0000100004"]

    _sql(engine, "DROP TABLE s4_export")
    with pytest.raises(CheckRunError, match="s4_export.*load or import"):
        _recon(project, engine, log)


def test_warn_severity_does_not_block(project_copy, working):
    engine, log = working
    _set_recons(
        project_copy,
        "  - name: c\n    structure: general\n    type: COUNT\n    severity: warn\n",
    )
    project = _prepare(project_copy, engine, log)
    _sql(engine, "DELETE FROM tgt_bp_general")
    results, _ = _recon(project, engine, log)
    assert (results["c"].status, results["c"].blocking) == (CheckStatus.FAIL, False)


def test_unknown_source_column_is_an_error_result(project_copy, working):
    engine, log = working
    _set_recons(
        project_copy,
        "  - name: k\n    structure: general\n    type: KEYS\n"
        "    source_key: [NOPE]\n    target_key: [PARTNER]\n",
    )
    project = _prepare(project_copy, engine, log)
    results, _ = _recon(project, engine, log)
    assert results["k"].status == CheckStatus.ERROR and "NOPE" in results["k"].message


def test_missing_tables_and_missing_file(project_copy, working):
    engine, log = working
    project = load_project(project_copy)
    with pytest.raises(CheckRunError, match="extract"):
        _recon(project, engine, log)
    (project_copy / "objects" / "supplier" / "recon.yaml").unlink()
    with pytest.raises(CheckRunError, match="no recon.yaml"):
        _recon(load_project(project_copy), engine, log)


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        (
            "type: KEYS\n    source_key: [A, B]\n    target_key: [PARTNER]\n",
            "same number of fields",
        ),
        ("type: KEYS\n    source_key: [LIFNR]\n    target_key: [NOPE]\n", "no target field 'NOPE'"),
        (
            "type: TOTALS\n    source_field: A\n    target_field: B\n    group_by_source: [X]\n",
            "same length",
        ),
        ("type: COUNT\n    structure_typo: 1\n", "Extra inputs"),
    ],
)
def test_recon_config_is_validated(project_copy, snippet, message):
    _set_recons(project_copy, f"  - name: bad\n    structure: general\n    {snippet}")
    with pytest.raises(ConfigError, match=message):
        load_project(project_copy)


def test_recon_unknown_structure_and_duplicate_names(project_copy):
    _set_recons(project_copy, "  - name: a\n    structure: nope\n    type: COUNT\n")
    with pytest.raises(ConfigError, match="unknown structure 'nope'"):
        load_project(project_copy)
    _set_recons(
        project_copy,
        "  - name: a\n    structure: general\n    type: COUNT\n"
        "  - name: a\n    structure: general\n    type: COUNT\n",
    )
    with pytest.raises(ConfigError, match="duplicate reconciliation name"):
        load_project(project_copy)
