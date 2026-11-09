"""Connects the proposal engine to a project: builds the facts it needs, remembers dismissed
proposals, and applies the ones the user accepts. Nothing here runs unless the user asks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from data_migration_concept.profile.profile import ColumnProfile, profile_table
from data_migration_concept.proposals import FieldFacts, ObjectFacts, Proposal, StructureFacts
from data_migration_concept.transform.generator import q
from data_migration_concept.ui import editor
from data_migration_concept.ui.editor import EditError
from data_migration_concept.ui.workspace import Ctx

PROFILE_ROWS = 100_000
DISTINCT_LIMIT = 2000
DISMISSED_FILE = "proposals_dismissed.json"


def build_facts(ctx: Ctx, obj_name: str) -> ObjectFacts:
    """A read-only snapshot of the object: structures, source columns, staged-data profiles."""
    obj = ctx.project.objects[obj_name]
    cfg = ctx.project.config
    existing_tables = set(inspect(ctx.engine).get_table_names())

    structures: list[StructureFacts] = []
    for st in obj.config.structures:
        mapping = obj.mappings.get(st.name)
        fields = [
            FieldFacts(
                f.target_field,
                f.rule.model_dump(exclude_none=True),
                f.data_type,
                f.length,
                f.mandatory,
                f.key,
                f.description,
            )  # fmt: skip
            for f in (mapping.fields if mapping else [])
        ]
        structures.append(StructureFacts(st.name, st.source, fields, custom_sql=mapping is None))

    source_columns: dict[str, list[str]] = {}
    profiles: dict[str, dict[str, ColumnProfile]] = {}
    staging: dict[str, str] = {}
    for src in obj.config.sources:
        table = f"{cfg.staging_prefix}{obj_name}_{src.name}"
        if table in existing_tables:
            staging[src.name] = table
            profile = profile_table(ctx.engine, table, PROFILE_ROWS)
            profiles[src.name] = {p.column: p for p in profile}
            source_columns[src.name] = [p.column for p in profile]
        elif src.kind == "file":
            try:
                source_columns[src.name] = editor.file_columns(
                    obj.root, src.model_dump(exclude_none=True)
                )
            except EditError:
                source_columns[src.name] = []

    cache: dict[tuple[str, str], list[tuple[str, int]] | None] = {}

    def value_counts(source: str, column: str) -> list[tuple[str, int]] | None:
        """Each distinct staged value with the number of rows that hold it."""
        key = (source, column)
        if key not in cache:
            table = staging.get(source)
            if table is None or column not in profiles.get(source, {}):
                cache[key] = None
            else:
                sql = (
                    f"SELECT CAST({q(column)} AS TEXT), COUNT(*) FROM {q(table)} "
                    f"WHERE {q(column)} IS NOT NULL GROUP BY 1 ORDER BY 1 "
                    f"LIMIT {DISTINCT_LIMIT + 1}"
                )
                with ctx.engine.connect() as conn:
                    cache[key] = [(str(v), int(n)) for v, n in conn.execute(text(sql))]
        return cache[key]

    def distinct(source: str, column: str) -> list[str] | None:
        counts = value_counts(source, column)
        return None if counts is None else [v for v, _ in counts]

    path = ctx.path
    return ObjectFacts(
        structures=structures,
        source_columns=source_columns,
        profiles=profiles,
        distinct=distinct,
        value_counts=value_counts,
        checks=editor.list_entries(path, obj_name, "checks.yaml", "checks"),
        from_mapping=editor.read_from_mapping(path, obj_name),
        recons=editor.list_entries(path, obj_name, "recon.yaml", "recons"),
        xrefs=set(editor.list_xrefs(path, obj_name)),
        loads=[ld.model_dump(exclude_none=True) for ld in obj.config.loads],
    )


# --- dismissed proposals ----------------------------------------------------------------


def _dismissed_path(project_dir: Path, obj: str) -> Path:
    return project_dir / "objects" / obj / DISMISSED_FILE


def load_dismissed(project_dir: Path, obj: str) -> set[str]:
    path = _dismissed_path(project_dir, obj)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (ValueError, OSError):
        return set()
    return {str(i) for i in data} if isinstance(data, list) else set()


def _save_dismissed(project_dir: Path, obj: str, ids: set[str]) -> None:
    path = _dismissed_path(project_dir, obj)
    if ids:
        path.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def dismiss(project_dir: Path, obj: str, ids: list[str]) -> int:
    current = load_dismissed(project_dir, obj)
    _save_dismissed(project_dir, obj, current | set(ids))
    return len(set(ids) - current)


def restore(project_dir: Path, obj: str, ids: list[str] | None = None) -> int:
    """Bring dismissed proposals back (all of them when `ids` is None)."""
    current = load_dismissed(project_dir, obj)
    back = current if ids is None else current & set(ids)
    _save_dismissed(project_dir, obj, current - back)
    return len(back)


# --- applying accepted proposals --------------------------------------------------------


@dataclass
class ApplyResult:
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, list[str]]] = field(default_factory=list)


def _apply_mappings(project_dir: Path, obj: str, items: list[Proposal], out: ApplyResult) -> None:
    by_structure: dict[str, list[Proposal]] = {}
    for p in items:
        by_structure.setdefault(p.structure, []).append(p)
    for structure, group in by_structure.items():
        try:
            path = editor.mapping_file(project_dir, obj, structure)
            mapping = editor.read_yaml(path) or {}
            fields = {f["target_field"]: f for f in mapping.get("fields", [])}
            changed: list[Proposal] = []
            for p in group:
                f = fields.get(p.payload["field"])
                if f is None or f.get("rule", {}).get("type") != "UNMAPPED":
                    out.skipped.append(f"{p.title} (the field is already mapped or gone)")
                    continue
                f["rule"] = p.payload["rule"]
                changed.append(p)
            if changed:
                editor.save_mapping(project_dir, obj, structure, mapping)
                out.applied.extend(p.title for p in changed)
        except EditError as exc:
            out.failed.extend((p.title, exc.errors) for p in group)


def _apply_xref(project_dir: Path, obj: str, p: Proposal) -> None:
    name: str = p.payload["xref"]
    xref_path = project_dir / "objects" / obj / "xref" / f"{name}.csv"
    if xref_path.exists():
        raise EditError(f"A value list called '{name}' already exists.")
    changes: dict[Path, bytes | None] = {
        xref_path: editor.xref_text([tuple(r) for r in p.payload["rows"]]).encode()
    }
    by_structure: dict[str, list[dict[str, str]]] = {}
    for f in p.payload["fields"]:
        by_structure.setdefault(f["structure"], []).append(f)
    for structure, fields in by_structure.items():
        path = editor.mapping_file(project_dir, obj, structure)
        mapping = editor.read_yaml(path) or {}
        for entry in mapping.get("fields", []):
            for wanted in fields:
                rule = entry.get("rule", {})
                if (
                    entry["target_field"] == wanted["field"]
                    and rule.get("type") == "MOVE"
                    and rule.get("source_field") == wanted["source_field"]
                ):
                    entry["rule"] = {
                        "type": "XREF", "source_field": wanted["source_field"],
                        "xref": name, "on_unmapped": "error",
                    }  # fmt: skip
        changes[path] = editor.dump_yaml(mapping).encode()
    editor.apply_changes(project_dir, changes)


def apply_proposals(project_dir: Path, obj: str, proposals: list[Proposal]) -> ApplyResult:
    """Apply the accepted proposals. Each one is validated against the whole project; one that
    would break something is refused and reported, and the others still go through."""
    out = ApplyResult()
    _apply_mappings(project_dir, obj, [p for p in proposals if p.kind == "mapping"], out)
    for p in proposals:
        try:
            if p.kind == "xref":
                _apply_xref(project_dir, obj, p)
            elif p.kind == "check" and "from_mapping" in p.payload:
                editor.set_from_mapping(project_dir, obj, p.payload["from_mapping"])
            elif p.kind == "check":
                for entry in p.payload["entries"]:
                    editor.add_entry(project_dir, obj, "checks.yaml", "checks", dict(entry))
            elif p.kind == "recon":
                for entry in p.payload["entries"]:
                    editor.add_entry(project_dir, obj, "recon.yaml", "recons", dict(entry))
            elif p.kind == "load":
                editor.add_load(project_dir, obj, dict(p.payload))
            else:
                continue
        except EditError as exc:
            out.failed.append((p.title, exc.errors))
            continue
        out.applied.append(p.title)
    return out


def describe(payload: dict[str, Any]) -> str:
    """A compact one-line rendering of a payload for display."""
    return ", ".join(f"{k}={v}" for k, v in payload.items() if k not in ("name", "description"))
