"""Built-in validation checks (set-based SQL; data-type checks run in Python)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import inspect, text

from data_migration_concept.checks.base import (
    Check,
    CheckConfigError,
    CheckContext,
    Failure,
    Severity,
)
from data_migration_concept.transform.errors import TransformError
from data_migration_concept.transform.generator import lit, q, render_sql_template

ROW_NO = "_dmc_row_no"
FETCH = 5_000


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def _stream(ctx: CheckContext, sql: str) -> Iterator[Any]:
    with ctx.engine.connect().execution_options(stream_results=True) as conn:
        result = conn.execute(text(sql))
        while True:
            rows = result.fetchmany(FETCH)
            if not rows:
                return
            yield from rows


class MandatoryCheck(Check):
    check_type = "MANDATORY"

    def __init__(self, structure: str, field: str, severity: Severity = "error") -> None:
        super().__init__(f"mandatory.{structure}.{field}", structure, severity, field)

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        sql = (
            f"SELECT {q(ROW_NO)} FROM {q(table)} "
            f"WHERE {f} IS NULL OR TRIM(CAST({f} AS TEXT)) = '' ORDER BY 1"
        )
        for row in _stream(ctx, sql):
            yield Failure(row[0], self.field, None, f"{self.field} is mandatory but empty")


class LengthCheck(Check):
    check_type = "LENGTH"

    def __init__(self, structure: str, field: str, length: int, severity: Severity = "error"):
        super().__init__(f"length.{structure}.{field}", structure, severity, field)
        self.length = length

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        sql = (
            f"SELECT {q(ROW_NO)}, {f} FROM {q(table)} "
            f"WHERE LENGTH(CAST({f} AS TEXT)) > {self.length} ORDER BY 1"
        )
        for row in _stream(ctx, sql):
            yield Failure(
                row[0], self.field, _s(row[1]), f"{self.field} is longer than {self.length}"
            )


_INTEGER = re.compile(r"^[+-]?\d+$")


def _is_integer(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return bool(_INTEGER.match(str(v).strip()))


def _is_decimal(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    try:
        return Decimal(str(v).strip()).is_finite()
    except InvalidOperation:
        return False


def _is_date(v: Any) -> bool:
    if isinstance(v, date | datetime):
        return True
    try:
        date.fromisoformat(str(v).strip())
    except ValueError:
        return False
    return True


def _is_boolean(v: Any) -> bool:
    return isinstance(v, bool) or str(v).strip().lower() in {"true", "false", "1", "0"}


TYPE_VALIDATORS: dict[str, tuple[Callable[[Any], bool], str]] = {
    "integer": (_is_integer, "a whole number"),
    "decimal": (_is_decimal, "a number"),
    "date": (_is_date, "an ISO date (YYYY-MM-DD)"),
    "boolean": (_is_boolean, "true/false/1/0"),
}


class DataTypeCheck(Check):
    check_type = "DATA_TYPE"

    def __init__(self, structure: str, field: str, data_type: str, severity: Severity = "error"):
        super().__init__(f"datatype.{structure}.{field}", structure, severity, field)
        self.data_type = data_type

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        valid, expected = TYPE_VALIDATORS[self.data_type]
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        sql = f"SELECT {q(ROW_NO)}, {f} FROM {q(table)} WHERE {f} IS NOT NULL ORDER BY 1"
        for row in _stream(ctx, sql):
            if str(row[1]).strip() != "" and not valid(row[1]):
                yield Failure(row[0], self.field, _s(row[1]), f"{self.field} is not {expected}")


class UniqueCheck(Check):
    check_type = "UNIQUE"

    def __init__(self, name: str, structure: str, fields: list[str], severity: Severity = "error"):
        super().__init__(name, structure, severity, ", ".join(fields))
        self.fields = fields

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        table = ctx.table(self.structure)
        ctx.require_columns(table, self.fields)
        cols = ", ".join(q(f) for f in self.fields)
        on = " AND ".join(f"t.{q(f)} = d.{q(f)}" for f in self.fields)
        sel = ", ".join(f"t.{q(f)}" for f in self.fields)
        sql = (
            f"SELECT t.{q(ROW_NO)}, {sel} FROM {q(table)} AS t "
            f"JOIN (SELECT {cols} FROM {q(table)} GROUP BY {cols} HAVING COUNT(*) > 1) AS d "
            f"ON {on} ORDER BY 2, 1"
        )
        for row in _stream(ctx, sql):
            key = ", ".join(f"{f}={v}" for f, v in zip(self.fields, row[1:], strict=True))
            yield Failure(row[0], self.field, _s(row[1]), f"duplicate key ({key})")


class AllowedValuesCheck(Check):
    check_type = "ALLOWED_VALUES"

    def __init__(
        self,
        name: str,
        structure: str,
        field: str,
        values: list[str | int | float | bool],
        severity: Severity = "error",
    ):
        super().__init__(name, structure, severity, field)
        self.values = [str(v).lower() if isinstance(v, bool) else str(v) for v in values]

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        allowed = ", ".join(lit(v) for v in self.values)
        sql = (
            f"SELECT {q(ROW_NO)}, {f} FROM {q(table)} "
            f"WHERE {f} IS NOT NULL AND CAST({f} AS TEXT) NOT IN ({allowed}) ORDER BY 1"
        )
        for row in _stream(ctx, sql):
            yield Failure(row[0], self.field, _s(row[1]), f"{self.field} value is not allowed")


class ReferentialCheck(Check):
    check_type = "REFERENTIAL"

    def __init__(
        self,
        name: str,
        structure: str,
        field: str,
        ref_structure: str,
        ref_field: str,
        ref_object: str | None = None,
        severity: Severity = "error",
    ):
        super().__init__(name, structure, severity, field)
        self.ref_structure = ref_structure
        self.ref_field = ref_field
        self.ref_object = ref_object

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        ref_obj = ctx.project.objects.get(self.ref_object or ctx.obj.config.name)
        if ref_obj is None:
            raise CheckConfigError(f"unknown ref_object '{self.ref_object}'")
        table = ctx.table(self.structure)
        ref_table = ctx.table(self.ref_structure, ref_obj)
        ctx.require_columns(table, [self.field])
        if ref_table not in inspect(ctx.engine).get_table_names():
            raise CheckConfigError(f"table {ref_table} not found; run transform first")
        ctx.require_columns(ref_table, [self.ref_field])
        f, rf = q(self.field), q(self.ref_field)
        sql = (
            f"SELECT t.{q(ROW_NO)}, t.{f} FROM {q(table)} AS t "
            f"WHERE t.{f} IS NOT NULL AND NOT EXISTS ("
            f"SELECT 1 FROM {q(ref_table)} AS r WHERE CAST(r.{rf} AS TEXT) = CAST(t.{f} AS TEXT)"
            f") ORDER BY 1"
        )
        for row in _stream(ctx, sql):
            yield Failure(
                row[0],
                self.field,
                _s(row[1]),
                f"{self.field} not found in {self.ref_structure}.{self.ref_field}",
            )


class NullRateCheck(Check):
    check_type = "NULL_RATE"

    def __init__(
        self, name: str, structure: str, field: str, max_pct: float, severity: Severity = "error"
    ):
        super().__init__(name, structure, severity, field)
        self.max_pct = max_pct

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        with ctx.engine.connect() as conn:
            total, empty = conn.execute(
                text(
                    f"SELECT COUNT(*), "
                    f"COALESCE(SUM(CASE WHEN {f} IS NULL OR TRIM(CAST({f} AS TEXT)) = '' "
                    f"THEN 1 ELSE 0 END), 0) FROM {q(table)}"
                )
            ).one()
        pct = 100.0 * empty / total if total else 0.0
        if pct > self.max_pct:
            yield Failure(
                None,
                self.field,
                f"{pct:.1f}%",
                f"{self.field} is empty in {pct:.1f}% of rows (allowed {self.max_pct:g}%)",
            )


class PatternCheck(Check):
    check_type = "PATTERN"

    def __init__(
        self, name: str, structure: str, field: str, pattern: str, severity: Severity = "error"
    ):
        super().__init__(name, structure, severity, field)
        self.pattern = re.compile(pattern)

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        sql = f"SELECT {q(ROW_NO)}, {f} FROM {q(table)} WHERE {f} IS NOT NULL ORDER BY 1"
        for row in _stream(ctx, sql):
            value = str(row[1])
            if value.strip() != "" and not self.pattern.fullmatch(value):
                yield Failure(
                    row[0], self.field, value, f"{self.field} does not match {self.pattern.pattern}"
                )


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


class RangeCheck(Check):
    check_type = "RANGE"

    def __init__(
        self,
        name: str,
        structure: str,
        field: str,
        minimum: float | None,
        maximum: float | None,
        severity: Severity = "error",
    ):
        super().__init__(name, structure, severity, field)
        self.minimum = None if minimum is None else Decimal(str(minimum))
        self.maximum = None if maximum is None else Decimal(str(maximum))

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        sql = f"SELECT {q(ROW_NO)}, {f} FROM {q(table)} WHERE {f} IS NOT NULL ORDER BY 1"
        for row in _stream(ctx, sql):
            if str(row[1]).strip() == "":
                continue
            number = _to_decimal(row[1])
            if number is None:
                yield Failure(row[0], self.field, _s(row[1]), f"{self.field} is not a number")
            elif (self.minimum is not None and number < self.minimum) or (
                self.maximum is not None and number > self.maximum
            ):
                yield Failure(
                    row[0], self.field, _s(row[1]), f"{self.field} is outside the allowed range"
                )


class OutlierCheck(Check):
    check_type = "OUTLIER"

    def __init__(
        self, name: str, structure: str, field: str, max_zscore: float, severity: Severity = "error"
    ):
        super().__init__(name, structure, severity, field)
        self.max_zscore = max_zscore

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        assert self.field is not None
        table = ctx.table(self.structure)
        ctx.require_columns(table, [self.field])
        f = q(self.field)
        sql = f"SELECT {q(ROW_NO)}, {f} FROM {q(table)} WHERE {f} IS NOT NULL ORDER BY 1"
        points: list[tuple[int, float, Any]] = []
        for row in _stream(ctx, sql):
            number = _to_decimal(row[1])
            if number is not None:
                points.append((row[0], float(number), row[1]))
        if len(points) < 3:
            return
        mean = sum(p[1] for p in points) / len(points)
        std = (sum((p[1] - mean) ** 2 for p in points) / len(points)) ** 0.5
        if std == 0:
            return
        for row_no, value, raw in points:
            z = abs(value - mean) / std
            if z > self.max_zscore:
                yield Failure(
                    row_no, self.field, _s(raw), f"{self.field} is an outlier (z-score {z:.1f})"
                )


class SqlAssertCheck(Check):
    check_type = "SQL_ASSERT"

    def __init__(self, name: str, structure: str, sql: str, severity: Severity = "error"):
        super().__init__(name, structure, severity)
        self.sql = sql

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        try:
            sql = render_sql_template(ctx.project, ctx.obj, self.sql, f"check '{self.name}'")
        except TransformError as exc:
            raise CheckConfigError(str(exc)) from exc
        with ctx.engine.connect().execution_options(stream_results=True) as conn:
            result = conn.execute(text(sql))
            keys = [k.lower() for k in result.keys()]  # noqa: SIM118
            row_idx = keys.index(ROW_NO) if ROW_NO in keys else None
            while True:
                rows = result.fetchmany(FETCH)
                if not rows:
                    return
                for row in rows:
                    row_no = int(row[row_idx]) if row_idx is not None else None
                    yield Failure(row_no, None, None, f"assertion '{self.name}' violated")
