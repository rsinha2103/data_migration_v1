"""Profile the staging (source) or target tables of an object and store the result."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import Engine, inspect

from data_migration_concept.config.loader import LoadedProject
from data_migration_concept.extract.staging import staging_table_name
from data_migration_concept.profile.profile import DEFAULT_MAX_ROWS, ColumnProfile, profile_table
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform.generator import target_table

Stage = Literal["staging", "target"]


class ProfileError(Exception):
    """Profiling is not possible (unknown object, tables missing)."""


def profile_object(
    project: LoadedProject,
    object_name: str,
    engine: Engine,
    runlog: RunLog,
    run_id: str,
    *,
    stage: Stage = "staging",
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, list[ColumnProfile]]:
    obj = project.objects.get(object_name)
    if obj is None:
        known = ", ".join(sorted(project.objects)) or "none"
        raise ProfileError(f"unknown object '{object_name}' (known: {known})")
    if stage == "staging":
        tables = [
            staging_table_name(project.config, object_name, s.name) for s in obj.config.sources
        ]
    else:
        tables = [target_table(project, s) for s in obj.config.structures]
    existing = set(inspect(engine).get_table_names())
    missing = [t for t in tables if t not in existing]
    if missing:
        hint = "extract" if stage == "staging" else "transform"
        raise ProfileError(f"table(s) not found: {', '.join(missing)}; run {hint} first")

    result: dict[str, list[ColumnProfile]] = {}
    for table in tables:
        with runlog.step(run_id, f"profile:{table}") as rec:
            profiles = profile_table(engine, table, max_rows)
            rec.rows_in = profiles[0].row_count if profiles else 0
            rec.message = f"{len(profiles)} column(s)"
        runlog.record_profile(run_id, table, profiles)
        result[table] = profiles
    return result
