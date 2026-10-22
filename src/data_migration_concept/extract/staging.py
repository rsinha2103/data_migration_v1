"""Write DataFrame chunks into a staging table (dropped and recreated on every extract)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import polars as pl
import sqlalchemy as sa
from sqlalchemy import Engine, MetaData, Table

from data_migration_concept.config.models import ProjectConfig
from data_migration_concept.extract.errors import ExtractError

ROW_NO = "_dmc_row_no"
RUN_ID = "_dmc_run_id"
RESERVED = {ROW_NO, RUN_ID}


@dataclass
class StageResult:
    table_name: str
    rows_read: int
    rows_written: int
    columns: list[tuple[str, str]]  # source columns only (lineage columns excluded)


def staging_table_name(project: ProjectConfig, object_name: str, source_name: str) -> str:
    return f"{project.staging_prefix}{object_name}_{source_name}"


def sa_type(dtype: pl.DataType) -> sa.types.TypeEngine:
    if dtype.is_integer():
        return sa.BigInteger()
    if dtype.is_float():
        return sa.Float()
    if dtype == pl.Boolean:
        return sa.Boolean()
    if dtype == pl.Date:
        return sa.Date()
    if dtype == pl.Datetime:
        return sa.DateTime()
    if dtype == pl.Decimal:
        return sa.Numeric()
    return sa.Text()  # strings, nulls and anything exotic


def _check_columns(source_name: str, names: list[str]) -> None:
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ExtractError(f"source '{source_name}': duplicate column(s): {', '.join(dupes)}")
    clash = sorted(RESERVED & set(names))
    if clash:
        raise ExtractError(f"source '{source_name}': reserved column name(s): {', '.join(clash)}")


def write_staging(
    engine: Engine,
    table_name: str,
    chunks: Iterable[pl.DataFrame],
    run_id: str,
    *,
    source_name: str,
    validate_columns: Callable[[list[str]], None] | None = None,
) -> StageResult:
    """Stream chunks into `table_name`. Column checks run before any existing table is dropped."""
    tbl: Table | None = None
    columns: list[tuple[str, str]] = []
    rows_read = 0
    for chunk in chunks:
        if tbl is None:
            names = chunk.columns
            _check_columns(source_name, names)
            if validate_columns is not None:
                validate_columns(names)
            md = MetaData()
            tbl = Table(
                table_name,
                md,
                sa.Column(ROW_NO, sa.BigInteger, nullable=False),
                sa.Column(RUN_ID, sa.String(36), nullable=False),
                *(sa.Column(n, sa_type(t), nullable=True) for n, t in chunk.schema.items()),
            )
            columns = [(n, str(t)) for n, t in chunk.schema.items()]
            tbl.drop(engine, checkfirst=True)
            tbl.create(engine)
        if chunk.height == 0:
            continue
        records = chunk.to_dicts()
        for offset, rec in enumerate(records, start=1):
            rec[ROW_NO] = rows_read + offset
            rec[RUN_ID] = run_id
        with engine.begin() as conn:
            conn.execute(sa.insert(tbl), records)
        rows_read += chunk.height

    if tbl is None:
        raise ExtractError(f"source '{source_name}': produced no data")
    with engine.connect() as conn:
        written = conn.execute(sa.select(sa.func.count()).select_from(tbl)).scalar_one()
    if written != rows_read:
        raise ExtractError(
            f"source '{source_name}': read {rows_read} rows but staged {written} in '{table_name}'"
        )
    return StageResult(table_name, rows_read, written, columns)
