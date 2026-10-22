"""Transform step: load XREF tables, then build each target structure from staging."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, inspect, text

from data_migration_concept.config.loader import LoadedObject, LoadedProject
from data_migration_concept.config.models import XrefRule
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform.errors import TransformError
from data_migration_concept.transform.generator import (
    MAX_UNMAPPED_SHOWN,
    GeneratedStructure,
    generate_structure,
    q,
    to_dialect,
    xref_table,
)
from data_migration_concept.transform.xref import load_xref


@dataclass
class TransformResult:
    structure: str
    target_table: str
    rows_in: int
    rows_out: int
    build_file: Path | None


def generate_object(project: LoadedProject, obj: LoadedObject) -> list[GeneratedStructure]:
    return [generate_structure(project, obj, st) for st in obj.config.structures]


def _used_xrefs(obj: LoadedObject) -> list[str]:
    names: set[str] = set()
    for mapping in obj.mappings.values():
        names.update(f.rule.xref for f in mapping.fields if isinstance(f.rule, XrefRule))
    return sorted(names)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {q(table)}")).scalar_one())


def write_build_files(
    project: LoadedProject, obj: LoadedObject, generated: list[GeneratedStructure]
) -> dict[str, Path]:
    out_dir = project.root / "build" / obj.config.name
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for g in generated:
        path = out_dir / f"{g.structure}.sql"
        header = f"-- structure: {g.structure} -> {g.target_table} (source: {g.source_table})\n"
        path.write_text(header + g.render(), encoding="utf-8")
        paths[g.structure] = path
    return paths


def transform_object(
    project: LoadedProject,
    object_name: str,
    engine: Engine,
    runlog: RunLog,
    run_id: str,
    *,
    write_build: bool = True,
) -> list[TransformResult]:
    obj = project.objects.get(object_name)
    if obj is None:
        known = ", ".join(sorted(project.objects)) or "none"
        raise TransformError(f"unknown object '{object_name}' (known: {known})")

    generated = generate_object(project, obj)
    build_files = write_build_files(project, obj, generated) if write_build else {}
    dialect = engine.dialect.name

    for name in _used_xrefs(obj):
        with runlog.step(run_id, f"xref:{name}") as rec:
            table = xref_table(project, obj, name)
            rec.rows_out = load_xref(engine, obj.root / "xref" / f"{name}.csv", table, run_id, name)
            rec.message = f"loaded into {table}"

    inspector = inspect(engine)
    results: list[TransformResult] = []
    for g in generated:
        with runlog.step(run_id, f"transform:{g.structure}") as rec:
            if not inspector.has_table(g.source_table):
                raise TransformError(
                    f"structure '{g.structure}': staging table '{g.source_table}' not found; "
                    "run extract first"
                )
            _fail_on_unmapped(engine, g)
            with engine.begin() as conn:
                for stmt in g.statements:
                    conn.execute(text(to_dialect(stmt, dialect)))
            rec.rows_in = _count(engine, g.source_table)
            rec.rows_out = _count(engine, g.target_table)
            rec.message = f"built {g.target_table}"
        results.append(
            TransformResult(
                g.structure, g.target_table, rec.rows_in, rec.rows_out, build_files.get(g.structure)
            )
        )
    return results


def _fail_on_unmapped(engine: Engine, g: GeneratedStructure) -> None:
    problems: list[str] = []
    for check in g.xref_checks:
        with engine.connect() as conn:
            values = [
                str(v)
                for v in conn.execute(text(to_dialect(check.sql, engine.dialect.name))).scalars()
            ]
        if values:
            more = " (first values shown)" if len(values) >= MAX_UNMAPPED_SHOWN else ""
            problems.append(
                f"field '{check.field}': no entry in xref '{check.xref}' for "
                f"{', '.join(repr(v) for v in values)}{more}"
            )
    if problems:
        raise TransformError(
            f"structure '{g.structure}' has unmapped values:\n  - " + "\n  - ".join(problems)
        )
