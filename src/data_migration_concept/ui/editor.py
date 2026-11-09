"""Guided editing of project files. Every change is validated by loading the whole project;
if it introduces a new problem the files are put back and the problems are returned."""

from __future__ import annotations

import csv
import io
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.config.models import (
    CheckConfig,
    FieldMapping,
    FileSource,
    Identifier,
    MappingFile,
    ReconConfig,
)
from data_migration_concept.extract.errors import ExtractError
from data_migration_concept.extract.readers import read_file

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
EDITABLE = re.compile(
    r"^(project\.yaml"
    r"|objects/[A-Za-z][A-Za-z0-9_]*/(object|checks|recon)\.yaml"
    r"|objects/[A-Za-z][A-Za-z0-9_]*/mappings/[A-Za-z0-9_.-]+\.yaml"
    r"|objects/[A-Za-z][A-Za-z0-9_]*/sql/[A-Za-z0-9_.-]+\.sql"
    r"|objects/[A-Za-z][A-Za-z0-9_]*/xref/[A-Za-z0-9_.-]+\.csv)$"
)
FORMATS = {".csv": "csv", ".txt": "csv", ".tsv": "csv", ".xlsx": "xlsx", ".parquet": "parquet"}
MAX_UPLOAD = 200 * 1024 * 1024


class EditError(Exception):
    """A change that cannot be made; the message is safe to show."""

    def __init__(self, errors: list[str] | str) -> None:
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


# --- safe, validated writes -------------------------------------------------------------


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None


def _errors(project_dir: Path) -> list[str]:
    try:
        load_project(project_dir)
    except ConfigError as exc:
        return exc.errors
    return []


def apply_changes(project_dir: Path, changes: dict[Path, bytes | None]) -> None:
    """Write (or delete, for None) the files, then validate the project. If the change adds a
    problem that was not there before, restore everything and raise EditError."""
    baseline = set(_errors(project_dir))
    backups = {p: (p.read_bytes() if p.is_file() else None) for p in changes}
    new_dirs: list[Path] = []
    for path, content in changes.items():
        if content is None:
            path.unlink(missing_ok=True)
            continue
        missing = [d for d in reversed(path.parents) if not d.exists()]
        new_dirs.extend(missing)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    added = [e for e in _errors(project_dir) if e not in baseline]
    if not added:
        return
    for path, old in backups.items():
        if old is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(old)
    for d in new_dirs:
        shutil.rmtree(d, ignore_errors=True)
    raise EditError(added)


def _safe_filename(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "upload"
    return stem[:100]


def identifier(text: str, fallback: str = "item") -> str:
    """Turn arbitrary text into a valid name (letters, digits, underscore; starts with a letter)."""
    cleaned = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]+", "_", text)).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"{fallback}_{cleaned}".strip("_")
    return cleaned


# --- sources ----------------------------------------------------------------------------


def sniff_csv(content: bytes) -> tuple[str, str]:
    """Best guess of (delimiter, encoding) for a CSV file."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            head = content[:65536].decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        head, encoding = content[:65536].decode("latin-1"), "latin-1"
    first = head.splitlines()[0] if head else ""
    delimiter = max(",;\t|", key=first.count)
    return delimiter, "utf-8" if encoding.startswith("utf-8") else encoding


def source_from_upload(
    obj_dir: Path, filename: str, content: bytes, name: str | None = None, sheet: str | None = None
) -> tuple[dict[str, Any], Path, bytes]:
    """Build a file-source definition from an uploaded file. Returns (definition, path, bytes)."""
    if len(content) > MAX_UPLOAD:
        raise EditError(f"The file is larger than {MAX_UPLOAD // (1024 * 1024)} MB.")
    suffix = Path(filename).suffix.lower()
    if suffix not in FORMATS:
        raise EditError("Upload a .csv, .txt, .tsv, .xlsx or .parquet file.")
    safe = _safe_filename(filename)
    source: dict[str, Any] = {
        "kind": "file",
        "name": name or identifier(Path(safe).stem.lower(), "src"),
        "path": f"data/{safe}",
        "format": FORMATS[suffix],
    }
    if source["format"] == "csv":
        delimiter, encoding = sniff_csv(content)
        if delimiter != ",":
            source["delimiter"] = delimiter
        if encoding != "utf-8":
            source["encoding"] = encoding
    if source["format"] == "xlsx" and sheet:
        source["sheet"] = sheet
    return source, obj_dir / source["path"], content


def file_columns(obj_dir: Path, source: dict[str, Any]) -> list[str]:
    """Column names of a file source (reads the file)."""
    try:
        model = FileSource.model_validate(source)
        first = next(iter(read_file(model, obj_dir)))
    except (ValidationError, ExtractError, StopIteration) as exc:
        raise EditError(f"Could not read '{source['path']}': {exc}") from exc
    return list(first.columns)


def mapping_for(columns: list[str]) -> dict[str, Any]:
    return {
        "fields": [
            {"target_field": c, "rule": {"type": "MOVE", "source_field": c}} for c in columns
        ]
    }


def add_object(
    project_dir: Path, name: str, filename: str, content: bytes, sheet: str | None = None
) -> None:
    if not NAME.match(name):
        raise EditError("Object names use letters, digits and '_' and start with a letter.")
    obj_dir = project_dir / "objects" / name
    if obj_dir.exists():
        raise EditError(f"An object called '{name}' already exists.")
    source, path, data = source_from_upload(obj_dir, filename, content, sheet=sheet)
    # Make sure the file can be read before writing anything into the project.
    probe = project_dir / ".dmc_probe" / name
    try:
        probe.mkdir(parents=True)
        (probe / "data").mkdir()
        (probe / source["path"]).write_bytes(data)
        file_columns(probe, source)
    finally:
        shutil.rmtree(project_dir / ".dmc_probe", ignore_errors=True)
    # An object starts with its source only. The target structure is defined next, before any
    # mapping is done.
    obj = {"name": name, "sources": [source], "structures": []}
    apply_changes(project_dir, {path: data, obj_dir / "object.yaml": dump_yaml(obj).encode()})


def _object_file(project_dir: Path, obj: str) -> tuple[Path, dict[str, Any]]:
    path = project_dir / "objects" / obj / "object.yaml"
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise EditError(f"objects/{obj}/object.yaml is missing or unreadable.")
    return path, data


def add_file_source(
    project_dir: Path, obj: str, filename: str, content: bytes, sheet: str | None = None
) -> None:
    path, data = _object_file(project_dir, obj)
    obj_dir = path.parent
    source, dest, raw = source_from_upload(obj_dir, filename, content, sheet=sheet)
    if any(s["name"] == source["name"] for s in data["sources"]):
        source["name"] = identifier(f"{source['name']}_{len(data['sources']) + 1}")
    data["sources"].append(source)
    apply_changes(project_dir, {dest: raw, path: dump_yaml(data).encode()})


def add_sql_source(
    project_dir: Path, obj: str, name: str, connection: str, table: str, query: str
) -> None:
    path, data = _object_file(project_dir, obj)
    if bool(table.strip()) == bool(query.strip()):
        raise EditError("Give either a table name or a query, not both and not neither.")
    source: dict[str, Any] = {"kind": "sql", "name": name.strip(), "connection": connection}
    if table.strip():
        source["table"] = table.strip()
    else:
        source["query"] = query.strip()
    data["sources"].append(source)
    apply_changes(project_dir, {path: dump_yaml(data).encode()})


def remove_source(project_dir: Path, obj: str, name: str) -> None:
    path, data = _object_file(project_dir, obj)
    kept = [s for s in data["sources"] if s["name"] != name]
    if len(kept) == len(data["sources"]):
        raise EditError(f"No source called '{name}'.")
    data["sources"] = kept
    apply_changes(project_dir, {path: dump_yaml(data).encode()})


def add_structure(
    project_dir: Path,
    obj: str,
    name: str,
    target_table: str,
    source: str,
    mapping: dict[str, Any],
) -> None:
    """Add a structure with the given mapping (use `template_mapping` or `mapping_for`)."""
    path, data = _object_file(project_dir, obj)
    if not (NAME.match(name) and NAME.match(target_table)):
        raise EditError("Names use letters, digits and '_' and start with a letter.")
    if not mapping.get("fields"):
        raise EditError("The target structure has no fields.")
    data["structures"].append(
        {
            "name": name,
            "target_table": target_table,
            "source": source,
            "mapping": f"mappings/{name}.yaml",
        }
    )
    apply_changes(
        project_dir,
        {
            path: dump_yaml(data).encode(),
            path.parent / "mappings" / f"{name}.yaml": dump_yaml(mapping).encode(),
        },
    )


def add_structures(project_dir: Path, obj: str, items: list[dict[str, Any]]) -> None:
    """Add several structures in one validated step. Each item: name, target_table, source and
    mapping (a dict with `fields`). Either all are added or none."""
    if not items:
        raise EditError("Nothing was selected.")
    path, data = _object_file(project_dir, obj)
    changes: dict[Path, bytes | None] = {}
    for item in items:
        if not (NAME.match(item["name"]) and NAME.match(item["target_table"])):
            raise EditError(
                f"'{item['name']}' / '{item['target_table']}': names use letters, digits and '_' "
                "and start with a letter."
            )
        if not item["mapping"].get("fields"):
            raise EditError(f"Structure '{item['name']}' has no fields.")
        data["structures"].append(
            {
                "name": item["name"],
                "target_table": item["target_table"],
                "source": item["source"],
                "mapping": f"mappings/{item['name']}.yaml",
            }
        )
        changes[path.parent / "mappings" / f"{item['name']}.yaml"] = dump_yaml(
            item["mapping"]
        ).encode()
    changes[path] = dump_yaml(data).encode()
    apply_changes(project_dir, changes)


def set_structure_source(project_dir: Path, obj: str, name: str, source: str) -> None:
    """Change which source feeds a structure."""
    path, data = _object_file(project_dir, obj)
    st = next((s for s in data["structures"] if s["name"] == name), None)
    if st is None:
        raise EditError(f"No structure called '{name}'.")
    st["source"] = source
    apply_changes(project_dir, {path: dump_yaml(data).encode()})


def remove_structure(project_dir: Path, obj: str, name: str) -> None:
    path, data = _object_file(project_dir, obj)
    removed = [s for s in data["structures"] if s["name"] == name]
    if not removed:
        raise EditError(f"No structure called '{name}'.")
    data["structures"] = [s for s in data["structures"] if s["name"] != name]
    data["loads"] = [ld for ld in data.get("loads", []) if ld.get("structure") != name]
    changes: dict[Path, bytes | None] = {path: dump_yaml(data).encode()}
    if removed[0].get("mapping"):
        changes[path.parent / removed[0]["mapping"]] = None
    apply_changes(project_dir, changes)


# --- mapping ----------------------------------------------------------------------------

RULE_TYPES = ["UNMAPPED", "MOVE", "CONSTANT", "XREF", "CONCAT", "SUBSTRING", "NOT_MIGRATED", "SQL"]
DATA_TYPES = ["string", "integer", "decimal", "date", "boolean"]


def rule_from_form(rule_type: str, src: str, a1: str, a2: str, a3: str) -> dict[str, Any]:
    src, a1, a2, a3 = src.strip(), a1.strip(), a2.strip(), a3.strip()
    if rule_type == "MOVE":
        return {"type": "MOVE", "source_field": src}
    if rule_type == "CONSTANT":
        return {"type": "CONSTANT", "value": a1}
    if rule_type == "XREF":
        rule: dict[str, Any] = {
            "type": "XREF", "source_field": src, "xref": a1, "on_unmapped": a2 or "error",
        }  # fmt: skip
        if rule["on_unmapped"] == "default":
            rule["default"] = a3
        return rule
    if rule_type == "CONCAT":
        return {
            "type": "CONCAT",
            "source_fields": [s.strip() for s in src.split(",") if s.strip()],
            "separator": a1,
        }
    if rule_type == "SUBSTRING":
        return {
            "type": "SUBSTRING", "source_field": src,
            "start": int(a1 or 1), "length": int(a2 or 0),
        }  # fmt: skip
    if rule_type == "NOT_MIGRATED":
        return {"type": "NOT_MIGRATED"}
    if rule_type == "UNMAPPED":
        return {"type": "UNMAPPED"}
    if rule_type == "SQL":
        return {"type": "SQL", "expression": a1}
    raise EditError(f"Unknown rule type '{rule_type}'.")


def mapping_from_form(form: Any) -> dict[str, Any]:
    """Build a mapping from the editor form (fields r<i>_target, r<i>_rule, ...)."""
    rows = sorted({int(m.group(1)) for k in form if (m := re.match(r"^r(\d+)_target$", k))})
    fields: list[dict[str, Any]] = []
    problems: list[str] = []
    for i in rows:

        def get(key: str, i: int = i) -> str:
            return str(form.get(f"r{i}_{key}", "") or "")

        if get("delete") or not get("target").strip():
            continue
        try:
            field: dict[str, Any] = {"target_field": get("target").strip()}
            if get("desc").strip():
                field["description"] = get("desc").strip()
            if get("dtype") and get("dtype") != "string":
                field["data_type"] = get("dtype")
            if get("length").strip():
                field["length"] = int(get("length"))
            if get("mandatory"):
                field["mandatory"] = True
            if get("key"):
                field["key"] = True
            field["rule"] = rule_from_form(get("rule"), get("src"), get("a1"), get("a2"), get("a3"))
            fields.append(field)
        except ValueError as exc:
            problems.append(f"Row {i + 1} ({get('target') or 'unnamed'}): {exc}")
    if problems:
        raise EditError(problems)
    mapping = {"fields": fields}
    try:
        MappingFile.model_validate(mapping)
    except ValidationError as exc:
        raise EditError(_pydantic_lines(exc, fields)) from exc
    return mapping


def _pydantic_lines(exc: ValidationError, fields: list[dict[str, Any]]) -> list[str]:
    lines = []
    for err in exc.errors():
        loc = err["loc"]
        label = ""
        if len(loc) >= 2 and loc[0] == "fields" and isinstance(loc[1], int):
            label = f"Field '{fields[loc[1]].get('target_field', loc[1] + 1)}': "
        lines.append(f"{label}{'.'.join(str(p) for p in loc[2:]) or 'value'} - {err['msg']}")
    return lines


def mapping_file(project_dir: Path, obj: str, structure: str) -> Path:
    """Path of a structure's mapping file."""
    path, data = _object_file(project_dir, obj)
    st = next((s for s in data["structures"] if s["name"] == structure), None)
    if st is None or not st.get("mapping"):
        raise EditError(f"Structure '{structure}' has no mapping file.")
    return path.parent / st["mapping"]


def save_mapping(project_dir: Path, obj: str, structure: str, mapping: dict[str, Any]) -> None:
    path, data = _object_file(project_dir, obj)
    st = next((s for s in data["structures"] if s["name"] == structure), None)
    if st is None or not st.get("mapping"):
        raise EditError(f"Structure '{structure}' has no mapping file to edit.")
    apply_changes(project_dir, {path.parent / st["mapping"]: dump_yaml(mapping).encode()})


def mapping_rows(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a mapping into the form rows used by the editor template."""
    rows = []
    for f in mapping.get("fields", []):
        rule = f.get("rule", {})
        t = rule.get("type", "MOVE")
        src = a1 = a2 = a3 = ""
        if t in ("MOVE", "XREF", "SUBSTRING"):
            src = rule.get("source_field", "")
        if t == "CONCAT":
            src, a1 = ", ".join(rule.get("source_fields", [])), rule.get("separator", "")
        if t == "CONSTANT":
            a1 = "" if rule.get("value") is None else str(rule.get("value"))
        if t == "XREF":
            a1, a2, a3 = (
                rule.get("xref", ""),
                rule.get("on_unmapped", "error"),
                rule.get("default", "") or "",
            )
        if t == "SUBSTRING":
            a1, a2 = str(rule.get("start", 1)), str(rule.get("length", ""))
        if t == "SQL":
            a1 = rule.get("expression", "")
        rows.append(
            {
                "target": f.get("target_field", ""), "desc": f.get("description", ""),
                "dtype": f.get("data_type", "string"), "length": f.get("length", ""),
                "mandatory": bool(f.get("mandatory")), "key": bool(f.get("key")), "rule": t,
                "src": src, "a1": a1, "a2": a2, "a3": a3,
            }
        )  # fmt: skip
    return rows


def mapping_summary(mapping: dict[str, Any]) -> dict[str, int]:
    fields = mapping.get("fields", [])
    unmapped = [f for f in fields if f.get("rule", {}).get("type") == "UNMAPPED"]
    return {
        "total": len(fields),
        "unmapped": len(unmapped),
        "mandatory_unmapped": sum(1 for f in unmapped if f.get("mandatory")),
        "mandatory": sum(1 for f in fields if f.get("mandatory")),
    }


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def auto_map_by_name(mapping: dict[str, Any], columns: list[str]) -> int:
    """Turn unmapped fields into MOVE rules where a source column has the same name (ignoring
    case, spaces, '_' and '-'). Ambiguous matches are left alone. Returns how many were mapped."""
    by_name: dict[str, list[str]] = {}
    for c in columns:
        by_name.setdefault(_squash(c), []).append(c)
    mapped = 0
    for f in mapping.get("fields", []):
        if f.get("rule", {}).get("type") != "UNMAPPED":
            continue
        hits = by_name.get(_squash(str(f["target_field"])), [])
        if len(hits) == 1:
            f["rule"] = {"type": "MOVE", "source_field": hits[0]}
            mapped += 1
    return mapped


# --- target structure (templates) -------------------------------------------------------

ROLES = ["field", "description", "type", "length", "mandatory", "key", "structure"]
POSITIONAL_ROLES = ROLES[:5]  # a pasted list without a header: name, description, type, ...
ROLE_LABELS = {
    "field": "Field name (required)",
    "description": "Description",
    "type": "Data type",
    "length": "Length",
    "mandatory": "Mandatory flag",
    "key": "Key flag",
    "structure": "Structure (when one file lists several)",
}
ROLE_SYNONYMS = {
    "field": ["field", "fieldname", "field name", "target field", "technical name",
              "technical field", "column", "column name", "field id", "name", "feld"],
    "description": ["description", "field description", "text", "label", "short text",
                    "short description", "descr", "field text"],
    "type": ["type", "data type", "datatype", "dtype", "field type", "data element type"],
    "length": ["length", "len", "field length", "size", "max length", "maxlength", "leng",
               "output length"],
    "mandatory": ["mandatory", "required", "must", "obligatory", "mandatory field", "req",
                  "required field", "mandatory flag"],
    "key": ["key", "key field", "primary key", "pk", "is key"],
    "structure": ["structure", "structure name", "sheet", "sheet name", "segment", "table name"],
}  # fmt: skip
TRUE_WORDS = {"x", "y", "yes", "true", "1", "m", "mandatory", "required", "r", "j", "ja", "*"}
TYPE_WORDS = {
    "integer": {"int", "int1", "int2", "int4", "int8", "integer", "smallint", "bigint"},
    "decimal": {"dec", "decimal", "curr", "quan", "fltp", "float", "double", "number", "numeric",
                "currency", "quantity", "real"},
    "date": {"dats", "date", "datum", "datetime", "timestamp", "tims", "time"},
    "boolean": {"bool", "boolean", "flag", "bit"},
}  # fmt: skip
MAX_TEMPLATE_ROWS = 20_000


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _looks_like_header(row: list[str]) -> bool:
    """A header names at least two columns (a data row can contain one such word, like 'Text'),
    or is a single cell that names the field column."""
    known = {s for words in ROLE_SYNONYMS.values() for s in words}
    cells = [c for c in row if c.strip()]
    hits = sum(1 for c in cells if _norm_header(c) in known)
    if len(cells) == 1:
        return _norm_header(cells[0]) in ROLE_SYNONYMS["field"]
    return hits >= 2


def guess_roles(headers: list[str]) -> dict[str, int | None]:
    """Which column plays which role, by header name. Exact matches win over partial ones."""
    normalised = [_norm_header(h) for h in headers]
    taken: set[int] = set()
    roles: dict[str, int | None] = dict.fromkeys(ROLES)
    for exact in (True, False):
        for role in ROLES:
            if roles[role] is not None:
                continue
            for i, h in enumerate(normalised):
                if i in taken or not h:
                    continue
                words = ROLE_SYNONYMS[role]
                if (exact and h in words) or (not exact and any(w in h.split() for w in words)):
                    roles[role] = i
                    taken.add(i)
                    break
    return roles


def read_template(filename: str, content: bytes, sheet: str | None = None) -> dict[str, Any]:
    """Read an uploaded or pasted table of target fields. Returns headers, rows, the sheets of
    an Excel file and whether a header row was found."""
    if len(content) > MAX_UPLOAD:
        raise EditError(f"The file is larger than {MAX_UPLOAD // (1024 * 1024)} MB.")
    suffix = Path(filename).suffix.lower()
    sheets: list[str] = []
    chosen: str | None = None
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        from data_migration_concept.extract.readers import _cell_to_text

        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise EditError(f"Could not read the Excel file: {exc}") from exc
        try:
            sheets = list(wb.sheetnames)
            chosen = sheet if sheet in sheets else sheets[0]
            grid = [
                [(_cell_to_text(c) or "") for c in row]
                for row in wb[chosen].iter_rows(values_only=True, max_row=MAX_TEMPLATE_ROWS)
            ]
        finally:
            wb.close()
    elif suffix in (".csv", ".txt", ".tsv", ""):
        delimiter, encoding = sniff_csv(content)
        text = content.decode(encoding, errors="replace").lstrip("\ufeff")
        grid = [list(r) for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    else:
        raise EditError("Upload a .csv, .txt, .tsv or .xlsx file.")
    grid = [[c.strip() for c in row] for row in grid if any(c.strip() for c in row)]
    if not grid:
        raise EditError("The file has no rows.")
    # Excel templates often start with title rows; the header is the first row naming a field.
    # Text and CSV files start with the header row (if any); only Excel has title rows above it.
    scan = 15 if suffix == ".xlsx" else 1
    header_at = next((i for i, row in enumerate(grid[:scan]) if _looks_like_header(row)), None)
    width = max(len(r) for r in grid)
    if header_at is None:
        headers = [f"Column {i + 1}" for i in range(width)]
        rows = grid
    else:
        headers = [grid[header_at][i] if i < len(grid[header_at]) else "" for i in range(width)]
        headers = [h or f"Column {i + 1}" for i, h in enumerate(headers)]
        rows = grid[header_at + 1 :]
    rows = [r + [""] * (width - len(r)) for r in rows]
    roles = (
        guess_roles(headers)
        if header_at is not None
        else {r: (i if i < width else None) for i, r in enumerate(POSITIONAL_ROLES)}
        | {"key": None, "structure": None}
    )  # no header: assume the order name, description, type, length, mandatory
    return {
        "headers": headers, "rows": rows, "sheets": sheets, "sheet": chosen,
        "has_header": header_at is not None, "roles": roles,
    }  # fmt: skip


def normalize_type(raw: str) -> str:
    word = raw.strip().lower()
    for kind, words in TYPE_WORDS.items():
        if word in words:
            return kind
    return "string"


def parse_length(raw: str) -> int | None:
    match = re.match(r"^\s*(\d+)", raw or "")
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def fields_from_template(
    rows: list[list[str]], roles: dict[str, int | None]
) -> list[dict[str, Any]]:
    """Target fields (all unmapped) from table rows and the chosen role columns."""
    name_col = roles.get("field")
    if name_col is None:
        raise EditError("Choose which column holds the field names.")

    def cell(row: list[str], role: str) -> str:
        i = roles.get(role)
        return row[i].strip() if i is not None and i < len(row) else ""

    fields: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for row in rows:
        name = row[name_col].strip() if name_col < len(row) else ""
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            continue
        field: dict[str, Any] = {"target_field": name}
        if cell(row, "description"):
            field["description"] = cell(row, "description")
        dtype = normalize_type(cell(row, "type"))
        if dtype != "string":
            field["data_type"] = dtype
        length = parse_length(cell(row, "length"))
        if length:
            field["length"] = length
        if cell(row, "mandatory").lower() in TRUE_WORDS:
            field["mandatory"] = True
        if cell(row, "key").lower() in TRUE_WORDS:
            field["key"] = True
        field["rule"] = {"type": "UNMAPPED"}
        fields.append(field)
    if not fields:
        raise EditError("No field names were found in that column.")
    dupes = sorted(n for n, c in seen.items() if c > 1)
    if dupes:
        raise EditError(
            f"These field names appear more than once: {', '.join(dupes[:10])}. "
            "Each target field must be listed once."
        )
    return fields


def template_mapping(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {"fields": fields}


def merge_template(
    project_dir: Path, obj: str, structure: str, fields: list[dict[str, Any]]
) -> dict[str, int]:
    """Bring a structure in line with a template: new fields are added (unmapped), the
    description, type, length and mandatory flag of existing ones are updated, and the mapping
    rules of existing fields are kept. Fields not in the template stay, at the end."""
    path, data = _object_file(project_dir, obj)
    st = next((s for s in data["structures"] if s["name"] == structure), None)
    if st is None or not st.get("mapping"):
        raise EditError(f"Structure '{structure}' has no mapping file to update.")
    current = read_yaml(path.parent / st["mapping"]) or {}
    existing = {f["target_field"]: f for f in current.get("fields", [])}
    merged: list[dict[str, Any]] = []
    added = updated = 0
    for new in fields:
        old = existing.pop(new["target_field"], None)
        if old is None:
            merged.append(new)
            added += 1
            continue
        field = dict(old)
        for key in ("description", "data_type", "length", "mandatory", "key"):
            if key in new and new[key] != old.get(key):
                field[key] = new[key]
                updated += 1
        merged.append(field)
    extra = list(existing.values())
    save_mapping(project_dir, obj, structure, {"fields": merged + extra})
    return {"added": added, "updated": updated, "not_in_template": len(extra)}


# --- XREF -------------------------------------------------------------------------------


def parse_xref(text: str) -> list[tuple[str, str]]:
    """Parse pasted/uploaded XREF text (comma, semicolon or tab separated; header optional)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    delimiter = max(",;\t", key=lines[0].count)
    rows = [tuple(c.strip() for c in r) for r in csv.reader(lines, delimiter=delimiter)]
    if rows and [c.lower() for c in rows[0][:2]] == ["legacy_value", "target_value"]:
        rows = rows[1:]
    out: list[tuple[str, str]] = []
    problems = []
    for n, r in enumerate(rows, start=1):
        if len(r) < 2 or not r[0]:
            problems.append(f"Row {n}: needs a legacy value and a target value.")
            continue
        out.append((r[0], r[1]))
    seen: dict[str, int] = {}
    for legacy, _ in out:
        seen[legacy] = seen.get(legacy, 0) + 1
    dupes = sorted(k for k, v in seen.items() if v > 1)
    if dupes:
        problems.append(f"Legacy value mapped more than once: {', '.join(dupes[:10])}")
    if problems:
        raise EditError(problems)
    return out


def xref_text(rows: list[tuple[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["legacy_value", "target_value"])
    writer.writerows(rows)
    return buf.getvalue()


def save_xref(project_dir: Path, obj: str, name: str, text: str) -> None:
    if not NAME.match(name):
        raise EditError("XREF names use letters, digits and '_' and start with a letter.")
    rows = parse_xref(text)
    path = project_dir / "objects" / obj / "xref" / f"{name}.csv"
    apply_changes(project_dir, {path: xref_text(rows).encode()})


def delete_xref(project_dir: Path, obj: str, name: str) -> None:
    path = project_dir / "objects" / obj / "xref" / f"{name}.csv"
    if not path.is_file():
        raise EditError(f"No XREF called '{name}'.")
    apply_changes(project_dir, {path: None})


def read_xref(project_dir: Path, obj: str, name: str) -> list[tuple[str, str]]:
    path = project_dir / "objects" / obj / "xref" / f"{name}.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return [(r[0], r[1] if len(r) > 1 else "") for r in rows[1:] if r]


def list_xrefs(project_dir: Path, obj: str) -> list[str]:
    folder = project_dir / "objects" / obj / "xref"
    return sorted(p.stem for p in folder.glob("*.csv")) if folder.is_dir() else []


# --- checks, reconciliations, loads -----------------------------------------------------

Spec = list[
    tuple[str, str, str]
]  # (key, label, kind) kinds: text text? number number? list list? textarea bool select:a|b

CHECK_SPECS: dict[str, Spec] = {
    "UNIQUE": [("fields", "Fields that together must be unique (comma separated)", "list")],
    "ALLOWED_VALUES": [
        ("field", "Field", "text"),
        ("values", "Allowed values (comma separated)", "list"),
    ],
    "REFERENTIAL": [
        ("field", "Field", "text"),
        ("ref_structure", "Must exist in structure", "text"),
        ("ref_field", "... in field", "text"),
        ("ref_object", "... of object (blank = this object)", "text?"),
    ],
    "NULL_RATE": [("field", "Field", "text"), ("max_pct", "Maximum % empty", "number")],
    "PATTERN": [
        ("field", "Field", "text"),
        ("pattern", "Regular expression (whole value)", "text"),
    ],
    "RANGE": [
        ("field", "Field", "text"),
        ("min", "Minimum (optional)", "number?"),
        ("max", "Maximum (optional)", "number?"),
    ],
    "OUTLIER": [
        ("field", "Field", "text"),
        ("max_zscore", "Maximum z-score (default 4)", "number?"),
    ],
    "SQL_ASSERT": [("sql", "SQL returning the bad rows", "textarea")],
}

RECON_SPECS: dict[str, Spec] = {
    "COUNT": [("tolerance", "Allowed difference in rows (optional)", "number?")],
    "KEYS": [
        ("source_key", "Source key fields (comma separated)", "list"),
        ("target_key", "Target key fields (comma separated)", "list"),
    ],
    "TOTALS": [
        ("source_field", "Source field", "text"),
        ("target_field", "Target field", "text"),
        ("agg", "Calculation", "select:sum|count|count_distinct|min|max"),
        ("group_by_source", "Group by source fields (optional)", "list?"),
        ("group_by_target", "Group by target fields (optional)", "list?"),
        ("tolerance", "Allowed difference (optional)", "number?"),
    ],
    "FIELDS": [
        ("source_key", "Source key fields (comma separated)", "list"),
        ("target_key", "Target key fields (comma separated)", "list"),
        ("fields", "Fields to compare: 'auto' or SOURCE:TARGET pairs, comma separated", "text"),
        ("tolerance", "Numeric tolerance (optional)", "number?"),
        ("ignore_case", "Ignore upper/lower case", "bool"),
    ],
}
RECON_COMMON: Spec = [
    ("target_table", "Compare against this table instead (optional)", "text?"),
    (
        "exclude_source",
        "SQL condition for source rows deliberately not migrated (optional)",
        "text?",
    ),
]


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _convert(kind: str, raw: str) -> Any:
    base = kind.rstrip("?")
    optional = kind.endswith("?")
    raw = raw.strip()
    if base == "bool":
        return bool(raw)
    if not raw:
        if optional:
            return None
        raise EditError("A required value is empty.")
    if base == "number":
        try:
            number = float(raw)
        except ValueError as exc:
            raise EditError(f"'{raw}' is not a number.") from exc
        return int(number) if number.is_integer() else number
    if base == "list":
        return _split(raw)
    return raw


def entry_from_form(
    form: Any,
    kind: str,
    specs: dict[str, Spec],
    common: Spec = (),  # type: ignore[assignment]
) -> dict[str, Any]:
    if kind not in specs:
        raise EditError(f"Unknown type '{kind}'.")
    entry: dict[str, Any] = {
        "name": str(form.get("name", "")).strip(),
        "structure": str(form.get("structure", "")).strip(),
        "type": kind,
    }
    if not NAME.match(entry["name"]):
        raise EditError("Give it a name (letters, digits, '_'; starts with a letter).")
    if form.get("severity") == "warn":
        entry["severity"] = "warn"
    if str(form.get("description", "")).strip():
        entry["description"] = str(form["description"]).strip()
    for key, label, spec_kind in [*specs[kind], *common]:
        try:
            value = _convert(spec_kind.split(":")[0], str(form.get(key, "")))
        except EditError as exc:
            raise EditError(f"{label}: {exc}") from exc
        if key == "fields" and kind == "FIELDS":
            value = (
                "auto"
                if str(value).strip().lower() == "auto"
                else [
                    {"source": s.split(":")[0].strip(), "target": s.split(":")[-1].strip()}
                    for s in _split(str(value))
                ]
            )
        if value is not None and value is not False and value != []:
            entry[key] = value
    return entry


def _list_file(
    project_dir: Path, obj: str, filename: str, key: str
) -> tuple[Path, list[dict[str, Any]]]:
    path = project_dir / "objects" / obj / filename
    data = read_yaml(path) or {}
    return path, list(data.get(key, []))


def _with_list(path: Path, key: str, entries: list[dict[str, Any]]) -> bytes:
    """The file's content with `key` replaced by `entries`; other top-level keys (such as
    `from_mapping`) are kept."""
    data = read_yaml(path) or {}
    data[key] = entries
    return dump_yaml(data).encode()


FROM_MAPPING_KINDS = ["mandatory", "length", "data_type", "key"]


def read_from_mapping(project_dir: Path, obj: str) -> dict[str, str]:
    """Which structure-based rules are confirmed (kind -> 'error' or 'warn')."""
    data = read_yaml(project_dir / "objects" / obj / "checks.yaml") or {}
    return {k: v for k, v in (data.get("from_mapping") or {}).items() if k in FROM_MAPPING_KINDS}


def set_from_mapping(project_dir: Path, obj: str, rules: dict[str, str]) -> None:
    """Confirm, change or switch off kinds of structure-based rules. A severity of 'error' or
    'warn' confirms the kind; anything else ('off', '') removes it."""
    path = project_dir / "objects" / obj / "checks.yaml"
    data = read_yaml(path) or {}
    current = dict(data.get("from_mapping") or {})
    for kind, severity in rules.items():
        if kind not in FROM_MAPPING_KINDS:
            raise EditError(f"Unknown rule kind '{kind}'.")
        if severity in ("error", "warn"):
            current[kind] = severity
        else:
            current.pop(kind, None)
    ordered = {k: current[k] for k in FROM_MAPPING_KINDS if k in current}
    rest = {k: v for k, v in data.items() if k != "from_mapping"}
    rest.setdefault("checks", [])
    new = {**({"from_mapping": ordered} if ordered else {}), **rest}
    apply_changes(project_dir, {path: dump_yaml(new).encode()})


def list_entries(project_dir: Path, obj: str, filename: str, key: str) -> list[dict[str, Any]]:
    return _list_file(project_dir, obj, filename, key)[1]


def add_entry(project_dir: Path, obj: str, filename: str, key: str, entry: dict[str, Any]) -> None:
    path, entries = _list_file(project_dir, obj, filename, key)
    if any(e.get("name") == entry["name"] for e in entries):
        raise EditError(f"There is already an entry called '{entry['name']}'.")
    model: Any = CheckConfig if key == "checks" else ReconConfig
    try:
        from pydantic import TypeAdapter

        TypeAdapter(model).validate_python(entry)
    except ValidationError as exc:
        raise EditError(
            [
                f"{'.'.join(str(p) for p in e['loc'][1:]) or 'value'}: {e['msg']}"
                for e in exc.errors()
            ]
        ) from exc
    entries.append(entry)
    apply_changes(project_dir, {path: _with_list(path, key, entries)})


def delete_entry(project_dir: Path, obj: str, filename: str, key: str, name: str) -> None:
    path, entries = _list_file(project_dir, obj, filename, key)
    kept = [e for e in entries if e.get("name") != name]
    if len(kept) == len(entries):
        raise EditError(f"No entry called '{name}'.")
    apply_changes(project_dir, {path: _with_list(path, key, kept)})


def load_from_form(form: Any) -> dict[str, Any]:
    kind = str(form.get("kind", "file"))
    entry: dict[str, Any] = {"kind": kind, "structure": str(form.get("structure", "")).strip()}
    if kind == "file":
        entry["path"] = str(form.get("path", "")).strip()
        entry["format"] = str(form.get("format", "csv"))
        if str(form.get("delimiter", "")).strip() and entry["format"] == "csv":
            entry["delimiter"] = str(form["delimiter"]).strip()[:1]
        if form.get("include_lineage"):
            entry["include_lineage"] = True
    else:
        entry["connection"] = str(form.get("connection", "")).strip()
        entry["table"] = str(form.get("table", "")).strip()
        entry["mode"] = str(form.get("mode", "replace"))
    return entry


def add_load(project_dir: Path, obj: str, entry: dict[str, Any]) -> None:
    path, data = _object_file(project_dir, obj)
    data.setdefault("loads", []).append(entry)
    apply_changes(project_dir, {path: dump_yaml(data).encode()})


def delete_load(project_dir: Path, obj: str, index: int) -> None:
    path, data = _object_file(project_dir, obj)
    loads = data.get("loads", [])
    if not 0 <= index < len(loads):
        raise EditError("No such load.")
    del loads[index]
    if not loads:
        data.pop("loads", None)
    apply_changes(project_dir, {path: dump_yaml(data).encode()})


# --- raw files --------------------------------------------------------------------------


def editable_files(project_dir: Path) -> list[str]:
    found = [
        p.relative_to(project_dir).as_posix()
        for p in project_dir.rglob("*")
        if p.is_file() and EDITABLE.match(p.relative_to(project_dir).as_posix())
    ]
    return sorted(found)


def _editable_path(project_dir: Path, rel: str) -> Path:
    if not EDITABLE.match(rel):
        raise EditError("That file cannot be edited here.")
    path = (project_dir / rel).resolve()
    if not path.is_relative_to(project_dir.resolve()):
        raise EditError("That file cannot be edited here.")
    return path


def read_raw(project_dir: Path, rel: str) -> str:
    path = _editable_path(project_dir, rel)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def save_raw(project_dir: Path, rel: str, content: str) -> None:
    path = _editable_path(project_dir, rel)
    if rel.endswith((".yaml", ".yml")):
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise EditError(f"Not valid YAML: {exc}") from exc
    apply_changes(project_dir, {path: content.replace("\r\n", "\n").encode()})


__all__ = [
    "CHECK_SPECS", "DATA_TYPES", "RECON_COMMON", "RECON_SPECS", "RULE_TYPES",
    "EditError", "FieldMapping", "Identifier",
]  # fmt: skip
