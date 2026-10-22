"""Reconciliation checks: staged source vs target, as ordinary Check/CheckResult objects."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from data_migration_concept.checks.base import (
    Check,
    CheckConfigError,
    CheckContext,
    Failure,
    Severity,
)
from data_migration_concept.config import models as cfg
from data_migration_concept.transform.errors import TransformError
from data_migration_concept.transform.generator import q, render_sql_template

ROW_NO = "_dmc_row_no"
FETCH = 5_000


def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _norm(value: Any, *, trim: bool, lower: bool = False) -> str | None:
    """Text form used for comparison; empty text counts as no value."""
    if value is None:
        return None
    s = str(value)
    if trim:
        s = s.strip()
    if lower:
        s = s.lower()
    return s or None


class ReconCheck(Check):
    unit = "difference(s)"

    def __init__(self, conf: cfg.CountRecon | cfg.KeysRecon | cfg.TotalsRecon | cfg.FieldsRecon):
        severity: Severity = conf.severity
        super().__init__(conf.name, conf.structure, severity)
        self.conf = conf

    # --- tables -----------------------------------------------------------------

    def source_table(self, ctx: CheckContext) -> str:
        return ctx.source_tables[self.structure]

    def target_table(self, ctx: CheckContext) -> str:
        return self.conf.target_table or ctx.table(self.structure)

    def count_rows(self, ctx: CheckContext) -> int:
        return ctx.row_count(self.target_table(ctx))

    def _source_from(self, ctx: CheckContext) -> str:
        """FROM clause for source rows, minus the explained exclusions, aliased `s`."""
        table = self.source_table(ctx)
        if not self.conf.exclude_source:
            return f"{q(table)} AS s"
        try:
            cond = render_sql_template(
                ctx.project, ctx.obj, self.conf.exclude_source, f"recon '{self.name}'"
            )
        except TransformError as exc:
            raise CheckConfigError(str(exc)) from exc
        return (
            f"(SELECT * FROM {q(table)} AS s WHERE CASE WHEN ({cond}) THEN 1 ELSE 0 END = 0) AS s"
        )

    def _rows(self, ctx: CheckContext, sql: str) -> Iterator[Any]:
        with ctx.engine.connect().execution_options(stream_results=True) as conn:
            result = conn.execute(text(sql))
            while True:
                batch = result.fetchmany(FETCH)
                if not batch:
                    return
                yield from batch

    def _prepare(self, ctx: CheckContext, source_cols: list[str], target_cols: list[str]) -> None:
        ctx.require_columns(self.source_table(ctx), source_cols)
        ctx.require_columns(self.target_table(ctx), target_cols)

    def _target_row_no(self, ctx: CheckContext) -> str:
        return q(ROW_NO) if ROW_NO in ctx.columns(self.target_table(ctx)) else "NULL"

    def _keys(
        self, ctx: CheckContext, side: str, key: list[str], trim: bool
    ) -> dict[tuple[str | None, ...], int | None]:
        """key -> first row number, for the source (after exclusions) or the target."""
        cols = ", ".join(q(k) for k in key)
        if side == "source":
            src_cols = ", ".join("s." + q(k) for k in key)
            sql = f"SELECT s.{q(ROW_NO)}, {src_cols} FROM {self._source_from(ctx)}"
        else:
            sql = f"SELECT {self._target_row_no(ctx)}, {cols} FROM {q(self.target_table(ctx))}"
        out: dict[tuple[str | None, ...], int | None] = {}
        for row in self._rows(ctx, sql + " ORDER BY 1"):
            out.setdefault(tuple(_norm(v, trim=trim) for v in row[1:]), row[0])
        return out


class CountRecon(ReconCheck):
    check_type = "RECON_COUNT"

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        conf = self.conf
        assert isinstance(conf, cfg.CountRecon)
        self._prepare(ctx, [], [])
        with ctx.engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM {q(self.source_table(ctx))}")
            ).scalar_one()
            expected = conn.execute(
                text(f"SELECT COUNT(*) FROM {self._source_from(ctx)}")
            ).scalar_one()
        actual = ctx.row_count(self.target_table(ctx))
        if abs(actual - expected) > conf.tolerance:
            excluded = total - expected
            note = f" ({excluded} intentionally excluded)" if excluded else ""
            yield Failure(
                None,
                None,
                f"{expected} -> {actual}",
                f"source has {total} rows{note}, so {expected} expected; target has {actual} "
                f"(difference {actual - expected:+d})",
            )


class KeysRecon(ReconCheck):
    check_type = "RECON_KEYS"

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        conf = self.conf
        assert isinstance(conf, cfg.KeysRecon)
        self._prepare(ctx, conf.source_key, conf.target_key)
        source = self._keys(ctx, "source", conf.source_key, conf.trim)
        target = self._keys(ctx, "target", conf.target_key, conf.trim)
        label = ", ".join(conf.source_key)
        for key, row_no in source.items():
            if key not in target:
                yield Failure(
                    row_no,
                    label,
                    "|".join(k or "" for k in key),
                    "key is in the source but missing in the target",
                )
        for key, row_no in target.items():
            if key not in source:
                yield Failure(
                    row_no,
                    label,
                    "|".join(k or "" for k in key),
                    "key is in the target but not in the source",
                )


class TotalsRecon(ReconCheck):
    check_type = "RECON_TOTALS"

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        conf = self.conf
        assert isinstance(conf, cfg.TotalsRecon)
        self._prepare(
            ctx,
            [conf.source_field, *conf.group_by_source],
            [conf.target_field, *conf.group_by_target],
        )
        src_cols = ", ".join("s." + q(c) for c in [conf.source_field, *conf.group_by_source])
        tgt_cols = ", ".join(q(c) for c in [conf.target_field, *conf.group_by_target])
        source = self._aggregate(
            self._rows(ctx, f"SELECT {src_cols} FROM {self._source_from(ctx)}"), conf.agg
        )
        target = self._aggregate(
            self._rows(ctx, f"SELECT {tgt_cols} FROM {q(self.target_table(ctx))}"), conf.agg
        )
        tolerance = Decimal(str(conf.tolerance))
        for group in sorted(set(source) | set(target), key=lambda g: tuple(map(str, g))):
            s, t = source.get(group), target.get(group)
            if not self._equal(s, t, tolerance):
                where = f"group {'|'.join(group)}: " if group else ""
                yield Failure(
                    None,
                    f"{conf.source_field} -> {conf.target_field}",
                    f"{s} vs {t}",
                    f"{where}{conf.agg} is {s} in the source but {t} in the target",
                )

    @staticmethod
    def _equal(s: Any, t: Any, tolerance: Decimal) -> bool:
        if s is None or t is None:
            return s is t
        if isinstance(s, Decimal) and isinstance(t, Decimal):
            return abs(s - t) <= tolerance
        return bool(s == t)

    @staticmethod
    def _aggregate(rows: Iterator[Any], agg: str) -> dict[tuple[str, ...], Any]:
        values: dict[tuple[str, ...], list[Any]] = defaultdict(list)
        for row in rows:
            group = tuple("" if g is None else str(g).strip() for g in row[1:])
            values[group].append(row[0])
        result: dict[tuple[str, ...], Any] = {}
        for group, items in values.items():
            present = [v for v in items if v is not None and str(v).strip() != ""]
            if agg == "count":
                result[group] = Decimal(len(present))
            elif agg == "count_distinct":
                result[group] = Decimal(len({str(v).strip() for v in present}))
            else:
                numbers = [_num(v) for v in present]
                if any(n is None for n in numbers):
                    bad = next(v for v, n in zip(present, numbers, strict=True) if n is None)
                    raise CheckConfigError(f"non-numeric value '{bad}' cannot be used for {agg}")
                nums: list[Decimal] = [n for n in numbers if n is not None]
                if agg == "sum":
                    result[group] = sum(nums, Decimal(0))
                elif nums:
                    result[group] = min(nums) if agg == "min" else max(nums)
                else:
                    result[group] = None
        return result


class FieldsRecon(ReconCheck):
    check_type = "RECON_FIELDS"

    def _pairs(self, ctx: CheckContext) -> list[tuple[str, str]]:
        conf = self.conf
        assert isinstance(conf, cfg.FieldsRecon)
        if conf.fields != "auto":
            return [(p.source, p.target) for p in conf.fields]
        mapping = ctx.obj.mappings.get(self.structure)
        if mapping is None:
            raise CheckConfigError("'fields: auto' needs a mapped structure; list the fields")
        pairs = [
            (f.rule.source_field, f.target_field)
            for f in mapping.fields
            if isinstance(f.rule, cfg.MoveRule)
        ]
        if not pairs:
            raise CheckConfigError("'fields: auto' found no MOVE rules to compare")
        return pairs

    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        conf = self.conf
        assert isinstance(conf, cfg.FieldsRecon)
        pairs = self._pairs(ctx)
        self._prepare(
            ctx,
            [*conf.source_key, *(p[0] for p in pairs)],
            [*conf.target_key, *(p[1] for p in pairs)],
        )
        nk = len(conf.source_key)
        tgt_cols = ", ".join(q(c) for c in [*conf.target_key, *(p[1] for p in pairs)])
        target: dict[tuple[str | None, ...], list[Any]] = {}
        for row in self._rows(ctx, f"SELECT {tgt_cols} FROM {q(self.target_table(ctx))}"):
            target.setdefault(tuple(_norm(v, trim=conf.trim) for v in row[:nk]), list(row[nk:]))

        src_cols = ", ".join(
            "s." + q(c) for c in [ROW_NO, *conf.source_key, *(p[0] for p in pairs)]
        )
        tolerance = Decimal(str(conf.tolerance))
        for row in self._rows(ctx, f"SELECT {src_cols} FROM {self._source_from(ctx)} ORDER BY 1"):
            key = tuple(_norm(v, trim=conf.trim) for v in row[1 : 1 + nk])
            other = target.get(key)
            if other is None:
                continue  # missing keys are the KEYS reconciliation's business
            for (src, tgt), sv, tv in zip(pairs, row[1 + nk :], other, strict=True):
                if not self._same(sv, tv, conf, tolerance):
                    yield Failure(
                        row[0],
                        f"{src} -> {tgt}",
                        f"{sv} | {tv}",
                        f"{'|'.join(k or '' for k in key)}: {src} is {sv!r} in the source "
                        f"but {tgt} is {tv!r} in the target",
                    )

    @staticmethod
    def _same(sv: Any, tv: Any, conf: cfg.FieldsRecon, tolerance: Decimal) -> bool:
        if tolerance > 0:
            a, b = _num(sv), _num(tv)
            if a is not None and b is not None:
                return abs(a - b) <= tolerance
        return _norm(sv, trim=conf.trim, lower=conf.ignore_case) == _norm(
            tv, trim=conf.trim, lower=conf.ignore_case
        )


def build_recons(recons: list[cfg.ReconConfig]) -> list[Check]:
    checks: list[Check] = []
    for conf in recons:
        if isinstance(conf, cfg.CountRecon):
            checks.append(CountRecon(conf))
        elif isinstance(conf, cfg.KeysRecon):
            checks.append(KeysRecon(conf))
        elif isinstance(conf, cfg.TotalsRecon):
            checks.append(TotalsRecon(conf))
        else:
            checks.append(FieldsRecon(conf))
    return checks
