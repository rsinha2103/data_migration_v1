import io

import pytest
from openpyxl import Workbook

from data_migration_concept.ui import editor, targets
from data_migration_concept.ui.editor import EditError
from target_files import cockpit_xml, cockpit_zip


def by_name(structures):
    return {s.name: s for s in structures}


def test_cockpit_xml_structures_fields_and_flags():
    parsed = targets.parse_targets("template.xml", cockpit_xml())
    assert parsed.kind == "cockpit"
    structures = by_name(parsed.structures)
    assert list(structures) == ["S_GEN", "S_BANK", "S_ODD_X"]
    gen = structures["S_GEN"]
    assert gen.label == "General Data" and gen.required is True
    assert structures["S_BANK"].required is False and structures["S_ODD_X"].required is True
    fields = {f["target_field"]: f for f in gen.fields}
    assert list(fields) == ["LIFNR", "NAME1", "FOUND", "CRLIM", "EMPL"]
    assert fields["LIFNR"] == {
        "target_field": "LIFNR",
        "description": "Customer",
        "length": 80,
        "mandatory": True,
        "key": True,
        "rule": {"type": "UNMAPPED"},
    }
    assert fields["FOUND"]["data_type"] == "date"
    assert fields["CRLIM"]["data_type"] == "decimal"
    assert fields["EMPL"]["data_type"] == "integer"
    assert "data_type" not in fields["NAME1"] and "mandatory" not in fields["NAME1"]
    assert all(f["rule"] == {"type": "UNMAPPED"} for s in parsed.structures for f in s.fields)


def test_cockpit_xml_refuses_dtd_and_other_xml():
    with pytest.raises(EditError, match="DTD"):
        targets.parse_cockpit_xml(cockpit_xml(extra_header="<!DOCTYPE x [<!ENTITY a 'b'>]>"))
    with pytest.raises(EditError, match="not an Excel XML workbook"):
        targets.parse_cockpit_xml(b"<?xml version='1.0'?><note>hello</note>")
    with pytest.raises(EditError, match="could not be read"):
        targets.parse_cockpit_xml(b"<Workbook><broken")


def test_cockpit_xml_without_field_list_or_fields():
    xml = (
        b'<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        b'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        b'<Worksheet ss:Name="Other"><Table/></Worksheet></Workbook>'
    )
    with pytest.raises(EditError, match="no 'Field List' sheet"):
        targets.parse_cockpit_xml(xml)
    empty = cockpit_xml(field_rows=[["", "Only a section"]])
    with pytest.raises(EditError, match="No fields were found"):
        targets.parse_cockpit_xml(empty)


def test_cockpit_zip_reads_markers_and_decodes_names():
    parsed = targets.parse_targets("templates.zip", cockpit_zip())
    structures = by_name(parsed.structures)
    assert list(structures) == ["S_ODD_X", "S_BANK", "S_GEN"]  # in file name order
    gen = {f["target_field"]: f for f in structures["S_GEN"].fields}
    assert gen["LIFNR"]["mandatory"] is True and gen["LIFNR"]["key"] is True
    assert "mandatory" not in gen["NAME1"] and "key" not in gen["NAME1"]
    bank = {f["target_field"]: f for f in structures["S_BANK"].fields}
    assert bank["BANKL"] == {"target_field": "BANKL", "key": True, "rule": {"type": "UNMAPPED"}}
    assert structures["S_GEN"].required is True and structures["S_BANK"].required is False
    assert structures["S_ODD_X"].label == "S_/ODD/X"  # %2f decoded, then made a valid name


def test_cockpit_zip_errors():
    with pytest.raises(EditError, match="not a valid zip"):
        targets.parse_targets("x.zip", b"not a zip")
    with pytest.raises(EditError, match="no CSV templates"):
        targets.parse_targets("x.zip", cockpit_zip({"a.txt": "hello"}))
    many = {f"f{i}.csv": "A\n" for i in range(targets.MAX_ZIP_MEMBERS + 1)}
    with pytest.raises(EditError, match="too many files"):
        targets.parse_targets("x.zip", cockpit_zip(many))


def test_plain_table_with_header_guesses_roles_and_normalises_types():
    csv_text = (
        "Field name;Field description;Data type;Length;Mandatory\n"
        "PARTNER;Partner;CHAR;10;X\n"
        "AMOUNT;Amount;CURR;15,2;\n"
        "CREATED;Created on;DATS;8;\n"
        "COUNT;Counter;INT4;;yes\n"
    )
    parsed = targets.parse_targets("fields.csv", csv_text.encode())
    assert parsed.kind == "table" and parsed.roles["field"] == 0 and parsed.roles["mandatory"] == 4
    (only,) = parsed.structures
    fields = {f["target_field"]: f for f in only.fields}
    assert fields["PARTNER"] == {
        "target_field": "PARTNER",
        "description": "Partner",
        "length": 10,
        "mandatory": True,
        "rule": {"type": "UNMAPPED"},
    }
    assert fields["AMOUNT"]["data_type"] == "decimal" and fields["AMOUNT"]["length"] == 15
    assert fields["CREATED"]["data_type"] == "date"
    assert fields["COUNT"]["data_type"] == "integer" and fields["COUNT"]["mandatory"] is True


def test_pasted_list_without_header_uses_column_order():
    pasted = "PARTNER\tBusiness partner\tText\t10\tX\nCOUNTRY\tCountry\tText\t2\t\n"
    parsed = targets.parse_targets("pasted.csv", pasted.encode(), default_name="general")
    (only,) = parsed.structures
    assert only.name == "general"
    assert [f["target_field"] for f in only.fields] == ["PARTNER", "COUNTRY"]
    assert only.fields[0]["mandatory"] is True and only.fields[1]["length"] == 2
    just_names = targets.parse_targets("pasted.csv", b"A\nB\nC\n", default_name="x")
    assert [f["target_field"] for f in just_names.structures[0].fields] == ["A", "B", "C"]


def test_table_with_a_structure_column_makes_several_structures():
    csv_text = "Structure,Field,Mandatory\ngeneral,ID,X\ngeneral,NAME,\nbank,ID,X\nbank,IBAN,\n"
    parsed = targets.parse_targets("multi.csv", csv_text.encode())
    structures = by_name(parsed.structures)
    assert list(structures) == ["general", "bank"]
    assert [f["target_field"] for f in structures["bank"].fields] == ["ID", "IBAN"]


def test_duplicate_fields_and_missing_name_column_are_reported():
    with pytest.raises(EditError, match="more than once: A"):
        targets.parse_targets("x.csv", b"Field,Type\nA,Text\nA,Text\n")
    parsed = targets.parse_targets("x.csv", b"Alpha,Beta\n1,2\n", default_name="s")
    assert parsed.table is not None
    # headers that mean nothing: the file is read as a table of columns the user must assign
    assert parsed.structures == [] or parsed.roles["field"] is None or parsed.roles["field"] == 0
    with pytest.raises(EditError, match="holds the field names"):
        targets.structures_from_table(parsed.table, {"field": None}, "s")


def test_excel_table_with_title_rows_and_sheets():
    wb = Workbook()
    ws = wb.active
    ws.title = "Cover"
    ws.append(["nothing here"])
    sheet = wb.create_sheet("Fields")
    sheet.append(["Target structure for suppliers"])
    sheet.append([])
    sheet.append(["Technical name", "Text", "Length", "Required"])
    sheet.append(["PARTNER", "Partner", 10, "X"])
    out = io.BytesIO()
    wb.save(out)
    first = targets.parse_targets("t.xlsx", out.getvalue())
    assert first.sheets == ["Cover", "Fields"]
    chosen = targets.parse_targets("t.xlsx", out.getvalue(), sheet="Fields", default_name="s")
    (only,) = chosen.structures
    assert only.fields[0]["target_field"] == "PARTNER" and only.fields[0]["length"] == 10
    assert only.fields[0]["mandatory"] is True


def test_cockpit_excel_version_is_recognised():
    wb = Workbook()
    ws = wb.active
    ws.title = "Field List"
    ws.append(["Field List"])
    ws.append(
        ["Sheet Name", "Group Name", "Field Description", "Importance", "Type", "Length",
         "Decimal", "SAP Structure", "SAP Field"]
    )  # fmt: skip
    ws.append(["General"])
    ws.append(["", "Key", "Customer", "mandatory for sheet", "Text", 10, "", "S_ONE", "ID"])
    out = io.BytesIO()
    wb.save(out)
    parsed = targets.parse_targets("cockpit.xlsx", out.getvalue())
    assert parsed.kind == "cockpit" and parsed.structures[0].fields[0]["key"] is True


@pytest.mark.parametrize("builder", ["xlsx", "csv"])
def test_blank_template_can_be_read_back(builder):
    content = targets.blank_template_xlsx() if builder == "xlsx" else targets.blank_template_csv()
    parsed = targets.parse_targets(f"template.{builder}", content)
    (only,) = parsed.structures
    assert only.name == "EXAMPLE_general"
    fields = {f["target_field"]: f for f in only.fields}
    assert list(fields) == ["PARTNER", "NAME_ORG1", "FOUNDED", "CREDIT_LIMIT"]
    assert fields["PARTNER"]["mandatory"] is True and fields["PARTNER"]["key"] is True
    assert fields["FOUNDED"]["data_type"] == "date"
    assert fields["CREDIT_LIMIT"]["data_type"] == "decimal"


def test_file_type_and_size_limits():
    with pytest.raises(EditError, match="Upload a .csv"):
        targets.parse_targets("a.exe", b"MZ")
    with pytest.raises(EditError, match="up to"):
        targets.parse_targets("a.csv", b"x" * (targets.MAX_TEMPLATE_BYTES + 1))
    with pytest.raises(EditError, match="no rows"):
        targets.parse_targets("a.csv", b"\n\n")


def test_pending_uploads_are_scoped_and_bounded():
    store = targets.PendingUploads(limit=2)
    first = store.put(targets.Pending("p", "o", "a.csv", b"x", "create"))
    store.put(targets.Pending("p", "o", "b.csv", b"x", "create"))
    store.put(targets.Pending("p", "o", "c.csv", b"x", "create"))
    with pytest.raises(EditError, match="expired"):
        store.get(first, "p", "o")  # evicted
    token = store.put(targets.Pending("p", "o", "d.csv", b"x", "create"))
    with pytest.raises(EditError, match="expired"):
        store.get(token, "other", "o")  # another project cannot use it
    assert store.get(token, "p", "o").filename == "d.csv"
    store.drop(token)
    with pytest.raises(EditError):
        store.get(token, "p", "o")


def test_helpers_for_types_lengths_and_names():
    assert editor.normalize_type("CURR") == "decimal" and editor.normalize_type("INT4") == "integer"
    assert editor.normalize_type("DATS") == "date" and editor.normalize_type("whatever") == "string"
    assert editor.parse_length("15,2") == 15 and editor.parse_length("") is None
    assert editor.parse_length("0") is None
    assert editor.identifier("S_/CEECV/ROFI_BPTY", "S") == "S_CEECV_ROFI_BPTY"
    assert editor.identifier("9lives", "S") == "S_9lives"


def test_auto_map_by_name_ignores_case_separators_and_ambiguity():
    mapping = {
        "fields": [
            {"target_field": "BANK_KEY", "rule": {"type": "UNMAPPED"}},
            {"target_field": "Name", "rule": {"type": "UNMAPPED"}},
            {"target_field": "CITY", "rule": {"type": "UNMAPPED"}},
            {"target_field": "DONE", "rule": {"type": "CONSTANT", "value": "x"}},
        ]
    }
    columns = ["bank key", "NAME", "City", "CITY", "done"]
    assert editor.auto_map_by_name(mapping, columns) == 2  # CITY is ambiguous, DONE is mapped
    rules = [f["rule"] for f in mapping["fields"]]
    assert rules[0] == {"type": "MOVE", "source_field": "bank key"}
    assert rules[1] == {"type": "MOVE", "source_field": "NAME"}
    assert rules[2] == {"type": "UNMAPPED"} and rules[3]["type"] == "CONSTANT"
