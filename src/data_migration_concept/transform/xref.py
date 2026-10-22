"""Load XREF (value mapping) CSV files into lookup tables."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from sqlalchemy import Engine

from data_migration_concept.extract.errors import ExtractError
from data_migration_concept.extract.staging import write_staging
from data_migration_concept.transform.errors import TransformError

REQUIRED = ["legacy_value", "target_value"]


def load_xref(engine: Engine, path: Path, table_name: str, run_id: str, name: str) -> int:
    """Validate `path` and (re)create `table_name` from it. Returns the number of entries."""
    if not path.is_file():
        raise TransformError(f"xref '{name}': file '{path.name}' not found")
    try:
        df = pl.read_csv(path, infer_schema=False)
    except Exception as exc:
        raise TransformError(f"xref '{name}': cannot read '{path.name}': {exc}") from exc

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise TransformError(f"xref '{name}': missing column(s) {', '.join(missing)}")
    df = df.select(REQUIRED)
    if df["legacy_value"].null_count():
        raise TransformError(f"xref '{name}': empty legacy_value found")
    dupes = df.filter(df["legacy_value"].is_duplicated())["legacy_value"].unique().sort().to_list()
    if dupes:
        shown = ", ".join(dupes[:10])
        raise TransformError(f"xref '{name}': legacy_value mapped more than once: {shown}")

    try:
        result = write_staging(engine, table_name, [df], run_id, source_name=f"xref {name}")
    except ExtractError as exc:
        raise TransformError(str(exc)) from exc
    return result.rows_written
