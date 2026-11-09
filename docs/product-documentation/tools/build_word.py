"""Build one Word document from the markdown chapters of the product documentation.

Usage (python-docx is fetched temporarily, it is not a dependency of the product):

    uv run --with python-docx python docs/product-documentation/tools/build_word.py

Output: docs/product-documentation/Data_Migration_Concept_Documentation.docx

Handles the markdown subset used in the chapters: headings, paragraphs, bullet and numbered lists,
pipe tables, fenced code blocks, block quotes, **bold**, *italic*, `code` and [links](...).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "Data_Migration_Concept_Documentation.docx"
CHAPTERS = [
    "00-index.md",
    "01-overview-and-concepts.md",
    "02-installation-and-running.md",
    "03a-ui-screens-project-to-structures.md",
    "03b-ui-mapping-screens.md",
    "03c-ui-screens-rules-data-admin.md",
    "04-project-files-reference.md",
    "05-engine-reference.md",
    "06-cli-reference.md",
    "07-proposals-reference.md",
    "08-test-guide.md",
    "09-traceability-and-production-readiness.md",
]

BODY_FONT = "Calibri"
CODE_FONT = "Consolas"
NAVY = RGBColor(0x1F, 0x3A, 0x5F)
GREY = "F2F2F2"
HEAD_FILL = "1F3A5F"
PAGE_WIDTH_CM = 21.0
MARGIN_CM = 1.9
USABLE_CM = PAGE_WIDTH_CM - 2 * MARGIN_CM

INLINE = re.compile(
    r"(\*\*.+?\*\*|`[^`]+`|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*])|\[[^\]]+\]\([^)]+\))"
)


# --- low-level helpers ---------------------------------------------------------------------


def set_shading(element, fill: str) -> None:
    props = element.get_or_add_pPr() if hasattr(element, "get_or_add_pPr") else element
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    props.append(shd)


def style_font(style, name: str, size: float, bold: bool = False, color: RGBColor | None = None):
    style.font.name = name
    style.font.size = Pt(size)
    style.font.bold = bold
    if color is not None:
        style.font.color.rgb = color
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), name)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if fonts.get(qn(attr)) is not None:
            del fonts.attrib[qn(attr)]


def add_field(paragraph, instruction: str, placeholder: str = "") -> None:
    def run_with(child):
        run = paragraph.add_run()
        run._r.append(child)
        return run

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    run_with(begin)
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run_with(instr)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run_with(sep)
    paragraph.add_run(placeholder)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run_with(end)


def add_inline(
    paragraph,
    text: str,
    *,
    size: float | None = None,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor | None = None,
) -> None:
    for part in INLINE.split(text):
        if not part:
            continue
        run_bold, run_italic, code, content = bold, italic, False, part
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            run_bold, content = True, part[2:-2]
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            code, content = True, part[1:-1]
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            run_italic, content = True, part[1:-1]
        else:
            link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", part)
            if link:
                content = link.group(1)
        # bold text may itself contain `code`
        if (run_bold or run_italic) and "`" in content and not code:
            add_inline(paragraph, content, size=size, bold=run_bold, italic=run_italic, color=color)
            continue
        run = paragraph.add_run(content.replace("\\|", "|"))
        run.bold = run_bold
        run.italic = run_italic
        if size:
            run.font.size = Pt(size)
        if color is not None:
            run.font.color.rgb = color
        if code:
            run.font.name = CODE_FONT
            rpr = run._r.get_or_add_rPr()
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is None:
                fonts = OxmlElement("w:rFonts")
                rpr.insert(0, fonts)
            for attr in ("w:ascii", "w:hAnsi", "w:cs"):
                fonts.set(qn(attr), CODE_FONT)
            run.font.size = Pt((size or 10.5) - 1)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "EDEDED")
            rpr.append(shd)


# --- document setup ------------------------------------------------------------------------


def new_document() -> Document:
    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(MARGIN_CM)
    section.top_margin, section.bottom_margin = Cm(2.0), Cm(1.9)

    styles = doc.styles
    style_font(styles["Normal"], BODY_FONT, 10.5)
    styles["Normal"].paragraph_format.space_after = Pt(5)
    styles["Normal"].paragraph_format.line_spacing = 1.08
    for name, size, before, after in (
        ("Heading 1", 20, 0, 10),
        ("Heading 2", 15, 16, 6),
        ("Heading 3", 12.5, 12, 4),
        ("Heading 4", 11, 10, 3),
    ):
        style_font(styles[name], BODY_FONT, size, True, NAVY)
        fmt = styles[name].paragraph_format
        fmt.space_before, fmt.space_after, fmt.keep_with_next = Pt(before), Pt(after), True
    styles["Heading 1"].paragraph_format.page_break_before = True
    style_font(styles["List Bullet"], BODY_FONT, 10.5)
    styles["List Bullet"].paragraph_format.space_after = Pt(2)

    # header and footer
    header = section.header.paragraphs[0]
    header.text = ""
    run = header.add_run("Data_Migration_Concept - product documentation")
    run.font.size, run.font.color.rgb = Pt(8.5), RGBColor(0x70, 0x70, 0x70)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("Page ").font.size = Pt(8.5)
    add_field(footer, "PAGE", "1")
    for r in footer.runs:
        r.font.size = Pt(8.5)

    # ask Word to refresh the table of contents when the file is opened
    settings = doc.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    later = [
        qn(f"w:{n}")
        for n in ("hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids")
    ]
    anchor = next((c for c in settings if c.tag in later), None)
    if anchor is not None:
        anchor.addprevious(update)  # the schema fixes the order of the settings elements
    else:
        settings.append(update)
    zoom = settings.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    return doc


def cover_page(doc: Document, chapter_titles: list[str]) -> None:
    for _ in range(5):
        doc.add_paragraph()
    title = doc.add_paragraph()
    run = title.add_run("Data_Migration_Concept")
    run.bold, run.font.size, run.font.color.rgb = True, Pt(34), NAVY
    sub = doc.add_paragraph()
    run = sub.add_run("Complete product documentation")
    run.font.size, run.font.color.rgb = Pt(20), RGBColor(0x40, 0x40, 0x40)
    doc.add_paragraph()
    for label, value in (
        ("Product", "Data_Migration_Concept (dmc) - open-source data migration workbench"),
        ("Version documented", "0.1.0"),
        ("Repository", "github.com/rsinha2103/data_migration_v1 (branch main)"),
        ("Document built", date.today().isoformat()),
        ("Copyright", "capt_conv, Apache-2.0"),
    ):
        p = doc.add_paragraph()
        r1 = p.add_run(f"{label}: ")
        r1.bold = True
        p.add_run(value)
    doc.add_paragraph()
    note = doc.add_paragraph()
    r = note.add_run(
        "Source of truth: the markdown chapters in docs/product-documentation. This Word "
        "document is generated from them (tools/build_word.py); change the markdown and "
        "rebuild rather than editing this file."
    )
    r.italic, r.font.size = True, Pt(9.5)

    brk = doc.add_paragraph()
    brk.add_run().add_break(WD_BREAK.PAGE)
    h = doc.add_paragraph()
    run = h.add_run("Contents")
    run.bold, run.font.size, run.font.color.rgb = True, Pt(20), NAVY
    toc = doc.add_paragraph()
    add_field(
        toc,
        'TOC \\o "1-2" \\h \\z \\u',
        "Right-click here and choose Update Field to show the table of contents "
        "(Word also offers to update it when the file is opened).",
    )
    doc.add_paragraph()
    lead = doc.add_paragraph()
    lead.add_run("Chapters in this document").bold = True
    for title_text in chapter_titles:
        add_inline(doc.add_paragraph(style="List Bullet"), title_text)


# --- markdown blocks -----------------------------------------------------------------------


def split_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", inner)]


def plain_len(text: str) -> int:
    return len(re.sub(r"[`*]|\[([^\]]+)\]\([^)]+\)", r"\1", text))


def add_table(doc: Document, rows: list[list[str]]) -> None:
    columns = max(len(r) for r in rows)
    rows = [r + [""] * (columns - len(r)) for r in rows]
    weights = []
    for c in range(columns):
        longest = max(plain_len(r[c]) for r in rows)
        average = sum(plain_len(r[c]) for r in rows[1:]) / max(1, len(rows) - 1)
        weights.append(min(max(0.6 * longest + 0.4 * average, 8), 70) ** 0.8)
    total = sum(weights)
    widths = [max(1.6, USABLE_CM * w / total) for w in weights]
    scale = USABLE_CM / sum(widths)
    widths = [w * scale for w in widths]

    table = doc.add_table(rows=len(rows), cols=columns)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    tbl_pr = table._tbl.tblPr  # autofit=False above already adds <w:tblLayout w:type="fixed"/>
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "BFBFBF")
        borders.append(el)
    cell_mar = OxmlElement("w:tblCellMar")
    for edge, value in (("top", 40), ("left", 70), ("bottom", 40), ("right", 70)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        cell_mar.append(el)
    # schema order inside tblPr: ... tblBorders, shd, tblLayout, tblCellMar, tblLook
    layout = tbl_pr.find(qn("w:tblLayout"))
    look = tbl_pr.find(qn("w:tblLook"))
    if layout is not None:
        layout.addprevious(borders)
        layout.addnext(cell_mar)
    elif look is not None:
        look.addprevious(borders)
        look.addprevious(cell_mar)
    else:
        tbl_pr.append(borders)
        tbl_pr.append(cell_mar)

    for r_index, row in enumerate(rows):
        tr = table.rows[r_index]
        props = tr._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        props.append(cant)
        if r_index == 0:
            props.append(OxmlElement("w:tblHeader"))
        for c_index, text in enumerate(row):
            cell = tr.cells[c_index]
            cell.width = Cm(widths[c_index])
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            if r_index == 0:
                add_inline(paragraph, text, size=8.5, bold=True, color=RGBColor(255, 255, 255))
                tcpr = cell._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), HEAD_FILL)
                tcpr.append(shd)
            else:
                add_inline(paragraph, text, size=8.5)
                if r_index % 2 == 0:
                    tcpr = cell._tc.get_or_add_tcPr()
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:val"), "clear")
                    shd.set(qn("w:color"), "auto")
                    shd.set(qn("w:fill"), "F7F9FC")
                    tcpr.append(shd)
    grid = table._tbl.tblGrid
    for col, width in zip(grid.findall(qn("w:gridCol")), widths, strict=True):
        col.set(qn("w:w"), str(int(width / 2.54 * 1440)))
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)


def add_code(doc: Document, lines: list[str]) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.2)
    paragraph.paragraph_format.space_after = Pt(6)
    pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "3")
        el.set(qn("w:color"), "D0D0D0")
        borders.append(el)
    shade = OxmlElement("w:shd")
    shade.set(qn("w:val"), "clear")
    shade.set(qn("w:color"), "auto")
    shade.set(qn("w:fill"), GREY)
    # schema order inside pPr: ... pBdr, shd, ... spacing, ind
    pr.insert(0, shade)
    pr.insert(0, borders)
    for index, line in enumerate(lines):
        run = paragraph.add_run(line.rstrip() if line.strip() else " ")
        run.font.name = CODE_FONT
        run.font.size = Pt(8)
        rpr = run._r.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs"):
            fonts.set(qn(attr), CODE_FONT)
        if index < len(lines) - 1:
            run.add_break()


def render_markdown(doc: Document, text: str, *, shift: int = 0) -> None:
    lines = text.splitlines()
    i = 0
    last_list: object = None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            last_list = None
            continue
        if stripped.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            add_code(doc, block)
            last_list = None
            continue
        heading = re.match(r"^(#{1,5})\s+(.*)$", stripped)
        if heading:
            level = max(1, len(heading.group(1)) - shift)
            p = doc.add_heading(level=min(level, 4))
            add_inline(p, heading.group(2).strip())
            for run in p.runs:
                run.font.color.rgb = NAVY
            last_list = None
            i += 1
            continue
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1])
        ):
            rows = [split_row(stripped)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            add_table(doc, rows)
            last_list = None
            continue
        if re.fullmatch(r"-{3,}", stripped):
            i += 1
            last_list = None
            continue
        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            pr = p._p.get_or_add_pPr()
            border = OxmlElement("w:pBdr")
            left = OxmlElement("w:left")
            left.set(qn("w:val"), "single")
            left.set(qn("w:sz"), "18")
            left.set(qn("w:space"), "6")
            left.set(qn("w:color"), "9DB2CF")
            border.append(left)
            pr.insert(0, border)
            add_inline(p, quote.group(1), italic=True)
            i += 1
            while i < len(lines) and lines[i].strip().startswith(">"):
                add_inline(p, " " + lines[i].strip().lstrip(">").strip(), italic=True)
                i += 1
            last_list = None
            continue
        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        numbered = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if bullet:
            nested = len(bullet.group(1)) >= 2
            p = doc.add_paragraph(style="List Bullet")
            if nested:
                p.paragraph_format.left_indent = Cm(1.3)
            add_inline(p, bullet.group(2))
            last_list = p
            i += 1
            continue
        if numbered:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.9)
            p.paragraph_format.first_line_indent = Cm(-0.6)
            p.paragraph_format.space_after = Pt(2)
            add_inline(p, f"{numbered.group(2)}.  {numbered.group(3)}")
            last_list = p
            i += 1
            continue
        if last_list is not None and line.startswith("  "):
            add_inline(last_list, " " + stripped)  # continuation of a list item
            i += 1
            continue
        paragraph_lines = [stripped]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not re.match(r"^(\s*([-*]|\d+\.)\s|#|\||>|```)", lines[i])
        ):
            paragraph_lines.append(lines[i].strip())
            i += 1
        p = doc.add_paragraph()
        add_inline(p, " ".join(paragraph_lines))
        last_list = None


def chapter_title(text: str) -> str:
    first = next(ln for ln in text.splitlines() if ln.startswith("# "))
    return first[2:].strip()


def main() -> None:
    texts = [(name, (HERE / name).read_text(encoding="utf-8")) for name in CHAPTERS]
    doc = new_document()
    titles = [chapter_title(t) for n, t in texts[1:]]
    cover_page(doc, titles)
    for index, (_name, text) in enumerate(texts):
        if index == 0:  # the index becomes the first chapter without its own title line
            body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("# "))
            render_markdown(doc, "# About this documentation\n" + body)
        else:
            render_markdown(doc, text)
    doc.core_properties.title = "Data_Migration_Concept - product documentation"
    doc.core_properties.author = "capt_conv"
    doc.save(OUT)
    print(f"written {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
