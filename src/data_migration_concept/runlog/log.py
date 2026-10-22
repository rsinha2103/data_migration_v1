"""Run log: every run and step is recorded in the working database (tables `dmc_run*`)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)

metadata = MetaData()

run_table = Table(
    "dmc_run",
    metadata,
    Column("run_id", String(36), primary_key=True),
    Column("project_name", String(200), nullable=False),
    Column("object_name", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
)

step_table = Table(
    "dmc_run_step",
    metadata,
    Column("step_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("dmc_run.run_id"), nullable=False),
    Column("step_name", String(100), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("rows_in", Integer),
    Column("rows_out", Integer),
    Column("message", Text),
)


schema_table = Table(
    "dmc_run_schema",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("dmc_run.run_id"), nullable=False),
    Column("table_name", String(200), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("column_name", String(200), nullable=False),
    Column("data_type", String(100), nullable=False),
)


check_table = Table(
    "dmc_run_check",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("dmc_run.run_id"), nullable=False),
    Column("check_name", String(300), nullable=False),
    Column("check_type", String(50), nullable=False),
    Column("structure", String(200), nullable=False),
    Column("field", String(300)),
    Column("severity", String(10), nullable=False),
    Column("status", String(10), nullable=False),  # PASS / FAIL / ERROR
    Column("checked_count", Integer),
    Column("failed_count", Integer),
    Column("truncated", Boolean, nullable=False, default=False),
    Column("message", Text),
)

reject_table = Table(
    "dmc_reject",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("dmc_run.run_id"), nullable=False),
    Column("check_name", String(300), nullable=False),
    Column("structure", String(200), nullable=False),
    Column("severity", String(10), nullable=False),
    Column("row_no", BigInteger),  # _dmc_row_no: row number in the staged source
    Column("field", String(300)),
    Column("value", Text),
    Column("message", Text),
)


profile_table_ = Table(
    "dmc_profile",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("dmc_run.run_id"), nullable=False),
    Column("table_name", String(200), nullable=False),
    Column("column_name", String(200), nullable=False),
    Column("row_count", BigInteger, nullable=False),
    Column("empty_count", BigInteger, nullable=False),
    Column("distinct_count", BigInteger, nullable=False),
    Column("min_value", Text),
    Column("max_value", Text),
    Column("min_length", Integer),
    Column("max_length", Integer),
    Column("top_values", Text),  # JSON: [[value, count], ...]
    Column("top_patterns", Text),  # JSON: [[pattern, count], ...]
)


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class StepRecorder:
    """Handed to the body of `RunLog.step()` so a step can report row counts."""

    rows_in: int | None = None
    rows_out: int | None = None
    message: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


class RunLog:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def ensure_schema(self) -> None:
        metadata.create_all(self.engine)

    def start_run(self, project_name: str, object_name: str) -> str:
        run_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                insert(run_table).values(
                    run_id=run_id,
                    project_name=project_name,
                    object_name=object_name,
                    status=RunStatus.RUNNING,
                    started_at=_now(),
                )
            )
        return run_id

    def finish_run(self, run_id: str, status: RunStatus) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(run_table)
                .where(run_table.c.run_id == run_id)
                .values(status=status, finished_at=_now())
            )

    def start_step(self, run_id: str, step_name: str) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                insert(step_table).values(
                    run_id=run_id,
                    step_name=step_name,
                    status=RunStatus.RUNNING,
                    started_at=_now(),
                )
            )
            pk = result.inserted_primary_key
            assert pk is not None
            return int(pk[0])

    def finish_step(
        self,
        step_id: int,
        status: RunStatus,
        *,
        rows_in: int | None = None,
        rows_out: int | None = None,
        message: str | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(step_table)
                .where(step_table.c.step_id == step_id)
                .values(
                    status=status,
                    finished_at=_now(),
                    rows_in=rows_in,
                    rows_out=rows_out,
                    message=message,
                )
            )

    @contextmanager
    def step(self, run_id: str, step_name: str) -> Iterator[StepRecorder]:
        """Record a step; marks it FAILED (and re-raises) if the body raises."""
        step_id = self.start_step(run_id, step_name)
        rec = StepRecorder()
        try:
            yield rec
        except Exception as exc:
            self.finish_step(
                step_id,
                RunStatus.FAILED,
                rows_in=rec.rows_in,
                rows_out=rec.rows_out,
                message=f"{type(exc).__name__}: {exc}",
            )
            raise
        self.finish_step(
            step_id,
            RunStatus.SUCCESS,
            rows_in=rec.rows_in,
            rows_out=rec.rows_out,
            message=rec.message,
        )

    def record_schema(self, run_id: str, table_name: str, columns: list[tuple[str, str]]) -> None:
        """Capture the (column, type) layout of a table as it was in this run."""
        if not columns:
            return
        with self.engine.begin() as conn:
            conn.execute(
                insert(schema_table),
                [
                    {
                        "run_id": run_id,
                        "table_name": table_name,
                        "ordinal": i,
                        "column_name": name,
                        "data_type": dtype,
                    }
                    for i, (name, dtype) in enumerate(columns, start=1)
                ],
            )

    def get_schema(self, run_id: str, table_name: str) -> list[tuple[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(schema_table.c.column_name, schema_table.c.data_type)
                .where(schema_table.c.run_id == run_id, schema_table.c.table_name == table_name)
                .order_by(schema_table.c.ordinal)
            )
            return [(r[0], r[1]) for r in rows]

    def record_check(self, run_id: str, **values: Any) -> None:
        """Store one check result. Keys: check_name, check_type, structure, field, severity,
        status, checked_count, failed_count, truncated, message."""
        with self.engine.begin() as conn:
            conn.execute(insert(check_table).values(run_id=run_id, **values))

    def record_rejects(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        """Store failing rows. Keys per row: check_name, structure, severity, row_no, field,
        value, message."""
        if not rows:
            return
        with self.engine.begin() as conn:
            conn.execute(insert(reject_table), [{"run_id": run_id, **r} for r in rows])

    def record_profile(self, run_id: str, table_name: str, profiles: list[Any]) -> None:
        """Store column profiles (objects with the ColumnProfile attributes)."""
        if not profiles:
            return
        rows = [
            {
                "run_id": run_id,
                "table_name": table_name,
                "column_name": p.column,
                "row_count": p.row_count,
                "empty_count": p.empty_count,
                "distinct_count": p.distinct_count,
                "min_value": p.min_value,
                "max_value": p.max_value,
                "min_length": p.min_length,
                "max_length": p.max_length,
                "top_values": json.dumps(p.top_values),
                "top_patterns": json.dumps(p.top_patterns),
            }
            for p in profiles
        ]
        with self.engine.begin() as conn:
            conn.execute(insert(profile_table_), rows)

    def get_profile(self, run_id: str, table_name: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(profile_table_)
                .where(profile_table_.c.run_id == run_id, profile_table_.c.table_name == table_name)
                .order_by(profile_table_.c.id)
            ).mappings()
            return [dict(r) for r in rows]

    def get_checks(self, run_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(check_table).where(check_table.c.run_id == run_id).order_by(check_table.c.id)
            ).mappings()
            return [dict(r) for r in rows]

    def get_rejects(self, run_id: str, check_name: str | None = None) -> list[dict[str, Any]]:
        stmt = select(reject_table).where(reject_table.c.run_id == run_id)
        if check_name is not None:
            stmt = stmt.where(reject_table.c.check_name == check_name)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt.order_by(reject_table.c.id)).mappings()
            return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = (
                conn.execute(select(run_table).where(run_table.c.run_id == run_id))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def list_runs(
        self,
        project_name: str | None = None,
        object_name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Runs, newest first."""
        stmt = select(run_table)
        if project_name is not None:
            stmt = stmt.where(run_table.c.project_name == project_name)
        if object_name is not None:
            stmt = stmt.where(run_table.c.object_name == object_name)
        stmt = stmt.order_by(run_table.c.started_at.desc(), run_table.c.run_id).limit(limit)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings()]

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(step_table)
                .where(step_table.c.run_id == run_id)
                .order_by(step_table.c.step_id)
            ).mappings()
            return [dict(r) for r in rows]
