"""Extract step: read every source of an object into staging tables."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine

from data_migration_concept.config.loader import LoadedObject, LoadedProject
from data_migration_concept.config.models import FileSource, rule_source_fields, split_ref
from data_migration_concept.connectors import get_engine
from data_migration_concept.extract.errors import ExtractError
from data_migration_concept.extract.readers import read_file, read_sql
from data_migration_concept.extract.staging import StageResult, staging_table_name, write_staging
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform.generator import effective_rule


def _column_validator(obj: LoadedObject, source_name: str) -> Callable[[list[str]], None]:
    """Mapping rules must only reference columns the source really has."""

    def validate(columns: list[str]) -> None:
        available = set(columns)
        problems: list[str] = []
        for st in obj.config.structures:
            mapping = obj.mappings.get(st.name)
            if mapping is None:
                continue
            for rs in st.row_sets():
                joined = {j.source for j in rs.joins}
                where = f"structure '{st.name}'" + (
                    "" if rs is st.row_sets()[0] else f", row set '{rs.name}'"
                )

                def owned_missing(
                    refs: list[str], rs_source: str = rs.source, names: set[str] = joined
                ) -> list[str]:
                    """Columns of refs that belong to this source and are not in it."""
                    gone = []
                    for ref in refs:
                        alias, column = split_ref(ref, names)
                        if (alias or rs_source) == source_name and column not in available:
                            gone.append(ref)
                    return gone

                for fld in mapping.fields:
                    missing = owned_missing(rule_source_fields(effective_rule(rs, fld)))
                    if missing:
                        problems.append(
                            f"{where}, field '{fld.target_field}': "
                            f"missing source column(s) {', '.join(missing)}"
                        )
                for join in rs.joins:
                    keys = owned_missing([k.main for k in join.keys])
                    if join.source == source_name:
                        keys += [k.other for k in join.keys if k.other not in available]
                    if keys:
                        problems.append(
                            f"{where}, join with '{join.source}': "
                            f"missing key column(s) {', '.join(keys)}"
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
