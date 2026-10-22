"""Extract step: read every source of an object into staging tables."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine

from data_migration_concept.config.loader import LoadedObject, LoadedProject
from data_migration_concept.config.models import FileSource, rule_source_fields
from data_migration_concept.connectors import get_engine
from data_migration_concept.extract.errors import ExtractError
from data_migration_concept.extract.readers import read_file, read_sql
from data_migration_concept.extract.staging import StageResult, staging_table_name, write_staging
from data_migration_concept.runlog import RunLog


def _column_validator(obj: LoadedObject, source_name: str) -> Callable[[list[str]], None]:
    """Mapping rules must only reference columns the source really has."""

    def validate(columns: list[str]) -> None:
        available = set(columns)
        problems: list[str] = []
        for st in obj.config.structures:
            mapping = obj.mappings.get(st.name)
            if st.source != source_name or mapping is None:
                continue
            for fld in mapping.fields:
                missing = [c for c in rule_source_fields(fld.rule) if c not in available]
                if missing:
                    problems.append(
                        f"structure '{st.name}', field '{fld.target_field}': "
                        f"missing source column(s) {', '.join(missing)}"
                    )
        if problems:
            raise ExtractError(
                f"source '{source_name}' does not match the mapping:\n  - "
                + "\n  - ".join(problems)
            )

    return validate


def extract_object(
    project: LoadedProject,
    object_name: str,
    working_engine: Engine,
    runlog: RunLog,
    run_id: str,
) -> list[StageResult]:
    """Stage all sources of one object. Each source is a run-log step; first failure stops."""
    obj = project.objects.get(object_name)
    if obj is None:
        known = ", ".join(sorted(project.objects)) or "none"
        raise ExtractError(f"unknown object '{object_name}' (known: {known})")

    results: list[StageResult] = []
    for source in obj.config.sources:
        table_name = staging_table_name(project.config, object_name, source.name)
        with runlog.step(run_id, f"extract:{source.name}") as rec:
            if isinstance(source, FileSource):
                chunks = read_file(source, obj.root)
            else:
                chunks = read_sql(source, get_engine(project.config, source.connection))
            result = write_staging(
                working_engine,
                table_name,
                chunks,
                run_id,
                source_name=source.name,
                validate_columns=_column_validator(obj, source.name),
            )
            rec.rows_in = result.rows_read
            rec.rows_out = result.rows_written
            rec.message = f"staged into {table_name}"
        runlog.record_schema(run_id, table_name, result.columns)
        results.append(result)
    return results
