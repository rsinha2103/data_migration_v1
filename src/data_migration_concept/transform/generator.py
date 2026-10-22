"""Generate SQL for a target structure from declarative mapping rules or a custom SQL file.

The generated SQL is deliberately plain ANSI that runs unchanged on SQLite and PostgreSQL
(double-quoted identifiers, `||`, `CAST(.. AS TEXT)`, `SUBSTR`). For other databases it is
transpiled with sqlglot on a best-effort basis. Raw `SQL` rules and custom SQL files are
inserted as written, so they must already be valid for the working database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from jinja2 import Environment, StrictUndefined, TemplateError

from data_migration_concept.config.loader import LoadedObject, LoadedProject
from data_migration_concept.config.models import (
    ConcatRule,
    ConstantRule,
    MoveRule,
    NotMigratedRule,
    Rule,
    SqlRule,
    StructureConfig,
    SubstringRule,
    XrefRule,
)
from data_migration_concept.transform.errors import TransformError

NATIVE_DIALECTS = {"sqlite", "postgresql"}
SQLGLOT_DIALECTS = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "mssql": "tsql",
    "oracle": "oracle",
}
LINEAGE = ("_dmc_row_no", "_dmc_run_id")
MAX_UNMAPPED_SHOWN = 20


def q(name: str) -> str:
    """Quote an identifier."""
    return '"' + name.replace('"', '""') + '"'


def lit(value: str | int | float | bool | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + value.replace("'", "''") + "'"


def staging_table(project: LoadedProject, obj: LoadedObject, source: str) -> str:
    return f"{project.config.staging_prefix}{obj.config.name}_{source}"


def xref_table(project: LoadedProject, obj: LoadedObject, xref: str) -> str:
    return f"{project.config.xref_prefix}{obj.config.name}_{xref}"


def target_table(project: LoadedProject, st: StructureConfig) -> str:
    return f"{project.config.target_prefix}{st.target_table}"


@dataclass
class XrefCheck:
    """Query returning distinct source values that have no XREF entry (policy 'error')."""

    field: str
    xref: str
    sql: str


@dataclass
class GeneratedStructure:
    structure: str
    target_table: str
    source_table: str
    statements: list[str]
    xrefs: dict[str, str] = field(default_factory=dict)  # xref name -> table, rules only
    xref_checks: list[XrefCheck] = field(default_factory=list)

    def render(self) -> str:
        return ";\n\n".join(self.statements) + ";\n"


def _text(col: str) -> str:
    return f"CAST({col} AS TEXT)"


def generate_structure(
    project: LoadedProject, obj: LoadedObject, st: StructureConfig
) -> GeneratedStructure:
    src_table = staging_table(project, obj, st.source)
    tgt = target_table(project, st)
    drop = f"DROP TABLE IF EXISTS {q(tgt)}"
    if st.custom_sql is not None:
        select = _render_custom_sql(project, obj, st)
        return GeneratedStructure(
            st.name, tgt, src_table, [drop, f"CREATE TABLE {q(tgt)} AS\n{select}"]
        )
    mapping = obj.mappings[st.name]

    columns = [f"  s.{q(c)} AS {q(c)}" for c in LINEAGE]
    joins: list[str] = []
    gen = GeneratedStructure(st.name, tgt, src_table, [])
    for fld in mapping.fields:
        if isinstance(fld.rule, NotMigratedRule):
            continue
        expr = _expression(fld.rule, gen, joins, project, obj, src_table, fld.target_field)
        columns.append(f"  {expr} AS {q(fld.target_field)}")
    if len(columns) == len(LINEAGE):
        raise TransformError(f"structure '{st.name}': every field is NOT_MIGRATED")

    select = "SELECT\n" + ",\n".join(columns) + f"\nFROM {q(src_table)} AS s"
    if joins:
        select += "\n" + "\n".join(joins)
    gen.statements = [drop, f"CREATE TABLE {q(tgt)} AS\n{select}"]
    return gen


def _expression(
    rule: Rule,
    gen: GeneratedStructure,
    joins: list[str],
    project: LoadedProject,
    obj: LoadedObject,
    src_table: str,
    target_field: str,
) -> str:
    if isinstance(rule, MoveRule):
        return f"s.{q(rule.source_field)}"
    if isinstance(rule, ConstantRule):
        return lit(rule.value)
    if isinstance(rule, ConcatRule):
        parts = [f"COALESCE({_text('s.' + q(f))}, '')" for f in rule.source_fields]
        return (
            f" || {lit(rule.separator)} || ".join(parts) if rule.separator else " || ".join(parts)
        )
    if isinstance(rule, SubstringRule):
        col = _text("s." + q(rule.source_field))
        return f"SUBSTR({col}, {rule.start}, {rule.length})"
    if isinstance(rule, SqlRule):
        return f"({rule.expression})"
    if isinstance(rule, XrefRule):
        alias = f"x{len(joins) + 1}"
        table = xref_table(project, obj, rule.xref)
        gen.xrefs[rule.xref] = table
        key = _text("s." + q(rule.source_field))
        joins.append(f"LEFT JOIN {q(table)} AS {alias}\n  ON {key} = {alias}.{q('legacy_value')}")
        value = f"{alias}.{q('target_value')}"
        if rule.on_unmapped == "default":
            return f"COALESCE({value}, {lit(rule.default)})"
        if rule.on_unmapped == "error":
            gen.xref_checks.append(
                XrefCheck(
                    field=target_field,
                    xref=rule.xref,
                    sql=(
                        f"SELECT DISTINCT {key} AS value\n"
                        f"FROM {q(src_table)} AS s\n"
                        f"LEFT JOIN {q(table)} AS x ON {key} = x.{q('legacy_value')}\n"
                        f"WHERE s.{q(rule.source_field)} IS NOT NULL "
                        f"AND x.{q('legacy_value')} IS NULL\n"
                        f"ORDER BY 1 LIMIT {MAX_UNMAPPED_SHOWN}"
                    ),
                )
            )
        return value
    raise TransformError(f"unsupported rule {rule!r}")  # pragma: no cover


_jinja = Environment(undefined=StrictUndefined, keep_trailing_newline=False, autoescape=False)


def _render_custom_sql(project: LoadedProject, obj: LoadedObject, st: StructureConfig) -> str:
    """Render a custom SQL file. Available names: stg.<source>, xref.<name>, tgt.<structure>."""
    assert st.custom_sql is not None
    text = (obj.root / st.custom_sql).read_text(encoding="utf-8")
    return render_sql_template(project, obj, text, f"structure '{st.name}': {st.custom_sql}")


def render_sql_template(project: LoadedProject, obj: LoadedObject, text: str, label: str) -> str:
    """Render a SQL template (custom SQL file or SQL_ASSERT check) and strip the trailing `;`."""
    xref_dir = obj.root / "xref"
    context = {
        "stg": {s.name: staging_table(project, obj, s.name) for s in obj.config.sources},
        "xref": {
            p.stem: xref_table(project, obj, p.stem)
            for p in (sorted(xref_dir.glob("*.csv")) if xref_dir.is_dir() else [])
        },
        "tgt": {s.name: target_table(project, s) for s in obj.config.structures},
    }
    try:
        sql = _jinja.from_string(text).render(**context)
    except TemplateError as exc:
        raise TransformError(f"{label}: {exc}") from exc
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise TransformError(f"{label}: SQL is empty")
    return sql


def to_dialect(sql: str, dialect: str) -> str:
    """Return SQL for the given SQLAlchemy dialect name (best effort outside SQLite/Postgres)."""
    if dialect in NATIVE_DIALECTS:
        return sql
    target = SQLGLOT_DIALECTS.get(dialect)
    if target is None:
        raise TransformError(f"unsupported SQL dialect '{dialect}'")
    return sqlglot.transpile(sql, read="postgres", write=target)[0]
