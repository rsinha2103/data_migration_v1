"""Check framework: every check produces a CheckResult; failing rows become rejects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from data_migration_concept.transform.generator import q

if TYPE_CHECKING:
    from data_migration_concept.config.loader import LoadedObject, LoadedProject

MAX_REJECTS_PER_CHECK = 10_000
MAX_SAMPLES = 20

Severity = Literal["error", "warn"]


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"  # the check itself could not run (bad column, bad SQL, ...)


class CheckConfigError(Exception):
    """A check refers to something that does not exist in the target data."""


class CheckRunError(Exception):
    """Checks cannot run at all (unknown object, target tables missing)."""


@dataclass
class Failure:
    row_no: int | None
    field: str | None
    value: str | None
    message: str


@dataclass
class CheckResult:
    name: str
    check_type: str
    structure: str
    field: str | None
    severity: Severity
    status: CheckStatus
    checked_count: int | None = None
    failed_count: int = 0
    truncated: bool = False
    message: str = ""
    failures: list[Failure] = dc_field(default_factory=list)  # capped at MAX_REJECTS_PER_CHECK

    @property
    def samples(self) -> list[Failure]:
        return self.failures[:MAX_SAMPLES]

    @property
    def blocking(self) -> bool:
        """Blocks the load step: failed error-severity checks, and any check that could not run."""
        return self.status == CheckStatus.ERROR or (
            self.status == CheckStatus.FAIL and self.severity == "error"
        )


@dataclass
class CheckContext:
    engine: Engine
    project: LoadedProject
    obj: LoadedObject
    target_tables: dict[str, str]  # structure name -> target table in the working DB
    source_tables: dict[str, str] = dc_field(default_factory=dict)  # structure -> staging table

    def table(self, structure: str, obj: LoadedObject | None = None) -> str:
        if obj is None or obj is self.obj:
            return self.target_tables[structure]
        st = next(s for s in obj.config.structures if s.name == structure)
        return f"{self.project.config.target_prefix}{st.target_table}"

    def columns(self, table: str) -> set[str]:
        return {c["name"] for c in inspect(self.engine).get_columns(table)}

    def row_count(self, table: str) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {q(table)}")).scalar_one())

    def require_columns(self, table: str, fields: list[str]) -> None:
        missing = [f for f in fields if f not in self.columns(table)]
        if missing:
            raise CheckConfigError(f"column(s) not found in {table}: {', '.join(missing)}")


class Check(ABC):
    check_type: str
    unit = "row(s)"  # what one Failure represents, for the summary message

    def __init__(
        self,
        name: str,
        structure: str,
        severity: Severity = "error",
        field: str | None = None,
    ) -> None:
        self.name = name
        self.structure = structure
        self.severity: Severity = severity
        self.field = field

    def count_rows(self, ctx: CheckContext) -> int:
        """Rows examined; reported as checked_count."""
        return ctx.row_count(ctx.table(self.structure))

    @abstractmethod
    def failures(self, ctx: CheckContext) -> Iterator[Failure]:
        """Yield one Failure per offending row."""

    def run(self, ctx: CheckContext) -> CheckResult:
        result = CheckResult(
            name=self.name,
            check_type=self.check_type,
            structure=self.structure,
            field=self.field,
            severity=self.severity,
            status=CheckStatus.PASS,
        )
        try:
            result.checked_count = self.count_rows(ctx)
            for failure in self.failures(ctx):
                result.failed_count += 1
                if len(result.failures) < MAX_REJECTS_PER_CHECK:
                    result.failures.append(failure)
                else:
                    result.truncated = True
        except (CheckConfigError, SQLAlchemyError) as exc:
            result.status = CheckStatus.ERROR
            result.message = str(getattr(exc, "orig", None) or exc).splitlines()[0]
            return result
        if result.failed_count:
            result.status = CheckStatus.FAIL
            result.message = f"{result.failed_count} {self.unit} failed"
        return result
