import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from data_migration_concept.cli import app
from data_migration_concept.config import load_project
from data_migration_concept.demo import DemoError, default_example_dir, run_demo

runner = CliRunner()


def test_demo_project_is_valid():
    project = load_project(default_example_dir())
    assert sorted(project.objects) == ["material", "supplier"]
    assert [s.name for s in project.objects["supplier"].config.structures] == ["general", "bank"]


def test_clean_demo_runs_everything_and_loads(tmp_path):
    result = run_demo(tmp_path / "demo")
    by_name = {o.name: o for o in result.objects}
    assert set(by_name) == {"material", "supplier"}
    for o in result.objects:
        assert o.overall == "OK"
        assert o.checks_passed == o.checks_total > 0
        assert o.recons_passed == o.recons_total > 0
        assert o.loaded
    out = result.project_dir / "output"
    assert (out / "supplier_general.csv").is_file()
    assert (out / "supplier_bank.xlsx").is_file()
    assert (out / "material_basic.csv").read_text(encoding="utf-8").count("\n") == 11
    assert sorted(p.name for p in (result.project_dir / "reports").iterdir()) == [
        f"{n}_report.{ext}" for n in ("material", "supplier") for ext in ("html", "json", "xlsx")
    ]
    data = json.loads(
        (result.project_dir / "reports" / "supplier_report.json").read_text(encoding="utf-8")
    )
    assert data["overall"] == "OK" and data["profiles"]


def test_defect_demo_blocks_the_load_and_explains_why(tmp_path):
    result = run_demo(tmp_path / "demo", defects=True)
    by_name = {o.name: o for o in result.objects}
    supplier, material = by_name["supplier"], by_name["material"]
    assert supplier.overall == material.overall == "BLOCKED"
    assert set(supplier.blocking_checks) == {
        "mandatory.general.NAME_ORG1",
        "partner_unique",
        "partner_is_numeric",
        "bank_partner_exists",
    }
    assert material.blocking_checks == ["weight_in_range"]
    assert not supplier.loaded and not material.loaded
    assert not (result.project_dir / "output").exists()
    data = json.loads(
        (result.project_dir / "reports" / "supplier_report.json").read_text(encoding="utf-8")
    )
    assert {r["check_name"] for r in data["rejects"]} >= {"partner_unique", "bank_partner_exists"}


def test_demo_does_not_touch_the_example_or_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DMC_WORKING_DB_URL", "sqlite:///keep-me")
    before = sorted(p.name for p in default_example_dir().rglob("*"))
    run_demo(tmp_path / "demo", defects=True)
    assert sorted(p.name for p in default_example_dir().rglob("*")) == before
    import os

    assert os.environ["DMC_WORKING_DB_URL"] == "sqlite:///keep-me"


def test_demo_refuses_to_overwrite_unless_forced_and_recognised(tmp_path):
    target = tmp_path / "demo"
    run_demo(target)
    with pytest.raises(DemoError, match="already exists"):
        run_demo(target)
    run_demo(target, force=True, defects=True)  # replaced by a defect demo
    assert "Duplicate Vendor" in (target / "objects" / "supplier" / "data" / "lfa1.csv").read_text(
        encoding="utf-8"
    )

    other = tmp_path / "my_files"
    other.mkdir()
    (other / "precious.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(DemoError, match="already exists"):
        run_demo(other, force=True)  # not a demo folder, so never deleted
    assert (other / "precious.txt").is_file()


def test_demo_needs_the_example(tmp_path):
    with pytest.raises(DemoError, match="example project not found"):
        run_demo(tmp_path / "demo", source=tmp_path / "missing")


def test_demo_command(tmp_path):
    target = tmp_path / "cli_demo"
    result = runner.invoke(app, ["demo", "--dir", str(target)])
    assert result.exit_code == 0, result.output
    assert "supplier: OK" in result.stdout and "loaded: output/supplier_bank.xlsx" in result.stdout

    again = runner.invoke(app, ["demo", "--dir", str(target)])
    assert again.exit_code == 1 and "already exists" in again.output

    bad = runner.invoke(app, ["demo", "--dir", str(target), "--force", "--defects"])
    assert bad.exit_code == 0, bad.output
    assert "LOAD BLOCKED by" in bad.stdout and Path(target / "reports").is_dir()
