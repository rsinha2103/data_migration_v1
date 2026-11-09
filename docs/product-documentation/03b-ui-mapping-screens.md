# 03b - UI screens: mapping (S09), preview (S10), specification import (S11)

## S09 Mapping page - `/p/<project>/o/<object>/structures/<structure>`

**Purpose**: map every target field of one structure; join more sources; add UNION ALL row sets; work in bulk; exchange the mapping with Excel; preview the result.

The page title is `<structure>  (table tgt_<target_table>)`.

**If the structure uses custom SQL** the page only shows: "This structure is built by custom SQL (`<file>`), not a mapping table. Edit the SQL in the file editor." (link to S22). All other controls below exist only for mapping-based structures.

### S09.1 Top cards

| Card | Content and behaviour |
|---|---|
| **Progress** | `N of M target fields mapped` (a field counts as mapped unless its rule is *(not mapped)*); badge **K mandatory field(s) not mapped** (amber) or **all mandatory fields mapped** (green); line `Review: A approved, C need changes, O open`. |
| **Mapping specification** | **Download Excel** (the specification workbook, see S09.9 / S11); file box + **Import back** (accepts .xlsx, .csv, .txt, .tsv). Hint: "Bring a mapping from Excel/CSV too: Target field, Rule, Source columns." |
| **Filled from source** | Select of the object's sources + **Change**; hint `N source column(s) available`. Changing sets the main source of the structure (green "This structure now reads from that source. Re-run Extract and Transform."). Red "Source not changed." only when the project would become invalid. **Whether the mapped columns exist in the new source is checked at Extract**, not when you change it. |
| **Update the target structure** | File box (.xml, .zip, .xlsx, .csv, .txt, .tsv) + **Read**. Opens S08 in *update* mode (fields added, details updated, rules kept). |

Below the cards an explanation of the rule types and: "Mandatory, length, data type and key become checks automatically" - **note**: they become checks only when confirmed (S14 / S05); "Saving rewrites the mapping file without comments."

### S09.2 Joined sources (card "Joined sources")

Purpose: bring columns of other sources into this structure (for example addresses next to the general data). In rules the joined source's columns are written `source.COLUMN`; the structure's own source needs no prefix.

- Existing joins are listed: `<source> (left|inner join, on MAIN = OTHER[, ...]; repeated keys: error | first row only)` with **Remove**.
- **Add form**: *source* (sources not already joined and not the main source), *type* (`keep all main rows (left)` / `only matching rows (inner)`), up to **three key pairs** `column here = column there` (the first column is a column of the main source, the second a column of the joined source), *repeated key policy* (`repeated key: stop` / `repeated key: first row`), **Add join**.
- If the object has no other source: hint "Add a second source to the object (Sources tab) to join it here."

| Action | Result |
|---|---|
| Add join | Green "Source joined. Its columns can now be used as source.COLUMN." |
| Add join, no key pair | Red "Join not added." + "Give at least one pair of key columns." |
| Remove join | Green "Join removed; fields that used it are now not mapped." Fields whose rule referenced `source.` columns are reset to *(not mapped)* in the same step. |
| Remove a join that does not exist | Red "Join not removed." + "'<structure>' does not join '<source>'." |

Behaviour at run time (chapter 05): joins compare `CAST(main AS TEXT) = CAST(other AS TEXT)`; with policy *stop* the transform refuses a join whose key repeats and names up to 5 repeated keys; with *first row* the row with the lowest `_dmc_row_no` per key is used. The column suggestions (type-ahead) of the source-column boxes include `source.COLUMN` entries for joined sources (columns are known once the file is readable, or the database source has been extracted).

### S09.3 Row sets (UNION ALL) (card "Row sets (UNION ALL)")

Purpose: add **more rows to the same target table**. Each row set reads its own main source (and joins) with *this* mapping, and only the fields you change use a different rule. The mapping grid below it is "the rows from `<main source>`".

For **each row set**:
- Heading line `<name> reads <source>[, joined with <source> on A = B]` and **Remove row set**.
- Hint: "Every field starts with the main rule. Change a rule here to give this row set its own; a rule that equals the main one is not stored (N field(s) differ now). Description, type, length, mandatory and key (greyed out) belong to the target table, so they are the same for every row set; change them in the main grid below."
- A grid with columns **Target field | Rule in `<name>` | Rule settings | Description | Type | Max length | Mandatory | Key | Status**. Rule and Rule settings behave exactly like the main grid (same dropdown, same labelled settings). *Status* shows a badge **own rule** or *as main*. The other columns are greyed out.
- **Save `<name>`**: stores only the differences from the main rules (see below).

**Add form**: *name* (e.g. `FLVN01`), *reads* (any source), *joined with* (nothing or one source), up to **two key pairs**, *type* (left/inner), *repeated key policy*, **Add row set**.

| Action | Result |
|---|---|
| Add row set | Green "Row set added: its rows are put below the main rows (UNION ALL). Give the fields that differ their own rule, then re-run Extract/Transform." |
| Add with a join but no key pair | Red "Row set not added." + "Give at least one pair of key columns for the join." |
| Add with name `main`, duplicate name, or a join of its own source | Red "Row set not added." + the validation message (`'main' is the name of the structure's own rows`, `row set names must be unique`, `... is its own source and cannot be joined`) |
| Save a row set grid | Green "Row set '<name>' saved: N field(s) with their own rule, the rest follow the main mapping. Re-run Transform to apply it." |
| Save with an invalid rule | Red "Row set '<name>' not saved." + reason (e.g. `source_fields - List should have at least 2 items`) |
| Override that makes a field `NOT_MIGRATED` in one row set only | Refused: "... field 'X' must be NOT_MIGRATED in every row set or in none" |
| Remove row set | Green "Row set removed." |

**How the saved differences work**: every rule in the grid is compared with the main rule of the same field (as validated rules, so presentation differences do not count). Equal -> no override stored (the field *follows* the main rule, including future changes to it). Different -> stored under `unions[].overrides.<field>` in `object.yaml`.

### S09.4 Filters and sorting (above the grid)

| Control | Effect (immediate, on this page only) |
|---|---|
| **Find a field** | shows only rows whose target name contains the text (case-insensitive) |
| **only not mapped** | rows whose rule is *(not mapped)* |
| **only mandatory** | rows with the Mandatory box ticked |
| **Review** | any / open / approved / changes needed |
| **Sort** | *as in the structure* / *not mapped first* (not mapped, mandatory first, then structure order) / *by name*. **Sorting reorders the rows in the form, so saving writes the fields in the new order.** |

### S09.5 Bulk tools (card "Bulk tools")

Tick fields in the first column (the box in the header ticks all *visible* rows); the card shows `N selected`.

| Button | Effect on the ticked rows |
|---|---|
| **Set this rule** | gives each ticked row the rule chosen in the card (rule type + source column + setting 1-3; empty settings leave the row's own value untouched) |
| **Copy first rule to the rest** | copies rule type and settings of the **first ticked row** (in screen order) to all other ticked rows; needs at least two ticked rows |
| **Set review** (select + button) | sets the Review of the ticked rows to open / approved / changes needed |

"Changes are made on this page only. Press **Save mapping** to keep them." With nothing ticked a browser alert says "Tick the fields to change first."

### S09.6 The mapping grid

One row per target field. Columns:

| Column | Control | Notes |
|---|---|---|
| (tick) | checkbox | selection for bulk tools |
| **Target field** | text | blank name = the row is dropped on save. Badges: *not mapped* (amber if mandatory, grey otherwise), *key* |
| **Rule** | select | (not mapped), MOVE, CONSTANT, XREF, CONCAT, SUBSTRING, NOT_MIGRATED, SQL |
| **Rule settings** | inputs shown according to the rule (below) | |
| **Description** | text | |
| **Type** | select | string, integer, decimal, date, boolean |
| **Max length** | number | empty = no limit |
| **Mandatory** | checkbox | |
| **Key** | checkbox | key fields must be unique **together** (when the key rule is confirmed) |
| **Note** | text | comment for the business reviewer |
| **Review** | select | open / approved / changes needed |
| **Delete** | checkbox | removes the field on save |

**Rule settings by rule type** (labels change as the rule changes)

| Rule | Settings shown | Stored as | Behaviour |
|---|---|---|---|
| *(not mapped)* | none | `UNMAPPED` | field stays in the target table and the load file, **empty** (`NULL`) |
| MOVE | *Source column* | `source_field` | copy the column unchanged |
| CONSTANT | *Fixed value* | `value` | the same text in every row (an empty box stores an empty value) |
| XREF | *Source column*, *XREF name* (type-ahead of the object's lists), *If no entry* (`error`/`default`/`blank`), *Default value* (only when `default`) | `source_field`, `xref`, `on_unmapped`, `default` | translate through the list `xref/<name>.csv` (details below) |
| CONCAT | *Source columns (comma separated)*, *Separator* | `source_fields` (at least 2), `separator` | join the columns; an empty column counts as empty text (no `NULL` result) |
| SUBSTRING | *Source column*, *Start (1 = first)*, *Length* | `source_field`, `start` (default 1), `length` (>= 1) | `SUBSTR` of the column as text |
| NOT_MIGRATED | none | `NOT_MIGRATED` | the field is **left out of the target table** altogether |
| SQL | *SQL expression (s = this row; other sources: `{{ stg.name }}`)* | `expression` | a SQL expression evaluated per row (see chapter 05 section 4.8) |

XREF policies: `error` = the transform stops and lists the unmapped source values (up to 20); `default` = use the default value for unmapped values; `blank` = leave the target empty for unmapped values.

### S09.7 Buttons under the grid

| Button | Effect |
|---|---|
| **+ Add a field** | appends a blank row (name it, set a rule) |
| **Auto-map by matching names** | saves the page **and** turns every *(not mapped)* field into MOVE when exactly one source column of the main source has the same name (ignoring case, spaces, `_`, `-`). Ambiguous matches stay unmapped. Green "N field(s) mapped by matching names. Check them, then re-run Transform." |
| **Save mapping** | validates and writes `mappings/<structure>.yaml` (comments are not preserved). Green "Mapping saved. Re-run Transform to apply it." |
| **Preview the result** | opens S10 (**save first**: it previews the saved mapping) |

If source columns cannot be listed (a database source not yet extracted) a hint says "Source columns could not be listed (a database source needs an Extract first), so the column box is free text."

### S09.8 Save validation (messages)

| Problem | Message (red "Mapping not saved.") |
|---|---|
| number field not numeric (e.g. Max length `abc`) | `Row N (<field>): invalid literal for int() ...` |
| SUBSTRING without length | `Field '<name>': length - Input should be greater than or equal to 1` |
| CONCAT with one column | `Field '<name>': source_fields - List should have at least 2 items` |
| XREF with policy `default` but no default | `... 'default' is required when on_unmapped is 'default'` |
| duplicate target field | `duplicate target_field '<name>'` |
| MOVE of a column that does not exist | not caught on save for database sources; for files it is caught at **Extract** ("source 'x' does not match the mapping: ... missing source column(s) ...") |
| XREF list does not exist | `... xref file 'xref/<name>.csv' not found` |

### S09.9 Specification workbook (exported by **Download Excel**)

File `mapping-<object>-<structure>.xlsx` with sheets:

| Sheet | Content |
|---|---|
| **Mapping** | one row per target field: Target field, Description, Type, Length, Mandatory (X), Key (X), **Rule**, **Source**, **Setting 1**, **Setting 2**, **Setting 3**, *Logic (read only)* (the rule in plain words), **Note**, **Review**. Header row dark blue; the columns a reviewer may change (Rule, Source, Setting 1-3, Note, Review) are tinted; Rule has a drop-down; Review has a drop-down (open / approved / changes needed); filter and frozen first column. |
| **Rows `<name>`** (one per row set) | the same columns without Note/Review plus **Own rule** (`own rule` / `as main`). Sheet names are cut at 31 characters. |
| **How to read** | how to edit; which Source/Setting columns each rule uses; for row sets, which source and joins each uses |
| **List `<name>`** (one per XREF list used) | the list entries (from / to) |

Per rule, the columns are: MOVE -> Source = column; CONSTANT -> Setting 1 = value; XREF -> Source = column, Setting 1 = list, Setting 2 = error/default/blank, Setting 3 = default value; CONCAT -> Source = columns separated by commas, Setting 1 = separator; SUBSTRING -> Source = column, Setting 1 = start, Setting 2 = length; SQL -> Setting 1 = expression; NOT_MIGRATED/(not mapped) -> nothing.

### S09 test cases

| ID | Steps | Expected |
|---|---|---|
| TC-S09-01 | Open a structure created with way C | All fields MOVE; Progress `N of N`, "all mandatory fields mapped" if none mandatory |
| TC-S09-02 | Open a structure from a template | All fields *(not mapped)* (grey/amber badges); Progress `0 of N` |
| TC-S09-03 | Press **Auto-map by matching names** with a source whose columns equal the field names | Matching fields become MOVE; message shows the count |
| TC-S09-04 | Set a field to CONSTANT `SUPL`, Save | Message "Mapping saved..."; reopening shows CONSTANT / `SUPL` |
| TC-S09-05 | Set XREF with a list that does not exist, Save | Red "Mapping not saved." with "xref file ... not found" |
| TC-S09-06 | Set CONCAT with one column | Red message about `source_fields` |
| TC-S09-07 | Set SUBSTRING with empty length | Red message about `length` |
| TC-S09-08 | Tick Delete on a field, Save | Field removed |
| TC-S09-09 | Rename a field (target name), Save | New name stored; checks/recons that referenced the old name now fail validation ("has no target field") and the save is refused if they exist |
| TC-S09-10 | Mark a field Key and Mandatory, Save, open S14 | Counts of structure rules increase accordingly |
| TC-S09-11 | Type in **Find a field**; tick **only not mapped**; **only mandatory**; choose Review *approved* | Rows hide/show immediately; counts unchanged |
| TC-S09-12 | Sort **not mapped first**, Save, reopen | Field order in the file changed accordingly |
| TC-S09-13 | Tick three rows, bulk **Set this rule** CONSTANT `X` | The three rows show CONSTANT `X`; not saved until Save |
| TC-S09-14 | Tick a configured MOVE row first, then two others, **Copy first rule to the rest** | Others get the same rule and source |
| TC-S09-15 | **Set review** *approved* on ticked rows, Save | Progress shows the approved count; Excel export shows `approved` |
| TC-S09-16 | Add a join (second source, key `ID = ID`), then use `addresses.CITY` in a MOVE | Saved; Preview shows the joined value |
| TC-S09-17 | Add a join with no key | Red "Join not added." + "Give at least one pair of key columns." |
| TC-S09-18 | Remove a join used by a rule | The rule becomes *(not mapped)* |
| TC-S09-19 | Add a row set `FLVN01` reading another source, set `BP_ROLE` CONSTANT `FLVN01`, Save the row set | "1 field(s) with their own rule"; status shows **own rule** for BP_ROLE |
| TC-S09-20 | In the row set, set a field back to the main rule, Save | Override removed ("0 field(s)...") |
| TC-S09-21 | Run Extract/Transform | Target table contains rows of both row sets (see chapter 08 scenario E2E-04) |
| TC-S09-22 | Click **Preview the result** | S10 opens |
| TC-S09-23 | Change the source in *Filled from source* to one lacking a mapped column | Green "This structure now reads from that source..."; **Run extract** then fails with "source '...' does not match the mapping: ... missing source column(s) ..." |
| TC-S09-24 | **Update the target structure** with a template that has a new field | S08 update mode; after confirming the new field is *(not mapped)* |
| TC-S09-25 | Custom-SQL structure | Only the explanation and the link to the file editor |

---

## S10 Preview of the result - `/p/<project>/o/<object>/structures/<structure>/preview?rows=<n>`

**Purpose**: see what Transform would produce **before running it**: the first rows with the source values behind each result, the XREF outcome and rule problems.

**Layout**
1. Title **Preview: `<structure>`**, explanation (nothing is written to the target; red cells break a rule of the target structure).
2. **Rows** select (5, 10, 25, 50) and `N source row(s) in total`.
3. If the structure has XREF rules: card **Value lists (XREF)**: per field `FIELD <- list <name>:` and either a green badge **every value has an entry** or an amber badge **N(+) value(s) without an entry**, the policy that applies ("the transform stops" / "gets the default 'X'" / "stays empty") and the values with how often they occur (up to 10).
4. The grid: header = target field name (`*` = mandatory) with the rule and `max <length>`; each cell shows the **result in bold** (or *(empty)*) and below it `from: <source value> | <source value>` for the columns the rule reads. **Red cells**: "mandatory, no value" or "too long". With row sets an extra first column **Row set** labels each row and notes list `Row set 'X': N source row(s).` The first N rows **of each row set** are shown.
5. Notes such as `Showing the first 10 rows of each row set (T source rows).`

**Errors shown instead of a grid** (red "No preview yet."):

| Situation | Message |
|---|---|
| source or joined source not extracted | `'stg_...' has not been extracted yet - run Extract first` |
| join key repeats (policy stop) | the same "would multiply rows" message as Transform |
| database refused the SQL (a SQL rule) | `structure '...': the database rejected the generated SQL: <reason>. In SQL rules and custom SQL, name other sources as {{ stg.<source> }} ...` |
| custom-SQL structure | `structure '...' is built by custom SQL; only mapping tables can be previewed` |
| every field NOT_MIGRATED | `structure '...': every field is NOT_MIGRATED` |

**Side effect**: the XREF lists are (re)loaded into their `xref_...` tables, exactly as Transform would do first; nothing else is written (no target table).

| ID | Steps | Expected |
|---|---|---|
| TC-S10-01 | Open before Extract | Red "No preview yet." + "run Extract first" |
| TC-S10-02 | After Extract, open | Rows, source values under results, `N source row(s) in total` |
| TC-S10-03 | A mandatory field mapped to a column with an empty value | That cell red "mandatory, no value" |
| TC-S10-04 | A value longer than the field length | Cell red "too long" |
| TC-S10-05 | XREF list missing an entry present in the data | Amber badge with the value and its count; policy text matches the field's policy |
| TC-S10-06 | Join to a source whose key repeats | Red message "would multiply rows" |
| TC-S10-07 | SQL rule `select NAME1 from lfa1 where lifnr = LIFNR` | Red "the database rejected the generated SQL: no such table: lfa1 ..." |
| TC-S10-08 | Structure with a row set | Column **Row set** with labels; rows of both sets |
| TC-S10-09 | Compare with the target table after Transform (S18) | Same values for the same rows |
| TC-S10-10 | Open S17 after previewing | No `tgt_` table was created by the preview |

---

## S11 Import preview of the mapping specification - `/p/<project>/o/<object>/structures/<structure>/spec/<token>`

**Purpose**: bring changes back from the specification workbook (or a simple CSV/Excel) **after seeing every difference**. Nothing is changed until you confirm.

**Step 1 - upload** on S09 (**Import back**). The file is read at once; if unusable you are returned to S09 with red "Could not read the mapping file." and the reason; otherwise S11 opens (the file is kept in memory under a token).

**What the importer reads**
- Excel: the sheet **Mapping**, and every sheet named **Rows `<row set name>`** of an existing row set (other sheets are ignored). A CSV/TXT/TSV is read as the Mapping sheet.
- The **header row** is found automatically (it must contain *Target field*); title rows above it are skipped in Excel. Column names are recognised in several spellings:

| Meaning | Accepted headings |
|---|---|
| Target field | Target field, Target, Field, Field name, Technical name, Feld |
| Description | Description, Field description, Label |
| Rule | Rule, Rule type, Mapping rule |
| Source | Source, Source field, Source column(s) |
| Setting 1/2/3 | Setting 1/2/3, Setting1.., Argument 1.., Parameter 1.. |
| Note | Note(s), Comment(s), Remark(s) |
| Review | Review, Review status, Status, Approval, Sign off |

- At least one of Rule, Source, Note or Review must be present, otherwise red "The file has none of the columns Rule, Source, Note or Review - nothing to import."
- **Rule text**: any case; `(not mapped)`, `not mapped`, `unmapped`, `-` mean not mapped; an **empty rule with a Source filled** means MOVE; `NOT_MIGRATE(D)` accepted. Unknown rule -> row problem `unknown rule 'X'`.
- **Review text**: `approved/ok/yes/agreed/signed off` -> approved; anything starting with `change`, or `rejected/no/not ok/nok` -> changes needed; otherwise open.
- If only the Source column is given (no Rule column), the current rule type is kept and only the source changes.
- Required values per rule (row problems): MOVE/XREF/SUBSTRING need a source ("a source column is required"); CONCAT needs source columns; XREF needs the list name in Setting 1.
- Only **rule, note and review** are imported. Description, type, length, mandatory and key are *not* changed by the import (they belong to the target structure; use S08 update mode).

**Layout of S11**: title `Import mapping from <file>`; explanation; amber card **Rows that cannot be imported** (they keep their current rule); card `N field(s) in the file are not in the target structure` (first 15 names; ignored); a table **Sheet | Target field | What (rule/note/review) | Now | In the file**; button **Import N change(s)** and **Cancel**; `N field(s) are the same as now.` If there is nothing to import: "The file matches the current mapping - there is nothing to import." and a Back button.

**Applying**: the main mapping and the overrides of all row sets present in the file are saved **in one validated write**. Green "N change(s) imported. Re-run Transform to apply them." A rule that references a list that does not exist is refused at this point (red "Import not applied."). Semantics for row-set sheets: each sheet is compared with what it showed when exported (the override, else the main rule *as it was*); a changed rule becomes (or stays) an override; a rule equal to the new main rule removes the override; fields the sheet did not change keep their override or follow the (possibly changed) main rule.

| ID | Steps | Expected |
|---|---|---|
| TC-S11-01 | Download Excel, import it back unchanged | "The file matches the current mapping - there is nothing to import." |
| TC-S11-02 | Change a Rule and a Setting in Excel, import | Table lists the change in plain words; nothing changed yet (check S09); after **Import** the rule is changed |
| TC-S11-03 | Write a note and review `approved` for a field in Excel | Two changes (note, review); S09 shows them; Progress shows 1 approved |
| TC-S11-04 | Rule `TELEPORT` | "Rows that cannot be imported": `unknown rule 'TELEPORT'`; rule unchanged |
| TC-S11-05 | MOVE with empty Source | Row problem "a source column is required" |
| TC-S11-06 | Field name not in the structure | Listed as ignored |
| TC-S11-07 | A CSV with only `Target field,Rule,Source` | Imported as the Mapping sheet |
| TC-S11-08 | Upload a file without a Target field column | Red "Could not read the mapping file." "Could not find the column 'Target field' ..." |
| TC-S11-09 | XREF rule to a list that does not exist, confirm | Red "Import not applied." with "xref file ... not found"; nothing changed |
| TC-S11-10 | Change a rule on sheet `Rows FLVN01`, import | Change tagged with sheet `Rows FLVN01`; stored as override of that row set |
| TC-S11-11 | Change a rule on `Mapping` only; `Rows FLVN01` unchanged | Row set fields without own rule follow the new main rule; no accidental override created |
| TC-S11-12 | Set a row-set rule back to the main rule | Override removed |
| TC-S11-13 | Wait for token expiry and press Import | Red "That upload has expired. Please upload the file again." |
