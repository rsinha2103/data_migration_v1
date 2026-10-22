"""Run the reconciliations of an object (recon.yaml)."""

from __future__ import annotations

from sqlalchemy import Engine, inspect

from data_migration_concept.checks.base import CheckContext, CheckResult, CheckRunError
from data_migration_concept.checks.runner import execute_and_record
from data_migration_concept.config.loader import LoadedProject
from data_migration_concept.extract.staging import staging_table_name
from data_migration_concept.recon.checks import build_recons
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform.generator import target_table


def run_recons(
    project: LoadedProject,
    object_name: str,
    engine: Engine,
    runlog: RunLog,
    run_id: str,
) -> list[CheckResult]:
    """Compare staged source with target tables. Results use the same tables and types as checks
    (check_type RECON_*), so `blocking` has the same meaning."""
    obj = project.objects.get(object_name)
    if obj is None:
        known = ", ".join(sorted(project.objects)) or "none"
        raise CheckRunError(f"unknown object '{object_name}' (known: {known})")
    if not obj.recons:
        raise CheckRunError(f"object '{object_name}' has no recon.yaml")

    structures = {s.name: s for s in obj.config.structures}
    targets = {n: target_table(project, s) for n, s in structures.items()}
    sources = {
        n: staging_table_name(project.config, object_name, s.source) for n, s in structures.items()
    }
    existing = set(inspect(engine).get_table_names())
    needed: dict[str, str] = {}
    for rec in obj.recons:
        needed[sources[rec.structure]] = "extract"
        needed[rec.target_table or targets[rec.structure]] = (
            "load or import" if rec.target_table else "transform"
        )
    missing = sorted(t for t in needed if t not in existing)
    if missing:
        hints = sorted({needed[t] for t in missing})
        raise CheckRunError(
            f"table(s) not found: {', '.join(missing)}; run {' / '.join(hints)} first"
        )

    ctx = CheckContext(
        engine=engine, project=project, obj=obj, target_tables=targets, source_tables=sources
    )
    return execute_and_record(build_recons(obj.recons), ctx, runlog, run_id, "recon")
