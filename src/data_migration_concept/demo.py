"""`dmc demo`: copy the fictitious example project and run the whole pipeline on it."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from data_migration_concept.checks import blocking_failures, run_checks
from data_migration_concept.config import load_project
from data_migration_concept.connectors import get_working_engine
from data_migration_concept.extract import extract_object
from data_migration_concept.load import LoadBlockedError, load_object
from data_migration_concept.profile import profile_object
from data_migration_concept.recon import run_recons
from data_migration_concept.report import collect_report, write_reports
from data_migration_concept.runlog import RunLog, RunStatus
from data_migration_concept.transform import transform_object

ENV_VAR = "DMC_WORKING_DB_URL"


class DemoError(Exception):
    """The demo cannot run (example not found, target folder in the way)."""


@dataclass
class ObjectOutcome:
    name: str
    overall: str
    checks_passed: int
    checks_total: int
    recons_passed: int
    recons_total: int
    blocking_checks: list[str] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)
    reports: list[Path] = field(default_factory=list)


@dataclass
class DemoResult:
    project_dir: Path
    database: Path
    objects: list[ObjectOutcome]


def default_example_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "acme_demo"


def seed_defects(project_dir: Path) -> None:
    """Add realistic problems to the copied data. They pass the transform step but fail
    checks, so the load is blocked and the reports have something to show."""
    supplier = project_dir / "objects" / "supplier" / "data"
    with (supplier / "lfa1.csv").open("a", encoding="utf-8", newline="") as fh:
        fh.write("0000100001,Duplicate Vendor Ltd,Berlin,DE,Z030\n")  # duplicate key
        fh.write("0000100013,,Madrid,DE,Z030\n")  # missing name
        fh.write("ABCDEFGH14,Bad Key Co,Oslo,US,Z060\n")  # key is not numeric
    wb = load_workbook(supplier / "lfbk.xlsx")
    wb["LFBK"].append(["0000100099", "DE", "10020030", "0001112223", "Ghost Supplier"])
    wb.save(supplier / "lfbk.xlsx")  # bank account of an unknown supplier
    with (project_dir / "objects" / "material" / "data" / "mara.csv").open(
        "a", encoding="utf-8", newline=""
    ) as fh:
        fh.write("000000000000003001,Heavy casting,ST,ROH,5000\n")  # weight out of range


@contextmanager
def _env(name: str, value: str) -> Iterator[None]:
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def prepare_target(source: Path, target: Path, *, force: bool, defects: bool) -> None:
    if not (source / "project.yaml").is_file():
        raise DemoError(
            f"example project not found at {source}; run from a clone of the repository "
            "or pass --source"
        )
    if target.exists() and any(target.iterdir()):
        if not (force and (target / "project.yaml").is_file()):
            raise DemoError(
                f"{target} already exists; choose another --dir, or use --force to replace "
                "a previous demo folder"
            )
        shutil.rmtree(target)
    shutil.copytree(source, target)
    if defects:
        seed_defects(target)


def run_demo(
    target: Path, *, source: Path | None = None, defects: bool = False, force: bool = False
) -> DemoResult:
    target = target.resolve()
    prepare_target(source or default_example_dir(), target, force=force, defects=defects)
    database = target / "work.db"

    with _env(ENV_VAR, f"sqlite:///{database.as_posix()}"):
        project = load_project(target)
        engine = get_working_engine(project.config)
        runlog = RunLog(engine)
        runlog.ensure_schema()
        outcomes: list[ObjectOutcome] = []
        for name in sorted(project.objects):
            pname = project.config.name

            def new_run(name: str = name, pname: str = pname) -> str:
                return runlog.start_run(pname, name)

            extract_object(project, name, engine, runlog, new_run())
            transform_object(project, name, engine, runlog, new_run())
            run_id = new_run()
            blocking = blocking_failures(run_checks(project, name, engine, runlog, run_id))
            runlog.finish_run(run_id, RunStatus.FAILED if blocking else RunStatus.SUCCESS)
            loaded: list[str] = []
            if not blocking:
                try:
                    loaded = [
                        f"{r.destination} ({r.rows} rows)"
                        for r in load_object(project, name, engine, runlog, new_run())
                    ]
                except LoadBlockedError:  # pragma: no cover - checks just passed
                    loaded = []
            run_recons(project, name, engine, runlog, new_run())
            profile_object(project, name, engine, runlog, new_run())

            data = collect_report(runlog, pname, name)
            files = write_reports(data, target / "reports", ("xlsx", "html", "json"))
            s = data["summary"]
            outcomes.append(
                ObjectOutcome(
                    name=name,
                    overall=data["overall"],
                    checks_passed=s["checks_passed"],
                    checks_total=s["checks_total"],
                    recons_passed=s["recons_passed"],
                    recons_total=s["recons_total"],
                    blocking_checks=[r.name for r in blocking],
                    loaded=loaded,
                    reports=files,
                )
            )
        engine.dispose()
    return DemoResult(project_dir=target, database=database, objects=outcomes)
