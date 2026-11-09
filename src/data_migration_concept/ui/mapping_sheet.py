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


def export_xlsx(
    rows: list[dict[str, Any]], title: str, xrefs: dict[str, list[tuple[str, str]]]
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Mapping"
    ws.append(HEADERS)
    for r in rows:
        ws.append(
            [
                r["target"], r["desc"], r["dtype"], r["length"] or None,
                "X" if r["mandatory"] else "", "X" if r["key"] else "",
                "(not mapped)" if r["rule"] == "UNMAPPED" else r["rule"],
                r["src"], r["a1"], r["a2"], r["a3"], describe_rule(r),
                r.get("note", ""), REVIEW_LABELS.get(r.get("review", ""), "open"),
            ]
        )  # fmt: skip
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    edit_fill = PatternFill("solid", fgColor="FFF4D6")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    last = len(rows) + 1
    edit_columns = ["Rule", "Source", "Setting 1", "Setting 2", "Setting 3", "Note", "Review"]
    for name in edit_columns:  # the columns a reviewer may change are tinted
        col = HEADERS.index(name) + 1
        for row in range(2, last + 1):
            ws.cell(row=row, column=col).fill = edit_fill
    widths = [24, 30, 10, 8, 10, 6, 14, 24, 20, 14, 14, 52, 36, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in range(2, last + 1):
        ws.cell(row=row, column=HEADERS.index("Logic (read only)") + 1).alignment = Alignment(
            wrap_text=True, vertical="top"
        )
    ws.freeze_panes = "B2"
    if rows:
        rules = ",".join(["(not mapped)"] + editor.RULE_TYPES[1:])
        rule_col = get_column_letter(HEADERS.index("Rule") + 1)
        rv = DataValidation(type="list", formula1=f'"{rules}"', allow_blank=True)
        ws.add_data_validation(rv)
        rv.add(f"{rule_col}2:{rule_col}{last}")
        review_col = get_column_letter(HEADERS.index("Review") + 1)
        dv = DataValidation(
            type="list", formula1='"open,approved,changes needed"', allow_blank=True
        )
        ws.add_data_validation(dv)
        dv.add(f"{review_col}2:{review_col}{last}")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(last, 2)}"

    guide = wb.create_sheet("How to read")
    guide.append([f"Mapping specification - {title}"])
    guide["A1"].font = Font(bold=True, size=14)
    for line in INSTRUCTIONS:
        guide.append([line])
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


@dataclass
class SheetDiff:
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)  # targets not in the structure
    unchanged: int = 0
    mapping: dict[str, Any] = field(default_factory=dict)


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


def diff_sheet(filename: str, content: bytes, mapping: dict[str, Any]) -> SheetDiff:
    """Compare an uploaded spec with the mapping. Returns the changes it would make and the
    mapping with them applied; the project is not touched."""
    table = editor.read_template(
        filename, content, "Mapping", is_header=lambda row: "target" in _columns(row)
    )
    headers, rows = table["headers"], table["rows"]
    cols = _columns(headers) if table["has_header"] else {}
    if "target" not in cols:
        raise EditError(
            "Could not find the column 'Target field' in the file. Use the sheet exported "
            "from this screen, or a table with the columns Target field, Rule and Source."
        )
    if not {"rule", "source", "note", "review"} & cols.keys():
        raise EditError(
            "The file has none of the columns Rule, Source, Note or Review - nothing to import."
        )

    current = {f["target_field"]: f for f in mapping.get("fields", [])}
    by_row = {r["target"]: r for r in editor.mapping_rows(mapping)}
    out = SheetDiff(mapping={"fields": [dict(f) for f in mapping.get("fields", [])]})
    fields = {f["target_field"]: f for f in out.mapping["fields"]}
    seen: set[str] = set()

    def cell(row: list[str], key: str) -> str:
        i = cols.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    for n, row in enumerate(rows, start=2):
        target = cell(row, "target")
        if not target:
            continue
        if target not in current:
            out.unknown.append(target)
            continue
        if target in seen:
            out.problems.append(f"Row {n}: '{target}' appears twice - the first one is used.")
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
                out.problems.append(f"Row {n} ({target}): {exc}")
            else:
                if new_rule != fields[target].get("rule"):
                    new_row = editor.mapping_rows(
                        {"fields": [{**fields[target], "rule": new_rule}]}
                    )[0]
                    out.changes.append(
                        Change(target, "rule", describe_rule(old), describe_rule(new_row))
                    )
                    fields[target]["rule"] = new_rule
        if "note" in cols and cell(row, "note") != str(old.get("note", "")):
            new_note = cell(row, "note")
            out.changes.append(Change(target, "note", str(old.get("note", "")), new_note))
            _set_optional(fields[target], "note", new_note)
        if "review" in cols:
            new_review = _review(cell(row, "review"))
            if new_review != old.get("review", ""):
                out.changes.append(
                    Change(
                        target, "review",
                        REVIEW_LABELS[old.get("review", "")], REVIEW_LABELS[new_review],
                    )
                )  # fmt: skip
                _set_optional(fields[target], "review", new_review)
        if len(out.changes) == before:
            out.unchanged += 1
    return out


def _set_optional(field_: dict[str, Any], key: str, value: str) -> None:
    if value:
        field_[key] = value
    else:
        field_.pop(key, None)
