"""Load step: run the checks, and only if nothing blocks, write each configured destination."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import Engine

from data_migration_concept.checks import blocking_failures, run_checks
from data_migration_concept.config.loader import LoadedProject
from data_migration_concept.config.models import FileLoad
from data_migration_concept.connectors import get_engine
from data_migration_concept.load.errors import LoadBlockedError, LoadError
from data_migration_concept.load.writers import (
    iter_frames,
    row_count,
    table_columns,
    write_file,
    write_table,
)
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform.generator import target_table


@dataclass
class LoadResult:
    structure: str
    kind: str
    destination: str
    rows: int


def load_object(
    project: LoadedProject,
    object_name: str,
    engine: Engine,
    runlog: RunLog,
    run_id: str,
) -> list[LoadResult]:
    """Check, then load. Checks always run first; blocking failures stop the load before any
    destination is touched. Each destination is written atomically (file: temp + rename;
    table: one transaction)."""
    obj = project.objects.get(object_name)
    if obj is None:
        known = ", ".join(sorted(project.objects)) or "none"
        raise LoadError(f"unknown object '{object_name}' (known: {known})")
    if not obj.config.loads:
        raise LoadError(f"object '{object_name}' has no 'loads' defined in object.yaml")

    blocking = blocking_failures(run_checks(project, object_name, engine, runlog, run_id))
    if blocking:
        raise LoadBlockedError(blocking)

    structures = {s.name: s for s in obj.config.structures}
    results: list[LoadResult] = []
    for ld in obj.config.loads:
        table = target_table(project, structures[ld.structure])
        columns = table_columns(engine, table, ld.include_lineage)
        with runlog.step(run_id, f"load:{ld.structure}:{ld.kind}") as rec:
            frames = iter_frames(engine, table, columns)
            if isinstance(ld, FileLoad):
                dest = (project.root / ld.path).resolve()
                tmp = dest.with_name(f"{dest.name}.{run_id[:8]}.tmp")
                try:
                    rows = write_file(frames, ld, tmp)
                    os.replace(tmp, dest)
                finally:
                    tmp.unlink(missing_ok=True)
                destination = ld.path
            else:
                rows = write_table(frames, ld, get_engine(project.config, ld.connection))
                destination = f"{ld.connection}:{ld.table}"
            rec.rows_in = row_count(engine, table)
            rec.rows_out = rows
            rec.message = f"wrote {destination}"
            if rows != rec.rows_in:
                raise LoadError(
                    f"structure '{ld.structure}': read {rec.rows_in} rows but wrote {rows}"
                )
        results.append(LoadResult(ld.structure, ld.kind, destination, rows))
    return results
