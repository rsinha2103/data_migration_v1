import json
from pathlib import Path

import polars as pl
import pytest
from sqlalchemy import create_engine

from data_migration_concept.checks import CheckStatus, run_checks
from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.extract import extract_object
from data_migration_concept.profile import ProfileError, profile_column, profile_object
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


def _append_checks(project_dir: Path, yaml_text: str):
    with (project_dir / "objects" / "supplier" / "checks.yaml").open("a", encoding="utf-8") as fh:
        fh.write(yaml_text)


def _add_amount(project_dir: Path, amounts: list[str]):
    data = project_dir / "objects" / "supplier" / "data" / "lfa1.csv"
    lines = data.read_text(encoding="utf-8").splitlines()
    out = [lines[0] + ",AMT"] + [f"{line},{a}" for line, a in zip(lines[1:], amounts, strict=True)]
    data.write_text("\n".join(out) + "\n", encoding="utf-8")
    _edit(
        project_dir,
        "mappings/general.yaml",
        "  - target_field: LEGACY_REF",
        "  - target_field: AMOUNT\n    data_type: decimal\n"
        "    rule: { type: MOVE, source_field: AMT }\n"
        "  - target_field: LEGACY_REF",
    )


def _run(project_dir: Path, engine, log):
    project = load_project(project_dir)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    transform_object(project, "supplier", engine, log, run_id, write_build=False)
    results = run_checks(project, "supplier", engine, log, log.start_run("p", "supplier"))
    return {r.name: r for r in results}, project, run_id


def test_null_rate_check(project_copy, working):
    engine, log = working
    _edit(project_copy, "data/lfa1.csv", "Contoso Logistics Ltd", "")
    _append_checks(
        project_copy,
        "  - name: names_mostly_filled\n    structure: general\n    type: NULL_RATE\n"
        "    field: NAME_ORG1\n    max_pct: 20\n",
    )
    results, _, _ = _run(project_copy, engine, log)
    r = results["names_mostly_filled"]
    assert (r.status, r.failed_count) == (CheckStatus.FAIL, 1)
    assert "25.0%" in r.failures[0].message

    _edit(project_copy, "checks.yaml", "max_pct: 20", "max_pct: 25")
    results, _, _ = _run(project_copy, engine, log)
    assert results["names_mostly_filled"].status == CheckStatus.PASS


def test_pattern_check_flags_non_matching_values(project_copy, working):
    engine, log = working
    _edit(project_copy, "data/lfa1.csv", "0000100003", "ABC0100003")
    results, _, _ = _run(project_copy, engine, log)
    r = results["partner_is_numeric"]
    assert [(f.row_no, f.value) for f in r.failures] == [(3, "ABC0100003")]


def test_range_check(project_copy, working):
    engine, log = working
    _add_amount(project_copy, ["5", "50", "500", "abc"])
    _append_checks(
        project_copy,
        "  - name: amount_range\n    structure: general\n    type: RANGE\n"
        "    field: AMOUNT\n    min: 10\n    max: 100\n",
    )
    results, _, _ = _run(project_copy, engine, log)
    r = results["amount_range"]
    assert [(f.row_no, f.value) for f in r.failures] == [(1, "5"), (3, "500"), (4, "abc")]
    assert "not a number" in r.failures[2].message


def test_outlier_check(project_copy, working):
    engine, log = working
    _add_amount(project_copy, ["10", "10", "10", "1000"])
    _append_checks(
        project_copy,
        "  - name: amount_outlier\n    structure: general\n    type: OUTLIER\n"
        "    field: AMOUNT\n    max_zscore: 1.2\n    severity: warn\n",
    )
    results, _, _ = _run(project_copy, engine, log)
    r = results["amount_outlier"]
    assert [(f.row_no, f.value) for f in r.failures] == [(4, "1000")]
    assert not r.blocking


def test_outlier_check_ignores_constant_and_tiny_data(project_copy, working):
    engine, log = working
    _add_amount(project_copy, ["7", "7", "7", "7"])
    _append_checks(
        project_copy,
        "  - name: amount_outlier\n    structure: general\n    type: OUTLIER\n    field: AMOUNT\n",
    )
    results, _, _ = _run(project_copy, engine, log)
    assert results["amount_outlier"].status == CheckStatus.PASS


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("type: PATTERN\n    field: PARTNER\n    pattern: '('\n", "invalid regular expression"),
        ("type: RANGE\n    field: PARTNER\n", "at least one of 'min' or 'max'"),
        ("type: RANGE\n    field: PARTNER\n    min: 5\n    max: 1\n", "must not be greater"),
        ("type: NULL_RATE\n    field: PARTNER\n    max_pct: 150\n", "less than or equal to 100"),
        ("type: OUTLIER\n    field: NOPE\n", "no target field 'NOPE'"),
    ],
)
def test_dq_check_config_is_validated(project_copy, snippet, message):
    _append_checks(project_copy, f"  - name: bad\n    structure: general\n    {snippet}")
    with pytest.raises(ConfigError, match=message):
        load_project(project_copy)


def test_profile_staging_table_and_persist(example_project, working):
    engine, log = working
    project = load_project(example_project)
    extract_object(project, "supplier", engine, log, log.start_run("p", "supplier"))
    run_id = log.start_run("p", "supplier")
    result = profile_object(project, "supplier", engine, log, run_id)
    cols = {c.column: c for c in result["stg_supplier_lfa1"]}
    assert list(cols) == ["LIFNR", "NAME1", "LAND1"]  # lineage columns excluded
    lifnr, land = cols["LIFNR"], cols["LAND1"]
    assert (lifnr.row_count, lifnr.empty_count, lifnr.distinct_count) == (4, 0, 4)
    assert (lifnr.min_length, lifnr.max_length) == (10, 10)
    assert lifnr.top_patterns == [("9999999999", 4)]
    assert land.distinct_count == 3 and land.top_values[0] == ("DE", 2)
    assert land.top_patterns == [("AA", 4)]

    stored = log.get_profile(run_id, "stg_supplier_lfa1")
    assert [s["column_name"] for s in stored] == ["LIFNR", "NAME1", "LAND1"]
    assert json.loads(stored[2]["top_values"])[0] == ["DE", 2]
    assert log.get_steps(run_id)[0]["step_name"] == "profile:stg_supplier_lfa1"


def test_profile_target_stage_needs_transform(example_project, working):
    engine, log = working
    project = load_project(example_project)
    extract_object(project, "supplier", engine, log, log.start_run("p", "supplier"))
    with pytest.raises(ProfileError, match="run transform first"):
        profile_object(project, "supplier", engine, log, log.start_run("p", "s"), stage="target")
    with pytest.raises(ProfileError, match="unknown object"):
        profile_object(project, "nope", engine, log, log.start_run("p", "s"))


def test_profile_missing_staging_asks_for_extract(example_project, working):
    engine, log = working
    project = load_project(example_project)
    with pytest.raises(ProfileError, match="run extract first"):
        profile_object(project, "supplier", engine, log, log.start_run("p", "s"))


def test_profile_column_handles_numbers_blanks_and_nulls():
    p = profile_column(pl.Series("x", [3, 1, None, 2, 2]))
    assert (p.row_count, p.empty_count, p.distinct_count) == (5, 1, 3)
    assert (p.min_value, p.max_value) == ("1", "3")
    assert p.empty_pct == 20.0

    text = profile_column(pl.Series("t", ["Ab1", " ", None, "Cd2"]))
    assert text.empty_count == 2
    assert text.top_patterns == [("Aa9", 2)]

    empty = profile_column(pl.Series("e", [], dtype=pl.Utf8))
    assert (empty.row_count, empty.min_value, empty.min_length, empty.top_values) == (
        0,
        None,
        None,
        [],
    )
