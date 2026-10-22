"""Command line entry point (`dmc`)."""

from pathlib import Path
from typing import Annotated

import typer

from data_migration_concept import __version__
from data_migration_concept.checks import (
    CheckResult,
    CheckRunError,
    CheckStatus,
    blocking_failures,
    run_checks,
)
from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.connectors import get_working_engine
from data_migration_concept.demo import DemoError, run_demo
from data_migration_concept.extract import ExtractError, extract_object
from data_migration_concept.load import LoadBlockedError, LoadError, load_object
from data_migration_concept.profile import ProfileError, profile_object
from data_migration_concept.profile.profile import DEFAULT_MAX_ROWS
from data_migration_concept.recon import run_recons
from data_migration_concept.report import (
    DEFAULT_MAX_REJECTS,
    ReportError,
    collect_report,
    write_reports,
)
from data_migration_concept.runlog import RunLog, RunStatus
from data_migration_concept.transform import (
    TransformError,
    generate_object,
    to_dialect,
    transform_object,
)

app = typer.Typer(help="Data_Migration_Concept workbench.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Data_Migration_Concept workbench."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def extract(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to extract; all objects if omitted.")
    ] = None,
) -> None:
    """Read each source of an object into staging tables in the working database."""
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    names = [object_name] if object_name else sorted(project.objects)
    failed = False
    for name in names:
        run_id = runlog.start_run(project.config.name, name)
        try:
            results = extract_object(project, name, engine, runlog, run_id)
        except ExtractError as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: FAILED (run {run_id})\n{exc}", err=True)
            failed = True
            continue
        runlog.finish_run(run_id, RunStatus.SUCCESS)
        typer.echo(f"{name}: OK (run {run_id})")
        for r in results:
            typer.echo(f"  {r.table_name}: {r.rows_written} row(s), {len(r.columns)} column(s)")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def transform(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to transform; all objects if omitted.")
    ] = None,
) -> None:
    """Build target tables from staging tables; the generated SQL is saved under build/."""
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    names = [object_name] if object_name else sorted(project.objects)
    failed = False
    for name in names:
        run_id = runlog.start_run(project.config.name, name)
        try:
            results = transform_object(project, name, engine, runlog, run_id)
        except (TransformError, ConfigError) as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: FAILED (run {run_id})\n{exc}", err=True)
            failed = True
            continue
        runlog.finish_run(run_id, RunStatus.SUCCESS)
        typer.echo(f"{name}: OK (run {run_id})")
        for r in results:
            typer.echo(f"  {r.target_table}: {r.rows_out} row(s) from {r.rows_in}")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def check(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to check; all objects if omitted.")
    ] = None,
) -> None:
    """Validate the target tables. Exits 1 if any error-severity check fails."""
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    names = [object_name] if object_name else sorted(project.objects)
    blocked = False
    for name in names:
        run_id = runlog.start_run(project.config.name, name)
        try:
            results = run_checks(project, name, engine, runlog, run_id)
        except CheckRunError as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: FAILED (run {run_id})\n{exc}", err=True)
            blocked = True
            continue
        blocking = blocking_failures(results)
        runlog.finish_run(run_id, RunStatus.FAILED if blocking else RunStatus.SUCCESS)
        typer.echo(f"{name}: {'BLOCKED' if blocking else 'OK'} (run {run_id})")
        _print_check_results(results)
        blocked = blocked or bool(blocking)
    if blocked:
        raise typer.Exit(code=1)


def _print_check_results(results: list[CheckResult], *, only_problems: bool = False) -> None:
    for r in results:
        if r.status == CheckStatus.PASS:
            if only_problems:
                continue
            label = "PASS "
        elif r.status == CheckStatus.ERROR:
            label = "ERROR"
        else:
            label = "FAIL " if r.severity == "error" else "WARN "
        detail = "" if r.status == CheckStatus.PASS else f" - {r.message}"
        typer.echo(f"  {label} {r.name}{detail}")
        for f in r.samples[:3]:
            typer.echo(
                f"        row {f.row_no}: {f.message}" + (f" [{f.value}]" if f.value else "")
            )


@app.command()
def demo(
    directory: Annotated[
        Path, typer.Option("--dir", help="Where to create the demo project.")
    ] = Path("dmc_demo"),
    defects: Annotated[
        bool, typer.Option("--defects", help="Seed data problems so checks block the load.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Replace an earlier demo folder.")] = False,
    source: Annotated[
        Path | None, typer.Option(help="Example project to copy (default: examples/acme_demo).")
    ] = None,
) -> None:
    """Copy the fictitious Acme project and run extract, transform, check, load, recon,
    profile and report on it (SQLite, no setup needed)."""
    try:
        result = run_demo(directory, source=source, defects=defects, force=force)
    except (DemoError, ConfigError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Demo project: {result.project_dir}")
    for o in result.objects:
        typer.echo(f"\n{o.name}: {o.overall}")
        typer.echo(f"  checks passed: {o.checks_passed}/{o.checks_total}")
        if o.blocking_checks:
            typer.echo(f"  LOAD BLOCKED by: {', '.join(o.blocking_checks)}")
        for dest in o.loaded:
            typer.echo(f"  loaded: {dest}")
        typer.echo(f"  reconciliations passed: {o.recons_passed}/{o.recons_total}")
        typer.echo(f"  report: {o.reports[1]}")
    typer.echo(f"\nOpen the .html or .xlsx reports in {result.project_dir / 'reports'}.")


@app.command()
def report(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to report on; all objects if omitted.")
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(help="Report on this run id only (default: newest run of each kind)."),
    ] = None,
    formats: Annotated[str, typer.Option("--format", help="Comma list of xlsx,html,json.")] = (
        "xlsx,html,json"
    ),
    out: Annotated[
        Path | None, typer.Option(help="Output folder (default: <project>/reports).")
    ] = None,
    max_rejects: Annotated[
        int, typer.Option(help="Rejected rows listed per check.")
    ] = DEFAULT_MAX_REJECTS,
) -> None:
    """Write Excel / HTML / JSON reports from the run log."""
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    out_dir = out or project.root / "reports"
    wanted = tuple(f.strip() for f in formats.split(",") if f.strip())
    names = [object_name] if object_name else sorted(project.objects)
    failed = False
    for name in names:
        try:
            if run is not None:
                known = runlog.get_run(run)
                if known is None:
                    raise ReportError(f"unknown run '{run}'")
                name = known["object_name"]
            data = collect_report(
                runlog, project.config.name, name, run_id=run, max_rejects=max_rejects
            )
            files = write_reports(data, out_dir, wanted)
        except ReportError as exc:
            typer.echo(f"{name}: {exc}", err=True)
            failed = True
            continue
        typer.echo(f"{name}: {data['overall']}")
        for f in files:
            typer.echo(f"  {f}")
        if run is not None:
            break
    if failed:
        raise typer.Exit(code=1)


@app.command()
def recon(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to reconcile; all objects with a recon.yaml.")
    ] = None,
) -> None:
    """Reconcile staged source data with the target tables; exits 1 on error differences."""
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    names = (
        [object_name] if object_name else sorted(n for n, o in project.objects.items() if o.recons)
    )
    if not names:
        typer.echo("No object has a recon.yaml.", err=True)
        raise typer.Exit(code=1)
    blocked = False
    for name in names:
        run_id = runlog.start_run(project.config.name, name)
        try:
            results = run_recons(project, name, engine, runlog, run_id)
        except CheckRunError as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: FAILED (run {run_id})\n{exc}", err=True)
            blocked = True
            continue
        blocking = blocking_failures(results)
        runlog.finish_run(run_id, RunStatus.FAILED if blocking else RunStatus.SUCCESS)
        typer.echo(f"{name}: {'DIFFERENCES' if blocking else 'RECONCILED'} (run {run_id})")
        _print_check_results(results)
        blocked = blocked or bool(blocking)
    if blocked:
        raise typer.Exit(code=1)


@app.command()
def load(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to load; all objects if omitted.")
    ] = None,
) -> None:
    """Run the checks and, if none blocks, write the configured file/table destinations."""
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    names = [object_name] if object_name else sorted(project.objects)
    failed = False
    for name in names:
        run_id = runlog.start_run(project.config.name, name)
        try:
            results = load_object(project, name, engine, runlog, run_id)
        except LoadBlockedError as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: NOT LOADED, checks blocked the load (run {run_id})", err=True)
            _print_check_results(exc.blocking)
            failed = True
            continue
        except (LoadError, CheckRunError, ConfigError) as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: FAILED (run {run_id})\n{exc}", err=True)
            failed = True
            continue
        runlog.finish_run(run_id, RunStatus.SUCCESS)
        typer.echo(f"{name}: LOADED (run {run_id})")
        for r in results:
            typer.echo(f"  {r.structure} -> {r.destination}: {r.rows} row(s)")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def profile(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to profile; all objects if omitted.")
    ] = None,
    stage: Annotated[str, typer.Option(help="'staging' (source data) or 'target'.")] = "staging",
    max_rows: Annotated[int, typer.Option(help="Rows profiled per table.")] = DEFAULT_MAX_ROWS,
) -> None:
    """Profile columns: empty %, distinct values, ranges, lengths, common values and shapes."""
    if stage not in ("staging", "target"):
        typer.echo("--stage must be 'staging' or 'target'", err=True)
        raise typer.Exit(code=2)
    try:
        project = load_project(path)
        engine = get_working_engine(project.config)
    except ConfigError as exc:
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc

    runlog = RunLog(engine)
    runlog.ensure_schema()
    names = [object_name] if object_name else sorted(project.objects)
    failed = False
    for name in names:
        run_id = runlog.start_run(project.config.name, name)
        try:
            tables = profile_object(
                project,
                name,
                engine,
                runlog,
                run_id,
                stage="staging" if stage == "staging" else "target",
                max_rows=max_rows,
            )
        except ProfileError as exc:
            runlog.finish_run(run_id, RunStatus.FAILED)
            typer.echo(f"{name}: FAILED (run {run_id})\n{exc}", err=True)
            failed = True
            continue
        runlog.finish_run(run_id, RunStatus.SUCCESS)
        typer.echo(f"{name}: profiled {stage} (run {run_id})")
        for table, columns in tables.items():
            typer.echo(f"  {table}")
            for c in columns:
                top = ", ".join(f"{v} ({n})" for v, n in c.top_values[:3])
                typer.echo(
                    f"    {c.column}: {c.row_count} rows, {c.empty_pct:.1f}% empty, "
                    f"{c.distinct_count} distinct, len {c.min_length}-{c.max_length}, "
                    f"range {c.min_value}..{c.max_value}; top: {top}"
                )
    if failed:
        raise typer.Exit(code=1)


@app.command("show-sql")
def show_sql(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")],
    object_name: Annotated[
        str | None, typer.Argument(help="Object to show; all objects if omitted.")
    ] = None,
    dialect: Annotated[
        str, typer.Option(help="sqlite, postgresql, mysql, mssql or oracle.")
    ] = "postgresql",
) -> None:
    """Print the SQL generated for each structure (no database needed)."""
    try:
        project = load_project(path)
        names = [object_name] if object_name else sorted(project.objects)
        for name in names:
            obj = project.objects.get(name)
            if obj is None:
                raise TransformError(f"unknown object '{name}'")
            for g in generate_object(project, obj):
                typer.echo(f"-- {name}.{g.structure} -> {g.target_table}")
                typer.echo(";\n\n".join(to_dialect(s, dialect) for s in g.statements) + ";\n")
    except (ConfigError, TransformError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("validate-config")
def validate_config(
    path: Annotated[Path, typer.Argument(help="Project folder containing project.yaml.")] = Path(
        "."
    ),
) -> None:
    """Validate a project definition and report every problem found."""
    try:
        project = load_project(path)
    except ConfigError as exc:
        typer.echo(f"Invalid project ({len(exc.errors)} problem(s)):", err=True)
        for line in exc.errors:
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=1) from exc
    n_struct = sum(len(o.config.structures) for o in project.objects.values())
    typer.echo(
        f"OK: project '{project.config.name}' with {len(project.objects)} object(s), "
        f"{n_struct} structure(s)."
    )
