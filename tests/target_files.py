"""Small, invented stand-ins for target structure files (no real SAP content): an Excel XML
workbook with a 'Field List' sheet and a zip of one CSV per structure, in the layout of SAP
Migration Cockpit templates."""

import io
import zipfile
from xml.sax.saxutils import escape

FIELD_LIST_HEADER = [
    "",
    "Sheet Name",
    "Group Name",
    "Field Description",
    "Importance",
    "Type",
    "Length",
    "Decimal",
    "SAP Structure",
    "SAP Field",
]


def _row(cells: list[str]) -> str:
    parts = [
        f'<Cell ss:Index="{i + 1}"><Data ss:Type="String">{escape(text)}</Data></Cell>'
        for i, text in enumerate(cells)
        if text != ""
    ]
    return f"<Row>{''.join(parts)}</Row>"


def _sheet(name: str, rows: list[list[str]]) -> str:
    body = "".join(_row(r) for r in rows)
    return f'<Worksheet ss:Name="{escape(name)}"><Table>{body}</Table></Worksheet>'


def cockpit_xml(field_rows: list[list[str]] | None = None, extra_header: str = "") -> bytes:
    rows = field_rows or [
        ["", "General Data (mandatory)"],
        ["", "", "Key", "Customer", "mandatory for sheet", "Text", "80  ", "", "S_GEN", "LIFNR"],
        ["", "", "Name", "Name", "", "Text", "40", "", "S_GEN", "NAME1"],
        ["", "", "Dates", "Founded", "", "Date", "8", "", "S_GEN", "FOUND"],
        ["", "", "Money", "Credit limit", "", "Number", "15", "2", "S_GEN", "CRLIM"],
        ["", "", "Counts", "Employees", "", "Number", "5", "", "S_GEN", "EMPL"],
        ["", "Bank Details"],
        ["", "", "Key", "Customer", "mandatory for sheet", "Text", "10", "", "S_BANK", "LIFNR"],
        ["", "", "Bank", "Bank key", "mandatory for sheet", "Text", "15", "", "S_BANK", "BANKL"],
        ["", "Odd name (mandatory)"],
        ["", "", "", "Reference", "", "Text", "5", "", "S_/ODD/X", "REF"],
    ]
    intro = [["Introduction"], ["Navigate to <LS>File </>-> <LS>Info</>."]]
    field_list = [
        ["Field List for Migration Object"],
        ["Version 1"],
        [],
        FIELD_LIST_HEADER,
        *rows,
    ]
    xml = (
        '<?xml version="1.0"?>'
        f"{extra_header}"
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        + _sheet("Introduction", intro).replace("&lt;LS&gt;", "<LS>").replace("&lt;/&gt;", "</>")
        + _sheet("Field List", field_list)
        + "</Workbook>"
    )
    return xml.encode("utf-8")


def cockpit_zip(members: dict[str, str] | None = None) -> bytes:
    members = members or {
        "Pkg/S_GEN#FreeText_Mandatory.csv": "LIFNR(k/*),NAME1,FOUND,CRLIM,EMPL\n",
        "Pkg/S_BANK#FreeText.csv": "LIFNR(k/*),BANKL(k)\n",
        "Pkg/S_%2fODD%2fX#FreeText.csv": "REF\n",
        "Pkg/README.pdf": "%PDF not a csv",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return out.getvalue()
