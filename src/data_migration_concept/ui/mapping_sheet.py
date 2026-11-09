"""The mapping as a spreadsheet: export it for business review, read it back.

One row per target field. The columns Rule, Source and Setting 1-3 hold exactly what the mapping
editor shows, so a reviewer can change a rule in Excel and the import turns it back into a
mapping. Nothing is applied by reading a file: the import only produces a list of changes
for the user to confirm."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from data_migration_concept.ui import editor
from data_migration_concept.ui.editor import EditError

HEADERS = [
    "Target field", "Description", "Type", "Length", "Mandatory", "Key",
    "Rule", "Source", "Setting 1", "Setting 2", "Setting 3",
    "Logic (read only)", "Note", "Review",
]  # fmt: skip
REVIEW_VALUES = ["", "approved", "changes"]
REVIEW_LABELS = {"": "open", "approved": "approved", "changes": "changes needed"}

SYNONYMS = {
    "target": ["target field", "target", "field", "field name", "technical name", "feld"],
    "description": ["description", "field description", "label"],
    "rule": ["rule", "rule type", "mapping rule", "type of rule"],
    "source": ["source", "source field", "source column", "source fields", "source columns"],
    "a1": ["setting 1", "setting1", "argument 1", "parameter 1"],
    "a2": ["setting 2", "setting2", "argument 2", "parameter 2"],
    "a3": ["setting 3", "setting3", "argument 3", "parameter 3"],
    "note": ["note", "notes", "comment", "comments", "remark", "remarks"],
    "review": ["review", "review status", "status", "approval", "sign off", "signoff"],
}  # fmt: skip

INSTRUCTIONS = [
    "How to read and change this sheet",
    "",
    "One row per target field. Change the columns Rule, Source, Setting 1-3, Note and Review,",
    "then send the file back; the import shows every difference before anything is changed.",
    "Columns Target field, Description, Type, Length, Mandatory and Key come from the target",
    "structure and are not changed by the import. 'Logic (read only)' is for reading only.",
    "",
    "Rule             Source                      Setting 1            Setting 2         Setting 3",
    "(not mapped)     -                           -                    -                 -",
    "MOVE             source column               -                    -                 -",
    "CONSTANT         -                           the fixed value      -                 -",
    "XREF             source column               name of the list     error/default/blank  value",
    "CONCAT           columns, comma separated    separator            -                 -",
    "SUBSTRING        source column               start (1 = first)    length            -",
    "NOT_MIGRATED     -                           -                    -                 -",
    "SQL              -                           the SQL expression   -                 -",
    "",
    "Columns of a joined source are written source.COLUMN, e.g. addresses.STRAS.",
    "Review: leave empty (open), or write 'approved' or 'changes'.",
]


def describe_rule(row: dict[str, Any]) -> str:
    """The rule in plain words, for readers who do not know the rule names."""
    t, src, a1, a2, a3 = row["rule"], row["src"], row["a1"], row["a2"], row["a3"]
    if t == "UNMAPPED":
        return "Not mapped yet - stays empty."
    if t == "MOVE":
        return f"Copy {src}."
    if t == "CONSTANT":
        return f"Always '{a1}'."
    if t == "XREF":
        policy = {
            "error": "the load stops",
            "default": f"use '{a3}'",
            "blank": "leave it empty",
        }.get(a2, a2)
        return f"Translate {src} through the list '{a1}'; if there is no entry, {policy}."
    if t == "CONCAT":
        return f"Join {src} with '{a1}' between them."
    if t == "SUBSTRING":
        part = (
            f"{a2} character(s) from position {a1}" if a2 and a2 != "0" else f"from position {a1}"
        )
        return f"Take {part} of {src}."
    if t == "NOT_MIGRATED":
        return "Not part of the target - left out."
    if t == "SQL":
        return f"Custom SQL: {a1}"
    return t


ROW_SET_HEADERS = [h for h in HEADERS if h not in ("Note", "Review")] + ["Own rule"]
ROW_SET_PREFIX = "Rows "


def row_set_sheet_name(name: str) -> str:
    return f"{ROW_SET_PREFIX}{name}"[:31]


def row_set_rows(
    mapping: dict[str, Any], overrides: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every field with the rule it uses in a row set: its override, else the main rule."""
    grid = editor.mapping_rows(
        {
            "fields": [
                {**f, "rule": overrides.get(f["target_field"], f.get("rule", {}))}
                for f in mapping.get("fields", [])
            ]
        }
    )
    for r in grid:
        r["own"] = r["target"] in overrides
    return grid


def _write_sheet(ws: Any, rows: list[dict[str, Any]], headers: list[str]) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    ws.append(headers)
    for r in rows:
        values = {
            "Target field": r["target"], "Description": r["desc"], "Type": r["dtype"],
            "Length": r["length"] or None, "Mandatory": "X" if r["mandatory"] else "",
            "Key": "X" if r["key"] else "",
            "Rule": "(not mapped)" if r["rule"] == "UNMAPPED" else r["rule"],
            "Source": r["src"], "Setting 1": r["a1"], "Setting 2": r["a2"],
            "Setting 3": r["a3"], "Logic (read only)": describe_rule(r),
            "Note": r.get("note", ""), "Review": REVIEW_LABELS.get(r.get("review", ""), "open"),
            "Own rule": "own rule" if r.get("own") else "as main",
        }  # fmt: skip
        ws.append([values[h] for h in headers])
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    edit_fill = PatternFill("solid", fgColor="FFF4D6")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    last = len(rows) + 1
    edit_columns = ["Rule", "Source", "Setting 1", "Setting 2", "Setting 3", "Note", "Review"]
    for name in edit_columns:  # the columns a reviewer may change are tinted
        if name not in headers:
            continue
        col = headers.index(name) + 1
        for row in range(2, last + 1):
            ws.cell(row=row, column=col).fill = edit_fill
    widths = {
        "Target field": 24, "Description": 30, "Type": 10, "Length": 8, "Mandatory": 10,
        "Key": 6, "Rule": 14, "Source": 24, "Setting 1": 20, "Setting 2": 14, "Setting 3": 14,
        "Logic (read only)": 52, "Note": 36, "Review": 16, "Own rule": 12,
    }  # fmt: skip
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths[h]
    logic = headers.index("Logic (read only)") + 1
    for row in range(2, last + 1):
        ws.cell(row=row, column=logic).alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "B2"
    if rows:
        rules = ",".join(["(not mapped)"] + editor.RULE_TYPES[1:])
        rule_col = get_column_letter(headers.index("Rule") + 1)
        rv = DataValidation(type="list", formula1=f'"{rules}"', allow_blank=True)
        ws.add_data_validation(rv)
        rv.add(f"{rule_col}2:{rule_col}{last}")
        if "Review" in headers:
            review_col = get_column_letter(headers.index("Review") + 1)
            dv = DataValidation(
                type="list", formula1='"open,approved,changes needed"', allow_blank=True
            )
            ws.add_data_validation(dv)
            dv.add(f"{review_col}2:{review_col}{last}")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(last, 2)}"


def export_xlsx(
    rows: list[dict[str, Any]],
    title: str,
    xrefs: dict[str, list[tuple[str, str]]],
    row_sets: list[dict[str, Any]] | None = None,
) -> bytes:
    """`row_sets`: one dict per UNION ALL row set with name, reads (text) and rows (see
    row_set_rows). Each gets its own sheet 'Rows <name>' next to the main 'Mapping' sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Mapping"
    _write_sheet(ws, rows, HEADERS)
    for rs in row_sets or []:
        _write_sheet(wb.create_sheet(row_set_sheet_name(rs["name"])), rs["rows"], ROW_SET_HEADERS)

    guide = wb.create_sheet("How to read")
    guide.append([f"Mapping specification - {title}"])
    guide["A1"].font = Font(bold=True, size=14)
    for line in INSTRUCTIONS:
        guide.append([line])
    if row_sets:
        guide.append([""])
        guide.append(["Row sets (UNION ALL)"])
        guide.cell(row=guide.max_row, column=1).font = Font(bold=True)
        guide.append(
            [
                "The sheet 'Mapping' holds the main rows. Each row set adds more rows to the same "
                "target table and has its own sheet; every field there shows the rule it uses "
                "in that row set ('as main' = same as the Mapping sheet). Change a rule on a row "
                "set's sheet to give that row set its own."
            ]
        )
        for rs in row_sets:
            guide.append([f"  {row_set_sheet_name(rs['name'])}: {rs['reads']}"])
    guide.column_dimensions["A"].width = 110
    for name, pairs in xrefs.items():
        sheet = wb.create_sheet(f"List {name}"[:31])
        sheet.append(["From (legacy value)", "To (target value)"])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for a, b in pairs:
            sheet.append([a, b])
        sheet.column_dimensions["A"].width = 26
        sheet.column_dimensions["B"].width = 26
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- reading it back --------------------------------------------------------------------


def _norm(text: str) -> str:
    return editor._norm_header(text)


def _columns(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for key, words in SYNONYMS.items():
        for i, h in enumerate(headers):
            if _norm(h) in words and i not in found.values():
                found[key] = i
                break
    return found


@dataclass
class Change:
    target: str
    what: str  # "rule", "note" or "review"
    old: str
    new: str
    sheet: str = "Mapping"


@dataclass
class SheetDiff:
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)  # targets not in the structure
    unchanged: int = 0
    mapping: dict[str, Any] = field(default_factory=dict)
    # row set name -> its overrides after the import (only for row sets that have a sheet)
    row_set_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


def _rule_type(raw: str, source: str) -> str:
    word = raw.strip().upper().replace(" ", "_")
    if word in ("", "(NOT_MAPPED)", "NOT_MAPPED", "UNMAPPED", "-"):
        return "MOVE" if (not raw.strip() and source.strip()) else "UNMAPPED"
    if word == "NOT_MIGRATE" or word == "NOTMIGRATED":
        return "NOT_MIGRATED"
    return word


def _review(raw: str) -> str:
    word = raw.strip().lower()
    if word in ("approved", "ok", "yes", "agreed", "signed off", "sign off"):
        return "approved"
    if word.startswith("change") or word in ("rejected", "no", "not ok", "nok"):
        return "changes"
    return ""


def _read(filename: str, content: bytes, sheet: str) -> tuple[dict[str, Any], dict[str, int]]:
    table = editor.read_template(
        filename, content, sheet, is_header=lambda row: "target" in _columns(row)
    )
    cols = _columns(table["headers"]) if table["has_header"] else {}
    if "target" not in cols:
        raise EditError(
            f"Could not find the column 'Target field' on the sheet '{table['sheet'] or sheet}'. "
            "Use the sheet exported from this screen, or a table with the columns Target field, "
            "Rule and Source."
        )
    return table, cols


def _diff_table(
    table: dict[str, Any],
    cols: dict[str, int],
    fields: dict[str, dict[str, Any]],
    sheet: str,
    out: SheetDiff,
) -> None:
    """Compare one sheet with `fields` (target -> field dict whose rule is the one in force for
    this sheet). Changes are recorded in `out` and applied to `fields`."""
    by_row = {r["target"]: r for r in editor.mapping_rows({"fields": list(fields.values())})}
    seen: set[str] = set()

    def cell(row: list[str], key: str) -> str:
        i = cols.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    for n, row in enumerate(table["rows"], start=2):
        target = cell(row, "target")
        if not target:
            continue
        if target not in fields:
            if target not in out.unknown:
                out.unknown.append(target)
            continue
        if target in seen:
            out.problems.append(
                f"{sheet}, row {n}: '{target}' appears twice - the first one is used."
            )
            continue
        seen.add(target)
        old = by_row[target]
        before = len(out.changes)
        if "rule" in cols or "source" in cols:
            src = cell(row, "source")
            kind = _rule_type(cell(row, "rule"), src) if "rule" in cols else old["rule"]
            a1, a2, a3 = (cell(row, k) for k in ("a1", "a2", "a3"))
            if "rule" not in cols:  # only the source column was given: keep the rule type
                a1, a2, a3 = old["a1"], old["a2"], old["a3"]
            try:
                if kind not in editor.RULE_TYPES:
                    raise ValueError(f"unknown rule '{cell(row, 'rule')}'")
                new_rule = editor.rule_from_form(kind, src, a1, a2, a3)
                if kind in ("MOVE", "XREF", "SUBSTRING") and not src:
                    raise ValueError("a source column is required")
                if kind == "CONCAT" and not new_rule["source_fields"]:
                    raise ValueError("source columns are required")
                if kind == "XREF" and not a1:
                    raise ValueError("the name of the list is required (Setting 1)")
            except (ValueError, EditError) as exc:
                out.problems.append(f"{sheet}, row {n} ({target}): {exc}")
            else:
                if new_rule != fields[target].get("rule"):
                    new_row = editor.mapping_rows(
                        {"fields": [{**fields[target], "rule": new_rule}]}
                    )[0]
                    out.changes.append(
                        Change(target, "rule", describe_rule(old), describe_rule(new_row), sheet)
                    )
                    fields[target]["rule"] = new_rule
        if "note" in cols and cell(row, "note") != str(old.get("note", "")):
            new_note = cell(row, "note")
            out.changes.append(Change(target, "note", str(old.get("note", "")), new_note, sheet))
            _set_optional(fields[target], "note", new_note)
        if "review" in cols:
            new_review = _review(cell(row, "review"))
            if new_review != old.get("review", ""):
                out.changes.append(
                    Change(
                        target, "review",
                        REVIEW_LABELS[old.get("review", "")], REVIEW_LABELS[new_review], sheet,
                    )
                )  # fmt: skip
                _set_optional(fields[target], "review", new_review)
        if len(out.changes) == before:
            out.unchanged += 1


def diff_sheet(
    filename: str,
    content: bytes,
    mapping: dict[str, Any],
    row_sets: dict[str, dict[str, Any]] | None = None,
) -> SheetDiff:
    """Compare an uploaded spec with the mapping and, for an Excel file with sheets named
    'Rows <name>', with the row sets (`row_sets`: name -> current overrides). Returns the
    changes and the mapping / overrides with them applied; the project is not touched."""
    table, cols = _read(filename, content, "Mapping")
    if not {"rule", "source", "note", "review"} & cols.keys():
        raise EditError(
            "The file has none of the columns Rule, Source, Note or Review - nothing to import."
        )
    out = SheetDiff(mapping={"fields": [dict(f) for f in mapping.get("fields", [])]})
    main_fields = {f["target_field"]: f for f in out.mapping["fields"]}
    _diff_table(table, cols, main_fields, "Mapping", out)

    original_main = {f["target_field"]: f.get("rule", {}) for f in mapping.get("fields", [])}
    for name, current in (row_sets or {}).items():
        sheet = row_set_sheet_name(name)
        if sheet not in table["sheets"]:
            continue
        rs_table, rs_cols = _read(filename, content, sheet)
        # what the exported sheet showed: the row set's override, else the main rule as it was
        fields = {
            t: {
                "target_field": t,
                "rule": current.get(t, original_main[t]),
                **{k: v for k, v in main_fields[t].items() if k in ("data_type", "length")},
            }
            for t in main_fields
        }
        before = len(out.changes)
        _diff_table(rs_table, rs_cols, fields, sheet, out)
        edited = {c.target for c in out.changes[before:] if c.what == "rule"}
        # a field the sheet did not change keeps its override, or follows the new main rule
        final = [
            (t, f["rule"] if t in edited or t in current else main_fields[t]["rule"])
            for t, f in fields.items()
        ]
        out.row_set_overrides[name] = editor.diff_overrides(
            {t: f.get("rule", {}) for t, f in main_fields.items()}, final
        )
    return out


def _set_optional(field_: dict[str, Any], key: str, value: str) -> None:
    if value:
        field_[key] = value
    else:
        field_.pop(key, None)
