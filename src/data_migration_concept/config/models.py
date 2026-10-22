"""Pydantic models describing a migration project, its objects and mapping rules."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Names that become part of table names must be safe SQL identifiers.
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- project.yaml -------------------------------------------------------------


class ConnectionConfig(_Base):
    """A database connection. The SQLAlchemy URL is read from an environment variable."""

    url_env: str = Field(description="Name of the environment variable holding the URL.")


class ProjectConfig(_Base):
    name: str
    description: str = ""
    working_connection: str = Field(description="Connection used for staging/target tables.")
    connections: dict[str, ConnectionConfig]
    staging_prefix: str = "stg_"
    target_prefix: str = "tgt_"
    xref_prefix: str = "xref_"
    objects_dir: str = "objects"

    @model_validator(mode="after")
    def _working_connection_defined(self) -> ProjectConfig:
        if self.working_connection not in self.connections:
            raise ValueError(f"working_connection '{self.working_connection}' is not defined")
        return self


# --- mapping rules ------------------------------------------------------------


class MoveRule(_Base):
    type: Literal["MOVE"]
    source_field: str


class ConstantRule(_Base):
    type: Literal["CONSTANT"]
    value: str | int | float | bool | None


class XrefRule(_Base):
    """Cross-reference (value mapping) lookup against `xref/<xref>.csv`."""

    type: Literal["XREF"]
    source_field: str
    xref: str
    on_unmapped: Literal["error", "default", "blank"] = "error"
    default: str | None = None

    @model_validator(mode="after")
    def _default_required(self) -> XrefRule:
        if self.on_unmapped == "default" and self.default is None:
            raise ValueError("'default' is required when on_unmapped is 'default'")
        return self


class ConcatRule(_Base):
    type: Literal["CONCAT"]
    source_fields: Annotated[list[str], Field(min_length=2)]
    separator: str = ""


class SubstringRule(_Base):
    type: Literal["SUBSTRING"]
    source_field: str
    start: Annotated[int, Field(ge=1)] = 1
    length: Annotated[int, Field(ge=1)]


class NotMigratedRule(_Base):
    type: Literal["NOT_MIGRATED"]


class SqlRule(_Base):
    """Free SQL expression evaluated per row against the staging table."""

    type: Literal["SQL"]
    expression: str


Rule = Annotated[
    MoveRule | ConstantRule | XrefRule | ConcatRule | SubstringRule | NotMigratedRule | SqlRule,
    Field(discriminator="type"),
]


def rule_source_fields(rule: Rule) -> list[str]:
    """Source columns a rule reads. SQL rules are free-form, so they report none."""
    if isinstance(rule, ConcatRule):
        return list(rule.source_fields)
    if isinstance(rule, MoveRule | XrefRule | SubstringRule):
        return [rule.source_field]
    return []


class FieldMapping(_Base):
    target_field: str
    description: str = ""
    data_type: Literal["string", "integer", "decimal", "date", "boolean"] = "string"
    length: Annotated[int, Field(gt=0)] | None = None
    mandatory: bool = False
    rule: Rule


class MappingFile(_Base):
    """Content of `mappings/<structure>.yaml`."""

    fields: Annotated[list[FieldMapping], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_targets(self) -> MappingFile:
        seen: set[str] = set()
        for f in self.fields:
            if f.target_field in seen:
                raise ValueError(f"duplicate target_field '{f.target_field}'")
            seen.add(f.target_field)
        return self


# --- checks.yaml --------------------------------------------------------------


class _CheckBase(_Base):
    name: Identifier
    structure: str
    severity: Literal["error", "warn"] = "error"
    description: str = ""


class UniqueCheck(_CheckBase):
    type: Literal["UNIQUE"]
    fields: Annotated[list[str], Field(min_length=1)]


class AllowedValuesCheck(_CheckBase):
    type: Literal["ALLOWED_VALUES"]
    field: str
    values: Annotated[list[str | int | float | bool], Field(min_length=1)]


class ReferentialCheck(_CheckBase):
    """Every non-empty `field` value must exist in `ref_field` of another structure."""

    type: Literal["REFERENTIAL"]
    field: str
    ref_structure: str
    ref_field: str
    ref_object: str | None = Field(default=None, description="Defaults to the same object.")


class SqlAssertCheck(_CheckBase):
    """A query returning the offending rows (include `_dmc_row_no` to point at the row).
    Jinja names: tgt.<structure>, stg.<source>, xref.<name>."""

    type: Literal["SQL_ASSERT"]
    sql: str


class NullRateCheck(_CheckBase):
    """Share of empty values in a field must not exceed `max_pct` percent."""

    type: Literal["NULL_RATE"]
    field: str
    max_pct: Annotated[float, Field(ge=0, le=100)]


class PatternCheck(_CheckBase):
    """Every non-empty value must match the regular expression in full."""

    type: Literal["PATTERN"]
    field: str
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _valid_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value


class RangeCheck(_CheckBase):
    """Numeric values must lie within [min, max]; non-numeric values fail."""

    type: Literal["RANGE"]
    field: str
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds(self) -> RangeCheck:
        if self.min is None and self.max is None:
            raise ValueError("at least one of 'min' or 'max' is required")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("'min' must not be greater than 'max'")
        return self


class OutlierCheck(_CheckBase):
    """Flags numeric values further than `max_zscore` standard deviations from the mean."""

    type: Literal["OUTLIER"]
    field: str
    max_zscore: Annotated[float, Field(gt=0)] = 4.0


CheckConfig = Annotated[
    UniqueCheck
    | AllowedValuesCheck
    | ReferentialCheck
    | SqlAssertCheck
    | NullRateCheck
    | PatternCheck
    | RangeCheck
    | OutlierCheck,
    Field(discriminator="type"),
]


class ChecksFile(_Base):
    """Content of `checks.yaml`. Mandatory, length and data-type checks are derived
    automatically from the mapping and need no entry here."""

    checks: list[CheckConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> ChecksFile:
        names = [c.name for c in self.checks]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate check name(s): {', '.join(sorted(dupes))}")
        return self


# --- recon.yaml ---------------------------------------------------------------

TableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_]\w*$")]


class _ReconBase(_Base):
    """Compares the staged source of `structure` with its target table."""

    name: Identifier
    structure: str
    severity: Literal["error", "warn"] = "error"
    description: str = ""
    target_table: TableName | None = Field(
        default=None,
        description="Compare against this table in the working database instead of the "
        "structure's target table (e.g. an extract of the target system).",
    )
    exclude_source: str | None = Field(
        default=None,
        description="SQL condition over alias s: source rows that are intentionally not "
        "migrated and must not be expected in the target.",
    )


class CountRecon(_ReconBase):
    type: Literal["COUNT"]
    tolerance: Annotated[int, Field(ge=0)] = 0


class _KeyedRecon(_ReconBase):
    source_key: Annotated[list[str], Field(min_length=1)]
    target_key: Annotated[list[str], Field(min_length=1)]

    @model_validator(mode="after")
    def _same_key_length(self) -> _KeyedRecon:
        if len(self.source_key) != len(self.target_key):
            raise ValueError("source_key and target_key must have the same number of fields")
        return self


class KeysRecon(_KeyedRecon):
    """Every source key must exist in the target and vice versa."""

    type: Literal["KEYS"]
    trim: bool = True


class TotalsRecon(_ReconBase):
    """A control total (sum, count, ...) must agree, optionally per group."""

    type: Literal["TOTALS"]
    source_field: str
    target_field: str
    agg: Literal["sum", "count", "count_distinct", "min", "max"] = "sum"
    group_by_source: list[str] = Field(default_factory=list)
    group_by_target: list[str] = Field(default_factory=list)
    tolerance: Annotated[float, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _same_group_length(self) -> TotalsRecon:
        if len(self.group_by_source) != len(self.group_by_target):
            raise ValueError("group_by_source and group_by_target must have the same length")
        return self


class FieldPair(_Base):
    source: str
    target: str


class FieldsRecon(_KeyedRecon):
    """Values of matched rows must agree. `fields: auto` compares every MOVE-rule field."""

    type: Literal["FIELDS"]
    fields: Annotated[list[FieldPair], Field(min_length=1)] | Literal["auto"] = "auto"
    tolerance: Annotated[float, Field(ge=0)] = Field(
        default=0, description="Absolute tolerance for numeric values (0 = exact text match)."
    )
    trim: bool = True
    ignore_case: bool = False


ReconConfig = Annotated[
    CountRecon | KeysRecon | TotalsRecon | FieldsRecon, Field(discriminator="type")
]


class ReconFile(_Base):
    recons: list[ReconConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> ReconFile:
        names = [r.name for r in self.recons]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate reconciliation name(s): {', '.join(sorted(dupes))}")
        return self


# --- object.yaml --------------------------------------------------------------


class FileSource(_Base):
    """A file source. CSV and Excel values are staged as text so nothing is lost
    (e.g. leading zeros in keys); Parquet keeps its native types."""

    kind: Literal["file"]
    name: Identifier
    path: str = Field(description="Path relative to the object folder.")
    format: Literal["csv", "xlsx", "parquet"]
    sheet: str | None = Field(default=None, description="Excel sheet; defaults to the first.")
    delimiter: Annotated[str, Field(min_length=1, max_length=1)] = ","
    encoding: str = "utf-8"


class SqlSource(_Base):
    kind: Literal["sql"]
    name: Identifier
    connection: str
    table: str | None = None
    query: str | None = None

    @model_validator(mode="after")
    def _table_xor_query(self) -> SqlSource:
        if (self.table is None) == (self.query is None):
            raise ValueError("exactly one of 'table' or 'query' is required")
        return self


Source = Annotated[FileSource | SqlSource, Field(discriminator="kind")]


class StructureConfig(_Base):
    """A target structure, built either from a mapping file or from custom SQL."""

    name: Identifier
    target_table: Identifier
    source: str = Field(description="Name of the source feeding this structure.")
    mapping: str | None = Field(default=None, description="Mapping YAML, relative to object.")
    custom_sql: str | None = Field(default=None, description="SQL file, relative to object.")

    @model_validator(mode="after")
    def _mapping_xor_sql(self) -> StructureConfig:
        if (self.mapping is None) == (self.custom_sql is None):
            raise ValueError("exactly one of 'mapping' or 'custom_sql' is required")
        return self


class FileLoad(_Base):
    """Write a structure to a file. `path` is relative to the project folder."""

    kind: Literal["file"]
    structure: str
    path: str
    format: Literal["csv", "xlsx", "parquet"]
    delimiter: Annotated[str, Field(min_length=1, max_length=1)] = ","
    encoding: str = "utf-8"
    include_lineage: bool = Field(default=False, description="Keep _dmc_* columns.")


class TableLoad(_Base):
    """Write a structure to a database table."""

    kind: Literal["table"]
    structure: str
    connection: str
    table: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")]
    mode: Literal["replace", "append"] = "replace"
    include_lineage: bool = False


Load = Annotated[FileLoad | TableLoad, Field(discriminator="kind")]


class ObjectConfig(_Base):
    name: Identifier
    description: str = ""
    sources: Annotated[list[Source], Field(min_length=1)]
    structures: Annotated[list[StructureConfig], Field(min_length=1)]
    loads: list[Load] = Field(default_factory=list)

    @model_validator(mode="after")
    def _loads_unique(self) -> ObjectConfig:
        destinations = [
            ("file", ld.path)
            if isinstance(ld, FileLoad)
            else ("table", f"{ld.connection}:{ld.table}")
            for ld in self.loads
        ]
        dupes = {d[1] for d in destinations if destinations.count(d) > 1}
        if dupes:
            raise ValueError(f"duplicate load destination(s): {', '.join(sorted(dupes))}")
        return self

    @model_validator(mode="after")
    def _unique_names(self) -> ObjectConfig:
        for label, names in (
            ("source", [s.name for s in self.sources]),
            ("structure", [s.name for s in self.structures]),
            ("target_table", [s.target_table for s in self.structures]),
        ):
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise ValueError(f"duplicate {label} name(s): {', '.join(sorted(dupes))}")
        return self
