"""Load and cross-validate a project folder (project.yaml + objects/*)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from data_migration_concept.config.errors import ConfigError
from data_migration_concept.config.models import (
    CheckConfig,
    ChecksFile,
    FieldsRecon,
    FileLoad,
    FileSource,
    KeysRecon,
    MappingFile,
    NotMigratedRule,
    ObjectConfig,
    ProjectConfig,
    ReconConfig,
    ReconFile,
    ReferentialCheck,
    SqlAssertCheck,
    SqlSource,
    TableLoad,
    TotalsRecon,
    UniqueCheck,
    XrefRule,
)

LINEAGE_COLUMNS = {"_dmc_row_no", "_dmc_run_id"}


@dataclass
class LoadedObject:
    config: ObjectConfig
    root: Path
    mappings: dict[str, MappingFile] = field(default_factory=dict)  # keyed by structure name
    checks: list[CheckConfig] = field(default_factory=list)
    recons: list[ReconConfig] = field(default_factory=list)

    def target_fields(self, structure: str) -> set[str] | None:
        """Columns a mapped structure produces, or None for custom-SQL structures."""
        mapping = self.mappings.get(structure)
        if mapping is None:
            return None
        names = {f.target_field for f in mapping.fields if not isinstance(f.rule, NotMigratedRule)}
        return names | LINEAGE_COLUMNS


@dataclass
class LoadedProject:
    config: ProjectConfig
    root: Path
    objects: dict[str, LoadedObject] = field(default_factory=dict)


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _read_yaml(path: Path, base: Path, errors: list[str]) -> Any | None:
    if not path.is_file():
        errors.append(f"{_rel(path, base)}: file not found")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{_rel(path, base)}: invalid YAML: {exc}")
        return None


def _validate[T: BaseModel](
    model: type[T], data: Any, path: Path, base: Path, errors: list[str]
) -> T | None:
    try:
        return model.model_validate(data if data is not None else {})
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"]) or "<root>"
            errors.append(f"{_rel(path, base)}: {loc}: {err['msg']}")
        return None


def _load_object(
    object_dir: Path, project: ProjectConfig, base: Path, errors: list[str]
) -> LoadedObject | None:
    obj_file = object_dir / "object.yaml"
    data = _read_yaml(obj_file, base, errors)
    if data is None:
        return None
    cfg = _validate(ObjectConfig, data, obj_file, base, errors)
    if cfg is None:
        return None

    loaded = LoadedObject(config=cfg, root=object_dir)
    where = _rel(obj_file, base)
    source_names = {s.name for s in cfg.sources}

    for src in cfg.sources:
        if isinstance(src, FileSource) and not (object_dir / src.path).is_file():
            errors.append(f"{where}: source '{src.name}': file '{src.path}' not found")
        if isinstance(src, SqlSource) and src.connection not in project.connections:
            errors.append(f"{where}: source '{src.name}': unknown connection '{src.connection}'")

    for st in cfg.structures:
        if st.source not in source_names:
            errors.append(f"{where}: structure '{st.name}': unknown source '{st.source}'")
        if st.custom_sql is not None and not (object_dir / st.custom_sql).is_file():
            errors.append(f"{where}: structure '{st.name}': SQL file '{st.custom_sql}' not found")
        if st.mapping is None:
            continue
        map_file = object_dir / st.mapping
        mdata = _read_yaml(map_file, base, errors)
        if mdata is None:
            continue
        mapping = _validate(MappingFile, mdata, map_file, base, errors)
        if mapping is None:
            continue
        loaded.mappings[st.name] = mapping
        for fld in mapping.fields:
            if isinstance(fld.rule, XrefRule):
                xref_file = object_dir / "xref" / f"{fld.rule.xref}.csv"
                if not xref_file.is_file():
                    errors.append(
                        f"{_rel(map_file, base)}: field '{fld.target_field}': "
                        f"xref file 'xref/{fld.rule.xref}.csv' not found"
                    )

    structure_names = {s.name for s in cfg.structures}
    project_root = base.resolve()
    for ld in cfg.loads:
        if ld.structure not in structure_names:
            errors.append(f"{where}: load: unknown structure '{ld.structure}'")
        if isinstance(ld, TableLoad) and ld.connection not in project.connections:
            errors.append(
                f"{where}: load of '{ld.structure}': unknown connection '{ld.connection}'"
            )
        if isinstance(ld, FileLoad):
            dest = (project_root / ld.path).resolve()
            if not dest.is_relative_to(project_root):
                errors.append(
                    f"{where}: load of '{ld.structure}': path '{ld.path}' leaves the project"
                )

    checks_file = object_dir / "checks.yaml"
    if checks_file.is_file():
        cdata = _read_yaml(checks_file, base, errors)
        parsed = _validate(ChecksFile, cdata, checks_file, base, errors)
        if parsed is not None:
            loaded.checks = parsed.checks
            _validate_checks(loaded, _rel(checks_file, base), errors)
    recon_file = object_dir / "recon.yaml"
    if recon_file.is_file():
        rdata = _read_yaml(recon_file, base, errors)
        recon = _validate(ReconFile, rdata, recon_file, base, errors)
        if recon is not None:
            loaded.recons = recon.recons
            _validate_recons(loaded, _rel(recon_file, base), errors)
    return loaded


def _check_fields(check: CheckConfig) -> list[str]:
    if isinstance(check, UniqueCheck):
        return check.fields
    if isinstance(check, SqlAssertCheck):
        return []
    return [check.field]


def _validate_checks(obj: LoadedObject, where: str, errors: list[str]) -> None:
    structures = {s.name for s in obj.config.structures}
    for check in obj.checks:
        if check.structure not in structures:
            errors.append(f"{where}: check '{check.name}': unknown structure '{check.structure}'")
            continue
        known = obj.target_fields(check.structure)
        if known is None:  # custom SQL: columns are not known up front
            continue
        for fld in _check_fields(check):
            if fld not in known:
                errors.append(
                    f"{where}: check '{check.name}': structure '{check.structure}' "
                    f"has no target field '{fld}'"
                )


def _validate_recons(obj: LoadedObject, where: str, errors: list[str]) -> None:
    structures = {s.name for s in obj.config.structures}
    for rec in obj.recons:
        if rec.structure not in structures:
            errors.append(f"{where}: recon '{rec.name}': unknown structure '{rec.structure}'")
            continue
        known = obj.target_fields(rec.structure)
        if known is None or rec.target_table is not None:
            continue  # columns unknown (custom SQL) or compared against another table
        wanted: list[str] = []
        if isinstance(rec, KeysRecon | FieldsRecon):
            wanted += rec.target_key
        if isinstance(rec, FieldsRecon) and rec.fields != "auto":
            wanted += [p.target for p in rec.fields]
        if isinstance(rec, TotalsRecon):
            wanted += [rec.target_field, *rec.group_by_target]
        for fld in wanted:
            if fld not in known:
                errors.append(
                    f"{where}: recon '{rec.name}': structure '{rec.structure}' "
                    f"has no target field '{fld}'"
                )


def _validate_references(project: LoadedProject, errors: list[str]) -> None:
    for obj in project.objects.values():
        for check in obj.checks:
            if not isinstance(check, ReferentialCheck):
                continue
            ref_obj = project.objects.get(check.ref_object or obj.config.name)
            where = f"objects/{obj.root.name}/checks.yaml: check '{check.name}'"
            if ref_obj is None:
                errors.append(f"{where}: unknown ref_object '{check.ref_object}'")
                continue
            if check.ref_structure not in {s.name for s in ref_obj.config.structures}:
                errors.append(f"{where}: unknown ref_structure '{check.ref_structure}'")
                continue
            known = ref_obj.target_fields(check.ref_structure)
            if known is not None and check.ref_field not in known:
                errors.append(
                    f"{where}: structure '{check.ref_structure}' has no field '{check.ref_field}'"
                )


def load_project(path: str | Path) -> LoadedProject:
    """Load a project folder. Raises ConfigError listing every problem found."""
    root = Path(path).resolve()
    errors: list[str] = []

    project_file = root / "project.yaml"
    data = _read_yaml(project_file, root, errors)
    project = (
        _validate(ProjectConfig, data, project_file, root, errors) if data is not None else None
    )
    if project is None:
        raise ConfigError(errors)

    result = LoadedProject(config=project, root=root)
    objects_root = root / project.objects_dir
    if not objects_root.is_dir():
        errors.append(f"{project.objects_dir}/: directory not found")
    else:
        for object_dir in sorted(p for p in objects_root.iterdir() if p.is_dir()):
            obj = _load_object(object_dir, project, root, errors)
            if obj is None:
                continue
            if obj.config.name in result.objects:
                errors.append(
                    f"{_rel(object_dir, root)}: duplicate object name '{obj.config.name}'"
                )
            result.objects[obj.config.name] = obj
        _validate_references(result, errors)

    if errors:
        raise ConfigError(errors)
    return result
