import shutil

from typer.testing import CliRunner

from data_migration_concept import __version__
from data_migration_concept.cli import app

runner = CliRunner()


def test_version_command_prints_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_validate_config_ok_on_example(example_project):
    result = runner.invoke(app, ["validate-config", str(example_project)])
    assert result.exit_code == 0
    assert "1 object(s), 1 structure(s)" in result.stdout


def test_extract_command_stages_example(example_project, monkeypatch, tmp_path):
    db = tmp_path / "work.db"
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{db}")
    result = runner.invoke(app, ["extract", str(example_project), "supplier"])
    assert result.exit_code == 0, result.output
    assert "stg_supplier_lfa1: 4 row(s), 3 column(s)" in result.stdout


def test_show_sql_prints_generated_sql(example_project):
    result = runner.invoke(app, ["show-sql", str(example_project)])
    assert result.exit_code == 0, result.output
    assert 'CREATE TABLE "tgt_bp_general" AS' in result.stdout


def test_extract_then_transform_commands(example_project, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    assert runner.invoke(app, ["extract", str(example_project)]).exit_code == 0
    result = runner.invoke(app, ["transform", str(example_project)])
    assert result.exit_code == 0, result.output
    assert "tgt_bp_general: 4 row(s) from 4" in result.stdout


def test_check_command_passes_on_clean_example(example_project, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    for cmd in ("extract", "transform"):
        assert runner.invoke(app, [cmd, str(example_project)]).exit_code == 0
    result = runner.invoke(app, ["check", str(example_project)])
    assert result.exit_code == 0, result.output
    assert "supplier: OK" in result.stdout and "PASS  partner_unique" in result.stdout


def test_check_command_exits_1_on_blocking_failure(project_copy, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    data.write_text(data.read_text(encoding="utf-8") + "0000100001,Dup Ltd,DE\n", encoding="utf-8")
    for cmd in ("extract", "transform"):
        assert runner.invoke(app, [cmd, str(project_copy)]).exit_code == 0
    result = runner.invoke(app, ["check", str(project_copy)])
    assert result.exit_code == 1
    assert "BLOCKED" in result.stdout and "FAIL  partner_unique" in result.stdout


def test_load_command_writes_file_when_checks_pass(example_project, monkeypatch, tmp_path):
    project_dir = tmp_path / "proj"
    shutil.copytree(example_project, project_dir)
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    for cmd in ("extract", "transform"):
        assert runner.invoke(app, [cmd, str(project_dir)]).exit_code == 0
    result = runner.invoke(app, ["load", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "supplier: LOADED" in result.stdout
    assert "general -> output/supplier_general.csv: 4 row(s)" in result.stdout
    assert (project_dir / "output" / "supplier_general.csv").is_file()


def test_load_command_is_blocked_by_failing_checks(project_copy, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    data = project_copy / "objects" / "supplier" / "data" / "lfa1.csv"
    data.write_text(data.read_text(encoding="utf-8") + "0000100001,Dup Ltd,DE\n", encoding="utf-8")
    for cmd in ("extract", "transform"):
        assert runner.invoke(app, [cmd, str(project_copy)]).exit_code == 0
    result = runner.invoke(app, ["load", str(project_copy)])
    assert result.exit_code == 1
    assert "NOT LOADED" in result.output and "partner_unique" in result.output
    assert not (project_copy / "output").exists()


def test_recon_command_reconciled_then_differences(example_project, monkeypatch, tmp_path):
    project_dir = tmp_path / "proj"
    shutil.copytree(example_project, project_dir)
    db = tmp_path / "work.db"
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{db}")
    for cmd in ("extract", "transform"):
        assert runner.invoke(app, [cmd, str(project_dir)]).exit_code == 0
    ok = runner.invoke(app, ["recon", str(project_dir)])
    assert ok.exit_code == 0, ok.output
    assert "supplier: RECONCILED" in ok.stdout and "PASS  supplier_keys" in ok.stdout

    from sqlalchemy import create_engine, text

    with create_engine(f"sqlite:///{db}").begin() as conn:
        conn.execute(text("DELETE FROM tgt_bp_general WHERE \"PARTNER\" = '0000100004'"))
    bad = runner.invoke(app, ["recon", str(project_dir), "supplier"])
    assert bad.exit_code == 1
    assert "DIFFERENCES" in bad.stdout and "FAIL  supplier_row_count" in bad.stdout


def test_recon_command_needs_a_recon_file(project_copy, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    (project_copy / "objects" / "supplier" / "recon.yaml").unlink()
    result = runner.invoke(app, ["recon", str(project_copy)])
    assert result.exit_code == 1 and "No object has a recon.yaml" in result.output


def test_profile_command(example_project, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    assert runner.invoke(app, ["extract", str(example_project)]).exit_code == 0
    result = runner.invoke(app, ["profile", str(example_project), "supplier"])
    assert result.exit_code == 0, result.output
    assert "LIFNR: 4 rows, 0.0% empty, 4 distinct, len 10-10" in result.stdout
    bad = runner.invoke(app, ["profile", str(example_project), "--stage", "nonsense"])
    assert bad.exit_code == 2


def test_check_command_without_transform(example_project, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    result = runner.invoke(app, ["check", str(example_project)])
    assert result.exit_code == 1
    assert "run transform first" in result.output


def test_transform_command_fails_without_extract(example_project, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    result = runner.invoke(app, ["transform", str(example_project)])
    assert result.exit_code == 1
    assert "run extract first" in result.output


def test_extract_command_reports_failure(project_copy, monkeypatch, tmp_path):
    monkeypatch.setenv("DMC_WORKING_DB_URL", f"sqlite:///{tmp_path / 'work.db'}")
    (project_copy / "objects" / "supplier" / "data" / "lfa1.csv").write_text(
        "LIFNR\n1\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["extract", str(project_copy)])
    assert result.exit_code == 1
    assert "missing source column" in result.output


def test_extract_command_needs_env_var(example_project, monkeypatch):
    monkeypatch.delenv("DMC_WORKING_DB_URL", raising=False)
    result = runner.invoke(app, ["extract", str(example_project)])
    assert result.exit_code == 1
    assert "DMC_WORKING_DB_URL" in result.output


def test_validate_config_fails_with_problem_list(project_copy):
    (project_copy / "objects" / "supplier" / "xref" / "country.csv").unlink()
    result = runner.invoke(app, ["validate-config", str(project_copy)])
    assert result.exit_code == 1
    assert "xref/country.csv" in result.output
