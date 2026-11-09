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
    FieldMapping,
    JoinConfig,
    MoveRule,
    NotMigratedRule,
    RowSet,
    Rule,
    SqlRule,
    StructureConfig,
    SubstringRule,
    UnmappedRule,
    XrefRule,
    split_ref,
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
class JoinPlan:
    """A joined source of a structure, as the generator and the duplicate-key check see it."""

    source: str  # also the SQL alias
    table: str
    keys: list[tuple[str, str]]  # (main column or alias.column, column of the joined source)
    on_duplicates: str
    type: str


@dataclass
class GeneratedStructure:
    structure: str
    target_table: str
    source_table: str
    statements: list[str]
    xrefs: dict[str, str] = field(default_factory=dict)  # xref name -> table, rules only
    xref_checks: list[XrefCheck] = field(default_factory=list)
    joins: list[JoinPlan] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # staging tables of the extra row sets

    @property
    def source_tables(self) -> list[str]:
        """Every staging table whose rows end up in the target (own source first)."""
        return [self.source_table, *self.sources]

    def render(self) -> str:
        return ";\n\n".join(self.statements) + ";\n"


def _text(col: str) -> str:
    return f"CAST({col} AS TEXT)"


class Refs:
    """Turns a column reference of a mapping rule into SQL: `COL` is read from the structure's
    own source (alias s), `adrc.COL` from the joined source adrc."""

    def __init__(self, joins: list[JoinConfig]) -> None:
        self.joined = {j.source for j in joins}

    def col(self, ref: str) -> str:
        alias, column = split_ref(ref, self.joined)
        return f"{alias or 's'}.{q(column)}"


@dataclass
class Plan:
    """What a structure's SELECT is made of; shared by the transform and the preview."""

    gen: GeneratedStructure
    fields: list[tuple[FieldMapping, str]]  # (field, SQL expression), NOT_MIGRATED left out
    from_sql: str  # FROM ... AS s, the joined sources, then the XREF joins
    refs: Refs


def _source_joins(
    project: LoadedProject, obj: LoadedObject, rs: RowSet, refs: Refs
) -> tuple[list[str], list[JoinPlan]]:
    clauses: list[str] = []
    plans: list[JoinPlan] = []
    for j in rs.joins:
        table = staging_table(project, obj, j.source)
        on = " AND ".join(
            f"{_text(refs.col(k.main))} = {_text(j.source + '.' + q(k.other))}" for k in j.keys
        )
        if j.on_duplicates == "first":
            group = ", ".join(_text(q(k.other)) for k in j.keys)
            target = (
                f"(SELECT * FROM {q(table)} WHERE {q('_dmc_row_no')} IN "
                f"(SELECT MIN({q('_dmc_row_no')}) FROM {q(table)} GROUP BY {group}))"
            )
        else:
            target = q(table)
        kind = "INNER" if j.type == "inner" else "LEFT"
        clauses.append(f"{kind} JOIN {target} AS {j.source}\n  ON {on}")
        plans.append(
            JoinPlan(j.source, table, [(k.main, k.other) for k in j.keys], j.on_duplicates, j.type)
        )
    return clauses, plans


def effective_rule(rs: RowSet, fld: FieldMapping) -> Rule:
    """The rule a field uses in a row set: its override, else the mapping's rule."""
    return rs.overrides.get(fld.target_field, fld.rule)


def plan_structure(
    project: LoadedProject, obj: LoadedObject, st: StructureConfig, rs: RowSet | None = None
) -> Plan:
    """Work out the expressions and joins of a mapped structure (one row set of it)."""
    rs = rs or st.row_sets()[0]
    src_table = staging_table(project, obj, rs.source)
    mapping = obj.mappings[st.name]
    refs = Refs(rs.joins)
    gen = GeneratedStructure(st.name, target_table(project, st), src_table, [])
    source_joins, gen.joins = _source_joins(project, obj, rs, refs)
    xref_joins: list[str] = []
    fields: list[tuple[FieldMapping, str]] = []
    for fld in mapping.fields:
        rule = effective_rule(rs, fld)
        if isinstance(rule, NotMigratedRule):
            continue
        expr = _expression(
            rule, gen, xref_joins, project, obj, src_table, fld.target_field, refs, source_joins
        )
        fields.append((fld, expr))
    from_sql = f"FROM {q(src_table)} AS s"
    clauses = [*source_joins, *xref_joins]
    if clauses:
        from_sql += "\n" + "\n".join(clauses)
    return Plan(gen, fields, from_sql, refs)


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
    selects: list[str] = []
    gen: GeneratedStructure | None = None
    for rs in st.row_sets():
        plan = plan_structure(project, obj, st, rs)
        columns = [f"  s.{q(c)} AS {q(c)}" for c in LINEAGE]
        columns += [f"  {expr} AS {q(fld.target_field)}" for fld, expr in plan.fields]
        if len(columns) == len(LINEAGE):
            raise TransformError(f"structure '{st.name}': every field is NOT_MIGRATED")
        selects.append("SELECT\n" + ",\n".join(columns) + "\n" + plan.from_sql)
        if gen is None:
            gen = plan.gen
        else:  # more row sets: collect what they read and check
            gen.sources.append(plan.gen.source_table)
            gen.joins += plan.gen.joins
            gen.xrefs.update(plan.gen.xrefs)
            gen.xref_checks += plan.gen.xref_checks
    assert gen is not None
    gen.statements = [drop, f"CREATE TABLE {q(tgt)} AS\n" + "\nUNION ALL\n".join(selects)]
    return gen


def _expression(
    rule: Rule,
    gen: GeneratedStructure,
    xref_joins: list[str],
    project: LoadedProject,
    obj: LoadedObject,
    src_table: str,
    target_field: str,
    refs: Refs,
    source_joins: list[str],
) -> str:
    if isinstance(rule, MoveRule):
        return refs.col(rule.source_field)
    if isinstance(rule, ConstantRule):
        return lit(rule.value)
    if isinstance(rule, ConcatRule):
        parts = [f"COALESCE({_text(refs.col(f))}, '')" for f in rule.source_fields]
        return (
            f" || {lit(rule.separator)} || ".join(parts) if rule.separator else " || ".join(parts)
        )
    if isinstance(rule, SubstringRule):
        col = _text(refs.col(rule.source_field))
        return f"SUBSTR({col}, {rule.start}, {rule.length})"
    if isinstance(rule, SqlRule):
        # {{ stg.<source> }}, {{ xref.<name> }} and {{ tgt.<structure> }} name the real tables
        sql = render_sql_template(project, obj, rule.expression, f"field '{target_field}'")
        return f"({sql})"
    if isinstance(rule, UnmappedRule):
        return "CAST(NULL AS TEXT)"
    if isinstance(rule, XrefRule):
        alias = f"x{len(xref_joins) + 1}"
        table = xref_table(project, obj, rule.xref)
        gen.xrefs[rule.xref] = table
        source_col = refs.col(rule.source_field)
        key = _text(source_col)
        xref_joins.append(
            f"LEFT JOIN {q(table)} AS {alias}\n  ON {key} = {alias}.{q('legacy_value')}"
        )
        value = f"{alias}.{q('target_value')}"
        if rule.on_unmapped == "default":
            return f"COALESCE({value}, {lit(rule.default)})"
        if rule.on_unmapped == "error":
            joined = "".join(f"{c}\n" for c in source_joins)
            gen.xref_checks.append(
                XrefCheck(
                    field=target_field,
                    xref=rule.xref,
                    sql=(
                        f"SELECT DISTINCT {key} AS value\n"
                        f"FROM {q(src_table)} AS s\n"
                        f"{joined}"
                        f"LEFT JOIN {q(table)} AS x ON {key} = x.{q('legacy_value')}\n"
                        f"WHERE {source_col} IS NOT NULL "
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
