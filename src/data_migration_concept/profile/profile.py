"""Column profiling: completeness, distinctness, ranges, lengths, common values and shapes."""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl
from sqlalchemy import Engine, text

from data_migration_concept.extract.readers import CHUNK_SIZE, rows_to_frame
from data_migration_concept.load.writers import LINEAGE, table_columns
from data_migration_concept.transform.generator import q

TOP_N = 5
MAX_VALUE_CHARS = 50
DEFAULT_MAX_ROWS = 1_000_000


@dataclass
class ColumnProfile:
    column: str
    row_count: int
    empty_count: int
    distinct_count: int
    min_value: str | None
    max_value: str | None
    min_length: int | None
    max_length: int | None
    top_values: list[tuple[str, int]] = field(default_factory=list)
    top_patterns: list[tuple[str, int]] = field(default_factory=list)

    @property
    def empty_pct(self) -> float:
        return 100.0 * self.empty_count / self.row_count if self.row_count else 0.0


def _read(engine: Engine, table: str, columns: list[str], max_rows: int) -> pl.DataFrame:
    cols = ", ".join(q(c) for c in columns)
    frames: list[pl.DataFrame] = []
    with engine.connect().execution_options(stream_results=True) as conn:
        result = conn.execute(text(f"SELECT {cols} FROM {q(table)} ORDER BY {q('_dmc_row_no')}"))
        remaining = max_rows
        while remaining > 0:
            rows = result.fetchmany(min(CHUNK_SIZE, remaining))
            if not rows:
                break
            frames.append(rows_to_frame(columns, rows))
            remaining -= len(rows)
    if not frames:
        return pl.DataFrame({c: pl.Series(c, [], dtype=pl.Utf8) for c in columns})
    return pl.concat(frames, how="vertical_relaxed")


def _top(values: pl.Series, n: int = TOP_N) -> list[tuple[str, int]]:
    if values.len() == 0:
        return []
    counts = values.value_counts(sort=True).head(n)
    label, count = counts.columns[0], counts.columns[1]
    return [
        (str(v)[:MAX_VALUE_CHARS], int(c))
        for v, c in zip(counts[label], counts[count], strict=True)
    ]


def profile_column(series: pl.Series) -> ColumnProfile:
    as_text = series.cast(pl.Utf8)
    empty = as_text.is_null() | (as_text.str.strip_chars() == "")
    present = as_text.filter(~empty)
    native = (
        series.filter(~empty)
        if (series.dtype.is_numeric() or series.dtype.is_temporal())
        else present
    )
    shapes = (
        present.str.replace_all(r"[0-9]", "9")
        .str.replace_all(r"[A-Z]", "A")
        .str.replace_all(r"[a-z]", "a")
    )
    lengths = present.str.len_chars()
    return ColumnProfile(
        column=series.name,
        row_count=series.len(),
        empty_count=int(empty.sum()),
        distinct_count=int(present.n_unique()) if present.len() else 0,
        min_value=None if native.len() == 0 else str(native.min()),
        max_value=None if native.len() == 0 else str(native.max()),
        min_length=None if present.len() == 0 else int(lengths.min()),  # type: ignore[arg-type]
        max_length=None if present.len() == 0 else int(lengths.max()),  # type: ignore[arg-type]
        top_values=_top(present),
        top_patterns=_top(shapes),
    )


def profile_table(
    engine: Engine, table: str, max_rows: int = DEFAULT_MAX_ROWS
) -> list[ColumnProfile]:
    """Profile every non-lineage column of `table` (first `max_rows` rows)."""
    columns = [c for c in table_columns(engine, table, include_lineage=False) if c not in LINEAGE]
    df = _read(engine, table, columns, max_rows)
    return [profile_column(df[c]) for c in columns]
