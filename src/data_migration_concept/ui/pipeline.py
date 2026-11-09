"""Run pipeline steps for the UI and describe what has been run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from data_migration_concept.checks import CheckRunError, blocking_failures, run_checks
from data_migration_concept.config import ConfigError
from data_migration_concept.extract import ExtractError, extract_object
from data_migration_concept.load import LoadBlockedError, LoadError, load_object
from data_migration_concept.profile import ProfileError, profile_object
from data_migration_concept.recon import run_recons
from data_migration_concept.report import ReportError, collect_report, write_reports
from data_migration_concept.report.collect import step_kind
from data_migration_concept.runlog import RunStatus
from data_migration_concept.transform import TransformError, generate_object, transform_object
from data_migration_concept.transform.generator import q, target_table
from data_migration_concept.ui.workspace import Ctx

STEPS = ["extract", "transform", "check", "load", "recon", "profile", "report"]
KINDS = ["extract", "transform", "checks", "load", "recon", "profile"]
STEP_TO_KIND = {s: s for s in STEPS} | {"check": "checks"}

DESCRIPTIONS = {
    "extract": (
        "Reads each source (file or database) into a staging table. Values stay as text so "
        "leading zeros survive.",
        "source files / databases",
        "stg_<object>_<source>",
    ),
    "transform": (
        "Loads the XREF (value mapping) files and builds the target tables with SQL generated "
        "from your mapping rules.",
        "staging tables + XREF files",
        "xref_<object>_<name>, tgt_<table>",
    ),
    "checks": (
        "Runs only the checks you have confirmed (rules from the target structure, and your own: "
        "uniqueness, allowed values, references, patterns, ranges). With none confirmed nothing "
        "runs. Failing rows are stored with their source row number.",
        "target tables",
        "results + rejected rows in the run log",
    ),
    "load": (
        "Runs the checks again and, only if nothing blocks, writes the target data to files "
        "or database tables.",
        "target tables",
        "output files / destination tables",
    ),
    "recon": (
        "Compares the staged source with the target: counts, keys, control totals and field "
        "values, allowing for rows you deliberately exclude.",
        "staging + target tables",
        "results in the run log",
    ),
    "profile": (
        "Describes every column of the staged data: empty %, distinct values, ranges, lengths, "
        "common values and shapes.",
        "staging tables",
        "profile in the run log",
    ),
}


@dataclass
class StepOutcome:
    step: str
    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)
    run_id: str | None = None


def _failed(step: str, run_id: str | None, summary: str, details: list[str]) -> StepOutcome:
    return StepOutcome(step, False, summary, details, run_id)


def run_step(ctx: Ctx, object_name: str, step: str, *, max_rejects: int = 100) -> StepOutcome:
    """Run one step the way the command line does and describe the outcome in plain words."""
    if step not in STEPS:
        return _failed(step, None, f"Unknown step '{step}'.", [])
    project, runlog = ctx.project, ctx.runlog
    if object_name not in project.objects:
        return _failed(step, None, f"Unknown object '{object_name}'.", [])

    if step == "report":
        try:
            data = collect_report(runlog, project.config.name, object_name, max_rejects=max_rejects)
            files = write_reports(data, ctx.path / "reports", ("xlsx", "html", "json"))
        except ReportError as exc:
            return _failed(step, None, "No report yet.", [str(exc) + " Run some steps first."])
        return StepOutcome(
            step, True, f"Report written: overall status {data['overall']}.",
            [f"reports/{f.name}" for f in files],
        )  # fmt: skip

    run_id = runlog.start_run(project.config.name, object_name)
    try:
        outcome = _execute(ctx, object_name, step, run_id)
    except (
        ExtractError,
        TransformError,
        CheckRunError,
        LoadError,
        ProfileError,
        ConfigError,
    ) as exc:
        runlog.finish_run(run_id, RunStatus.FAILED)
        message = "\n".join(exc.errors) if isinstance(exc, ConfigError) else str(exc)
        return _failed(step, run_id, f"{step.capitalize()} failed.", message.splitlines())
    runlog.finish_run(run_id, RunStatus.SUCCESS if outcome.ok else RunStatus.FAILED)
    outcome.run_id = run_id
    return outcome


def _execute(ctx: Ctx, name: str, step: str, run_id: str) -> StepOutcome:
    project, engine, runlog = ctx.project, ctx.engine, ctx.runlog
    if step == "extract":
        staged = extract_object(project, name, engine, runlog, run_id)
        return StepOutcome(
            step, True, f"Staged {len(staged)} source(s).",
            [f"{r.table_name}: {r.rows_written} rows, {len(r.columns)} columns" for r in staged],
        )  # fmt: skip
    if step == "transform":
        built = transform_object(project, name, engine, runlog, run_id)
        return StepOutcome(
            step, True, f"Built {len(built)} target table(s).",
            [f"{r.target_table}: {r.rows_out} rows from {r.rows_in} staged" for r in built],
        )  # fmt: skip
    if step == "check":
        results = run_checks(project, name, engine, runlog, run_id)
        if not results:
            return StepOutcome(
                step,
                True,
                "No checks are confirmed yet, so nothing was run and nothing can block the load.",
                ["Review the suggestions on the Proposals tab, or add checks on the Checks tab."],
            )
        blocking = blocking_failures(results)
        passed = sum(1 for r in results if r.status.value == "PASS")
        details = [f"BLOCKING: {r.name} - {r.message}" for r in blocking]
        return StepOutcome(
            step, not blocking,
            f"{passed} of {len(results)} checks passed"
            + (f"; {len(blocking)} blocking the load." if blocking else "."),
            details,
        )  # fmt: skip
    if step == "load":
        try:
            loaded = load_object(project, name, engine, runlog, run_id)
        except LoadBlockedError as exc:
            return StepOutcome(
                step, False, "Nothing was loaded: checks are blocking the load.",
                [f"{r.name}: {r.message}" for r in exc.blocking],
            )  # fmt: skip
        return StepOutcome(
            step, True, f"Loaded {len(loaded)} destination(s).",
            [f"{r.structure} -> {r.destination}: {r.rows} rows" for r in loaded],
        )  # fmt: skip
    if step == "recon":
        results = run_recons(project, name, engine, runlog, run_id)
        blocking = blocking_failures(results)
        passed = sum(1 for r in results if r.status.value == "PASS")
        return StepOutcome(
            step, not blocking,
            f"{passed} of {len(results)} reconciliations agree"
            + (f"; {len(blocking)} differ." if blocking else "."),
            [f"{r.name}: {r.message}" for r in blocking],
        )  # fmt: skip
    profiled = profile_object(project, name, engine, runlog, run_id)
    return StepOutcome(
        step, True, f"Profiled {len(profiled)} table(s).",
        [f"{t}: {len(cols)} columns" for t, cols in profiled.items()],
    )  # fmt: skip


def run_all(ctx: Ctx, object_name: str) -> list[StepOutcome]:
    """Extract, transform, check, load (if allowed), recon, profile and report."""
    outcomes: list[StepOutcome] = []
    for step in STEPS:
        outcome = run_step(ctx, object_name, step)
        outcomes.append(outcome)
        if not outcome.ok and step in ("extract", "transform"):
            break  # later steps cannot work
    return outcomes


# Files whose change makes the result of a step out of date (relative to the object folder).
CONFIG_FILES = {
    "extract": ["object.yaml", "data/**/*"],
    "transform": ["object.yaml", "mappings/*", "xref/*", "sql/*"],
    "checks": ["object.yaml", "mappings/*", "checks.yaml"],
    "load": ["object.yaml", "mappings/*", "xref/*", "sql/*", "checks.yaml"],
    "recon": ["object.yaml", "mappings/*", "recon.yaml"],
}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _changed_since(root: Path, kind: str, moment: datetime) -> str | None:
    """Name of a file of this object that was changed after `moment`, if any."""
    limit = _aware(moment).timestamp()
    for pattern in CONFIG_FILES.get(kind, []):
        for path in root.glob(pattern):
            if path.is_file() and path.stat().st_mtime > limit:
                return path.relative_to(root).as_posix()
    return None


def pipeline_state(ctx: Ctx, object_name: str) -> list[dict[str, Any]]:
    """Newest run of each step kind: status, row counts and whether the result is out of date
    (an earlier step ran more recently, or a file it depends on was changed after it ran)."""
    runlog = ctx.runlog
    root = ctx.project.objects[object_name].root
    newest: dict[str, dict[str, Any]] = {}
    for run in runlog.list_runs(ctx.project.config.name, object_name):
        for st in runlog.get_steps(run["run_id"]):
            kind = step_kind(st["step_name"])
            if kind in KINDS:
                slot = newest.setdefault(
                    kind, {"run": run, "steps": [], "started": run["started_at"]}
                )
                if slot["run"]["run_id"] == run["run_id"]:
                    slot["steps"].append(st)
    cards: list[dict[str, Any]] = []
    previous_start = None
    for kind in KINDS:
        desc, reads, writes = DESCRIPTIONS[kind]
        found = newest.get(kind)
        card: dict[str, Any] = {
            "kind": kind, "description": desc, "reads": reads, "writes": writes,
            "status": "NOT RUN", "rows_in": None, "rows_out": None, "run_id": None,
            "started": None, "stale": False, "stale_reason": "", "message": "",
        }  # fmt: skip
        if found:
            slot = found
            steps = slot["steps"]
            failed = [s for s in steps if s["status"] == "FAILED"]
            card.update(
                status="FAILED" if failed else "OK",
                rows_in=sum(s["rows_in"] or 0 for s in steps) if steps else None,
                rows_out=sum(s["rows_out"] or 0 for s in steps) if steps else None,
                run_id=slot["run"]["run_id"],
                started=slot["run"]["started_at"],
                message=(failed[0]["message"] if failed else "") or "",
            )
            earlier_newer = (
                kind in ("transform", "checks", "load", "recon")
                and previous_start is not None
                and previous_start > slot["run"]["started_at"]
            )
            if earlier_newer:
                card["stale"] = True
                card["stale_reason"] = "A step before this one ran more recently."
            changed = _changed_since(root, kind, slot["run"]["started_at"])
            if changed:
                card["stale"] = True
                card["stale_reason"] = f"{changed} was changed after this step ran."
        newer = card["started"] and (previous_start is None or card["started"] > previous_start)
        if kind in ("extract", "transform", "checks") and newer:
            previous_start = card["started"]
        cards.append(card)
    return cards


# --- tables ---------------------------------------------------------------------------


def object_tables(ctx: Ctx, object_name: str) -> list[dict[str, str]]:
    """Existing tables that belong to an object, labelled by stage."""
    cfg = ctx.project.config
    obj = ctx.project.objects[object_name]
    existing = set(inspect(ctx.engine).get_table_names())
    wanted: list[tuple[str, str]] = []
    for s in obj.config.sources:
        wanted.append(("Staging (source as read)", f"{cfg.staging_prefix}{object_name}_{s.name}"))
    for x in sorted((obj.root / "xref").glob("*.csv")) if (obj.root / "xref").is_dir() else []:
        wanted.append(("XREF lookup", f"{cfg.xref_prefix}{object_name}_{x.stem}"))
    for st in obj.config.structures:
        wanted.append(("Target (after transform)", target_table(ctx.project, st)))
    return [{"stage": stage, "table": t} for stage, t in wanted if t in existing]


def preview_table(
    ctx: Ctx, object_name: str, table: str, *, page: int = 1, size: int = 50, needle: str = ""
) -> dict[str, Any]:
    allowed = {t["table"] for t in object_tables(ctx, object_name)}
    if table not in allowed:
        raise ValueError(f"Table '{table}' is not part of this object.")
    columns = [c["name"] for c in inspect(ctx.engine).get_columns(table)]
    where, params = "", {}
    if needle:
        where = " WHERE " + " OR ".join(f"CAST({q(c)} AS TEXT) LIKE :needle" for c in columns)
        params["needle"] = f"%{needle}%"
    order = f" ORDER BY {q('_dmc_row_no')}" if "_dmc_row_no" in columns else ""
    size = max(1, min(size, 500))
    page = max(1, page)
    with ctx.engine.connect() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM {q(table)}{where}"), params).scalar_one()
        rows = conn.execute(
            text(f"SELECT * FROM {q(table)}{where}{order} LIMIT {size} OFFSET {(page - 1) * size}"),
            params,
        ).all()
    return {
        "table": table, "columns": columns, "rows": rows, "total": int(total),
        "page": page, "size": size, "pages": max(1, -(-int(total) // size)), "needle": needle,
    }  # fmt: skip


def generated_sql(ctx: Ctx, object_name: str) -> list[dict[str, str]]:
    obj = ctx.project.objects[object_name]
    return [
        {"structure": g.structure, "target": g.target_table, "sql": g.render()}
        for g in generate_object(ctx.project, obj)
    ]
