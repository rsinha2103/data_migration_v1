"""Preview of a mapped structure: the first rows as the transform would produce them, next to
the source values they come from. Nothing is written to the target; the XREF lists are loaded
into their working tables exactly as a transform would (that is the only side effect)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect, text

from data_migration_concept.config.loader import LoadedObject, LoadedProject
from data_migration_concept.config.models import (
    StructureConfig,
    XrefRule,
    rule_source_fields,
)
from data_migration_concept.transform.errors import TransformError
from data_migration_concept.transform.generator import (
    effective_rule,
    plan_structure,
    q,
    to_dialect,
    xref_table,
)
from data_migration_concept.transform.runner import _check_joins, _used_xrefs
from data_migration_concept.transform.xref import load_xref

MAX_ROWS = 50
MAX_UNMAPPED = 10


@dataclass
class PreviewCell:
    value: str | None
    sources: list[str | None]  # the source values this cell was made from
    flag: str = ""  # "", "empty" (a mandatory field has no value) or "long"


@dataclass
class PreviewColumn:
    name: str
    rule: str
    mandatory: bool
    length: int | None
    sources: list[str]  # the source columns the rule reads


@dataclass
class XrefOutcome:
    field: str
    xref: str
    policy: str
    missing: list[tuple[str, int]]  # source values without an entry, with how often they occur


@dataclass
class Preview:
    structure: str
    total_rows: int
    columns: list[PreviewColumn]
    rows: list[list[PreviewCell]]
    row_sets: list[str]  # row set of each row ("" when the structure has only one)
    xrefs: list[XrefOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def preview_structure(
    project: LoadedProject,
    obj: LoadedObject,
    st: StructureConfig,
    engine: Engine,
    limit: int = 10,
) -> Preview:
    if st.name not in obj.mappings:
        raise TransformError(
            f"structure '{st.name}' is built by custom SQL; only mapping tables can be previewed"
        )
    limit = max(1, min(limit, MAX_ROWS))
    row_sets = st.row_sets()
    plans = [plan_structure(project, obj, st, rs) for rs in row_sets]
    inspector = inspect(engine)
    for plan in plans:
        for table in [plan.gen.source_table, *(j.table for j in plan.gen.joins)]:
            if not inspector.has_table(table):
                raise TransformError(f"'{table}' has not been extracted yet - run Extract first")
        _check_joins(engine, plan.gen, inspector)
    for name in _used_xrefs(obj):
        load_xref(
            engine,
            obj.root / "xref" / f"{name}.csv",
            xref_table(project, obj, name),
            "preview",
            name,
        )
    dialect = engine.dialect.name

    def run(sql: str) -> list[tuple]:  # type: ignore[type-arg]
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(to_dialect(sql, dialect)))]

    base = plans[0]
    columns = [
        PreviewColumn(
            fld.target_field, fld.rule.type, fld.mandatory, fld.length, rule_source_fields(fld.rule)
        )
        for fld, _ in base.fields
    ]
    if not columns:
        raise TransformError(f"structure '{st.name}': every field is NOT_MIGRATED")

    rows: list[list[PreviewCell]] = []
    labels: list[str] = []
    total = 0
    out = Preview(st.name, 0, columns, rows, labels)
    for rs, plan in zip(row_sets, plans, strict=True):
        selects: list[str] = []
        layout: list[int] = []  # how many source values each field has
        for i, (fld, expr) in enumerate(plan.fields):
            refs = rule_source_fields(effective_rule(rs, fld))
            selects.append(f"{expr} AS {q(f't{i}')}")
            for j, ref in enumerate(refs):
                selects.append(f"{plan.refs.col(ref)} AS {q(f'v{i}_{j}')}")
            layout.append(len(refs))
        sql = (
            "SELECT\n  " + ",\n  ".join(selects) + "\n" + plan.from_sql
            + f"\nORDER BY s.{q('_dmc_row_no')} LIMIT {limit}"
        )  # fmt: skip
        for raw in run(sql):
            pos = 0
            cells: list[PreviewCell] = []
            for i, count in enumerate(layout):
                value = raw[pos]
                sources = [None if v is None else str(v) for v in raw[pos + 1 : pos + 1 + count]]
                pos += 1 + count
                col = columns[i]
                shown = None if value is None else str(value)
                flag = ""
                if col.mandatory and (shown is None or not shown.strip()):
                    flag = "empty"
                elif col.length and shown is not None and len(shown) > col.length:
                    flag = "long"
                cells.append(PreviewCell(shown, sources, flag))
            rows.append(cells)
            labels.append(rs.name if len(row_sets) > 1 else "")
        count_rows = run(f"SELECT COUNT(*) FROM {q(plan.gen.source_table)}")[0][0]
        total += int(count_rows)
        if len(row_sets) > 1:
            out.notes.append(f"Row set '{rs.name}': {int(count_rows)} source row(s).")

        for fld, _ in plan.fields:
            rule = effective_rule(rs, fld)
            if not isinstance(rule, XrefRule):
                continue
            source_sql = plan.refs.col(rule.source_field)
            key = f"CAST({source_sql} AS TEXT)"
            missing = run(
                f"SELECT {key} AS value, COUNT(*) AS n\n{plan.from_sql}\n"
                f"LEFT JOIN {q(xref_table(project, obj, rule.xref))} AS xo "
                f"ON {key} = xo.{q('legacy_value')}\n"
                f"WHERE {source_sql} IS NOT NULL AND xo.{q('legacy_value')} IS NULL\n"
                f"GROUP BY {key} ORDER BY 2 DESC LIMIT {MAX_UNMAPPED}"
            )
            policy = {
                "error": "the transform stops",
                "default": f"gets the default '{rule.default}'",
                "blank": "stays empty",
            }[rule.on_unmapped]
            label = fld.target_field if len(row_sets) == 1 else f"{fld.target_field} ({rs.name})"
            out.xrefs.append(
                XrefOutcome(label, rule.xref, policy, [(str(v), int(n)) for v, n in missing])
            )
    out.total_rows = total
    shown_rows = len(rows)
    if total > shown_rows:
        out.notes.append(f"Showing the first {limit} rows of each row set ({total} source rows).")
    return out


__all__ = ["Preview", "preview_structure"]
