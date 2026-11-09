"""Reading target structure definitions: SAP Migration Cockpit templates (Excel XML, zipped
CSV files, or the Excel version) and plain tables (CSV/Excel/pasted text) with a column picker.
Also the blank template users can fill in."""

from __future__ import annotations

import csv
import io
import re
import secrets
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from data_migration_concept.extract.readers import _cell_to_text
from data_migration_concept.ui.editor import (
    ROLE_LABELS,
    ROLES,
    EditError,
    fields_from_template,
    identifier,
    normalize_type,
    parse_length,
    read_template,
)

MAX_TEMPLATE_BYTES = 30 * 1024 * 1024
MAX_ZIP_MEMBERS = 600
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
FIELD_LIST = "Field List"


@dataclass
class TargetStructure:
    name: str  # suggested structure name (a valid identifier)
    label: str  # the name as written in the file (sheet name, SAP structure, ...)
    fields: list[dict[str, Any]]
    required: bool = False  # the file marks the whole structure as mandatory


@dataclass
class ParsedTargets:
    kind: str  # "cockpit" or "table"
    structures: list[TargetStructure]
    table: dict[str, Any] | None = None  # headers/rows/roles when kind == "table"
    roles: dict[str, int | None] = field(default_factory=dict)
    sheets: list[str] = field(default_factory=list)
    sheet: str | None = None
    note: str = ""


# --- SAP Migration Cockpit --------------------------------------------------------------


def _cockpit_type(kind: str, decimals: str) -> str:
    word = kind.strip().lower()
    if word == "date":
        return "date"
    if word == "number":
        return "decimal" if decimals.strip() not in ("", "0") else "integer"
    return "string"


def _cockpit_from_grid(grid: list[list[str]]) -> list[TargetStructure]:
    """Structures from the rows of a 'Field List' sheet (sheet section rows, then one row per
    field with SAP Structure and SAP Field columns)."""
    header_at = next(
        (i for i, row in enumerate(grid) if "SAP Field" in row and "SAP Structure" in row), None
    )
    if header_at is None:
        raise EditError("That looks like a Migration Cockpit file but has no 'SAP Field' column.")
    head = grid[header_at]

    def col(name: str) -> int | None:
        return head.index(name) if name in head else None

    c_group, c_desc, c_imp = col("Group Name"), col("Field Description"), col("Importance")
    c_type, c_len, c_dec = col("Type"), col("Length"), col("Decimal")
    c_struct, c_field = head.index("SAP Structure"), head.index("SAP Field")

    def cell(row: list[str], i: int | None) -> str:
        return row[i].strip() if i is not None and i < len(row) else ""

    structures: dict[str, TargetStructure] = {}
    label, required = "", False
    for row in grid[header_at + 1 :]:
        filled = [c for c in row if c.strip()]
        if len(filled) == 1:  # a section row: the sheet name
            text = filled[0].strip()
            required = text.lower().endswith("(mandatory)")
            label = re.sub(r"\s*\(mandatory\)\s*$", "", text, flags=re.I)
            continue
        sap_field, sap_struct = cell(row, c_field), cell(row, c_struct)
        if not sap_field or not sap_struct:
            continue
        target = structures.get(sap_struct)
        if target is None:
            target = structures[sap_struct] = TargetStructure(
                name=identifier(sap_struct, "S"), label=label or sap_struct, fields=[],
                required=required,
            )  # fmt: skip
        entry: dict[str, Any] = {"target_field": sap_field}
        if cell(row, c_desc):
            entry["description"] = cell(row, c_desc)
        dtype = _cockpit_type(cell(row, c_type), cell(row, c_dec))
        if dtype != "string":
            entry["data_type"] = dtype
        length = parse_length(cell(row, c_len))
        if length:
            entry["length"] = length
        if "mandatory" in cell(row, c_imp).lower():
            entry["mandatory"] = True
        if cell(row, c_group).lower() == "key":
            entry["key"] = True
        entry["rule"] = {"type": "UNMAPPED"}
        if all(f["target_field"] != sap_field for f in target.fields):
            target.fields.append(entry)
    if not structures:
        raise EditError("No fields were found in the 'Field List'.")
    return _unique_names(list(structures.values()))


def _unique_names(items: list[TargetStructure]) -> list[TargetStructure]:
    seen: dict[str, int] = {}
    for item in items:
        base = item.name
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            item.name = f"{base}_{seen[base]}"
    return items


def _spreadsheetml_rows(raw: bytes, sheet_name: str) -> list[list[str]]:
    """Dense rows of one worksheet of an Excel 2003 XML file."""
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise EditError("That XML file contains a DTD, which is not accepted.")
    # SAP writes inline markup (<LS>, </>) inside help texts; it is not valid XML.
    raw = re.sub(rb"<LS>|</>", b"", raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EditError(f"The XML file could not be read: {exc}") from exc
    if not root.tag.endswith("Workbook"):
        raise EditError("That XML file is not an Excel XML workbook.")
    sheet = next(
        (w for w in root.findall("ss:Worksheet", NS) if w.get(SS + "Name") == sheet_name), None
    )
    if sheet is None:
        raise EditError(f"The workbook has no '{sheet_name}' sheet.")
    table = sheet.find("ss:Table", NS)
    rows: list[list[str]] = []
    for row in table.findall("ss:Row", NS) if table is not None else []:
        cells: dict[int, str] = {}
        index = 0
        for c in row.findall("ss:Cell", NS):
            if c.get(SS + "Index"):
                index = int(c.get(SS + "Index", "1")) - 1
            data = c.find("ss:Data", NS)
            cells[index] = "".join(data.itertext()).strip() if data is not None else ""
            index += 1
        rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
    return rows


def parse_cockpit_xml(raw: bytes) -> list[TargetStructure]:
    return _cockpit_from_grid(_spreadsheetml_rows(raw, FIELD_LIST))


def parse_cockpit_xlsx(content: bytes) -> list[TargetStructure] | None:
    """The Excel version of a Cockpit template; None if it has no 'Field List' sheet."""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise EditError(f"Could not read the Excel file: {exc}") from exc
    try:
        if FIELD_LIST not in wb.sheetnames:
            return None
        grid = [
            [(_cell_to_text(c) or "") for c in r]
            for r in wb[FIELD_LIST].iter_rows(values_only=True)
        ]
    finally:
        wb.close()
    return _cockpit_from_grid(grid)


_MARK = re.compile(r"^(?P<name>.+?)\((?P<flags>[k*/]+)\)$")


def parse_cockpit_zip(content: bytes) -> list[TargetStructure]:
    """A zip of one CSV per structure (`S_NAME#FreeText.csv`) whose header row lists the fields,
    with `(k)` marking key fields and `(*)` mandatory fields, e.g. `LIFNR(k/*)`."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise EditError("That is not a valid zip file.") from exc
    with zf:
        members = [
            i for i in zf.infolist() if i.filename.lower().endswith(".csv") and not i.is_dir()
        ]
        if len(zf.infolist()) > MAX_ZIP_MEMBERS:
            raise EditError("The zip file contains too many files.")
        structures: list[TargetStructure] = []
        for info in sorted(members, key=lambda i: i.filename):
            if info.file_size > 5 * 1024 * 1024:
                continue  # a template header is tiny; a data file is not what we want here
            stem = Path(info.filename).name[: -len(".csv")]
            label = unquote(stem.split("#", 1)[0])
            first = zf.read(info).decode("utf-8-sig", errors="replace").splitlines()
            if not first:
                continue
            header = next(
                csv.reader(
                    [first[0]], delimiter=";" if first[0].count(";") > first[0].count(",") else ","
                )
            )
            fields: list[dict[str, Any]] = []
            for raw in header:
                raw = raw.strip()
                if not raw:
                    continue
                match = _MARK.match(raw)
                name = match["name"].strip() if match else raw
                flags = match["flags"] if match else ""
                entry: dict[str, Any] = {"target_field": name}
                if "*" in flags:
                    entry["mandatory"] = True
                if "k" in flags:
                    entry["key"] = True
                entry["rule"] = {"type": "UNMAPPED"}
                if all(f["target_field"] != name for f in fields):
                    fields.append(entry)
            if fields:
                structures.append(
                    TargetStructure(
                        name=identifier(label, "S"), label=label, fields=fields,
                        required="_mandatory" in stem.lower(),
                    )
                )  # fmt: skip
    if not structures:
        raise EditError("The zip file contains no CSV templates with a header row.")
    return _unique_names(structures)


# --- any file ---------------------------------------------------------------------------


def structures_from_table(
    table: dict[str, Any], roles: dict[str, int | None], default_name: str
) -> list[TargetStructure]:
    rows: list[list[str]] = table["rows"]
    group_col = roles.get("structure")
    if group_col is None:
        return [
            TargetStructure(
                name=identifier(default_name or "main", "S"),
                label=default_name or "main",
                fields=fields_from_template(rows, roles),
            )
        ]
    groups: dict[str, list[list[str]]] = {}
    for row in rows:
        key = row[group_col].strip() if group_col < len(row) else ""
        if key:
            groups.setdefault(key, []).append(row)
    if not groups:
        raise EditError("No structure names were found in the chosen column.")
    return _unique_names(
        [
            TargetStructure(name=identifier(k, "S"), label=k, fields=fields_from_template(v, roles))
            for k, v in groups.items()
        ]
    )


def parse_targets(
    filename: str,
    content: bytes,
    *,
    sheet: str | None = None,
    roles: dict[str, int | None] | None = None,
    default_name: str = "",
) -> ParsedTargets:
    """Detect what the file is and read the target structures from it."""
    if len(content) > MAX_TEMPLATE_BYTES:
        raise EditError(f"Template files can be up to {MAX_TEMPLATE_BYTES // (1024 * 1024)} MB.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".zip":
        return ParsedTargets(
            "cockpit", parse_cockpit_zip(content), note="SAP Migration Cockpit CSV templates"
        )
    if suffix == ".xml":
        return ParsedTargets(
            "cockpit", parse_cockpit_xml(content), note="SAP Migration Cockpit Excel XML template"
        )
    if suffix == ".xlsx":
        found = parse_cockpit_xlsx(content)
        if found is not None:
            return ParsedTargets("cockpit", found, note="SAP Migration Cockpit Excel template")
    table = read_template(filename, content, sheet)
    chosen = roles if roles is not None else table["roles"]
    structures = (
        structures_from_table(table, chosen, default_name)
        if chosen.get("field") is not None
        else []
    )
    return ParsedTargets(
        "table", structures, table=table, roles=chosen, sheets=table["sheets"], sheet=table["sheet"]
    )


# --- the blank template -----------------------------------------------------------------

TEMPLATE_HEADERS = ["Structure", "Field", "Description", "Data type", "Length", "Mandatory", "Key"]
TEMPLATE_EXAMPLE = [
    ["EXAMPLE_general", "PARTNER", "Business partner number", "Text", "10", "X", "X"],
    ["EXAMPLE_general", "NAME_ORG1", "Name", "Text", "40", "X", ""],
    ["EXAMPLE_general", "FOUNDED", "Founding date", "Date", "", "", ""],
    ["EXAMPLE_general", "CREDIT_LIMIT", "Credit limit", "Decimal", "15", "", ""],
]
TEMPLATE_HELP = [
    "How to fill in the target structure template",
    "",
    "One row per target field. Delete the EXAMPLE rows and replace them with your own.",
    "Structure: a name for the target layout, e.g. general or bank. "
    "Several structures can share this sheet.",
    "Field: the technical field name exactly as the target system expects it.",
    "Description: a short text for people.",
    "Data type: Text, Number, Decimal, Integer, Date or Boolean. Anything else is treated as Text.",
    "Length: the maximum number of characters (optional).",
    "Mandatory: put X when the field must always be filled.",
    "Key: put X on the fields that together identify a record (they must be unique).",
    "",
    "SAP users can upload the Migration Cockpit template (.xml, or the zip of CSV files) instead.",
]


def blank_template_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Target structure"
    ws.append(TEMPLATE_HEADERS)
    for row in TEMPLATE_EXAMPLE:
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    for letter, width in zip("ABCDEFG", (24, 24, 36, 12, 8, 11, 6), strict=True):
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"
    guide = wb.create_sheet("How to fill in")
    for line in TEMPLATE_HELP:
        guide.append([line])
    guide["A1"].font = Font(bold=True, size=13)
    guide.column_dimensions["A"].width = 110
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def blank_template_csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerows(TEMPLATE_EXAMPLE)
    return buf.getvalue().encode("utf-8")


# --- uploads waiting for the user to confirm --------------------------------------------


@dataclass
class Pending:
    project: str
    obj: str
    filename: str
    content: bytes
    mode: str  # "create" or "merge"
    structure: str = ""  # for merge: the structure being updated


class PendingUploads:
    """Template files kept in memory between the upload and the confirmation page."""

    def __init__(self, limit: int = 12) -> None:
        self._items: dict[str, Pending] = {}
        self._limit = limit

    def put(self, item: Pending) -> str:
        while len(self._items) >= self._limit:
            self._items.pop(next(iter(self._items)))
        token = secrets.token_urlsafe(12)
        self._items[token] = item
        return token

    def get(self, token: str, project: str, obj: str) -> Pending:
        item = self._items.get(token)
        if item is None or item.project != project or item.obj != obj:
            raise EditError("That upload has expired. Please upload the file again.")
        return item

    def drop(self, token: str) -> None:
        self._items.pop(token, None)


__all__ = [
    "ROLES", "ROLE_LABELS", "ParsedTargets", "Pending", "PendingUploads", "TargetStructure",
    "blank_template_csv", "blank_template_xlsx", "normalize_type", "parse_cockpit_xml",
    "parse_cockpit_zip", "parse_targets", "structures_from_table",
]  # fmt: skip
