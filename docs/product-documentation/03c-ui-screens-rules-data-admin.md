# 03c - UI screens: XREF, checks, loads, reconciliation, data, results, SQL, history, files

## S12 XREF lists - `/p/<project>/o/<object>/xref`

**Purpose**: manage value lists that translate legacy values into target values (country codes, units, payment terms).

**Layout**: explanation ("... If the data contains a value that is not in the list, Transform stops and tells you which - nothing is guessed."); hint about ready-made lists on the Proposals tab; table **Name** (link to S13), **Entries**, **Used by** (`structure.field` of every XREF rule of the *main* mapping, or "not used yet"); form **New XREF list** with *Name* and *Entries* (one per line: legacy value, target value; comma, semicolon or tab separated; pasting from Excel works; a header line `legacy_value,target_value` is allowed).

| Action | Result |
|---|---|
| Create, valid | `xref/<name>.csv` written with header `legacy_value,target_value`; redirect to S13; green "XREF created." |
| Invalid name | Red "Could not create the XREF." + "XREF names use letters, digits and '_' and start with a letter." |
| A row without two values | `Row N: needs a legacy value and a target value.` |
| Same legacy value twice | `Legacy value mapped more than once: A, B, ...` |

**Limitation**: *Used by* lists main-mapping rules only; an XREF used only in a row-set override shows "not used yet" (the list is still protected from deletion by project validation).

| ID | Steps | Expected |
|---|---|---|
| TC-S12-01 | Create list `country` with `DE,DE` / `UK,GB` | Listed with 2 entries, "not used yet" |
| TC-S12-02 | Create list with a tab-separated paste from Excel | Parsed correctly |
| TC-S12-03 | Create list with a duplicate legacy value | Red duplicate message; no file |
| TC-S12-04 | Create `1bad` | Red name message |
| TC-S12-05 | Use the list in a mapping, return | "Used by" shows `structure.field` |

## S13 XREF list editor - `/p/<project>/o/<object>/xref/<name>`

**Layout**
1. Title **XREF list: `<name>`**.
2. If staged data contains values of the mapped source column(s) without an entry: box "**N value(s) in the staged data have no entry yet:** v1, v2, ..." (first 30) with **Add them to the list below** (appends `value,` lines to the text box; you then fill in the target values and save). Values come from up to 2000 distinct staged values of the source column of each *main-mapping* XREF rule of this list.
3. Text box **Entries** (`legacy_value,target_value`, one per line, including the header line), **Save**.
4. **Replace from a CSV file** (upload; read as UTF-8, up to 5 MB) with **Upload**.
5. **Delete this list** (confirm).

| Action | Message |
|---|---|
| Save ok | Green "XREF saved. Re-run Transform to apply it." |
| Save invalid | Red "XREF not saved." + reasons (as in S12) |
| Upload ok / invalid | Green "XREF replaced from the uploaded file." / red "File not used." |
| Delete | Green "XREF deleted." (redirects to S12). If a mapping still uses it: red "Could not delete it." + "xref file 'xref/<name>.csv' not found" (nothing deleted) |
| Open unknown list | 404 "Unknown XREF '<name>'." |

| ID | Steps | Expected |
|---|---|---|
| TC-S13-01 | After Extract, open a list used by a field whose data holds an unlisted value | Box lists the value; **Add them** appends `value,` |
| TC-S13-02 | Fill the target value, Save, run Transform | Transform succeeds for that value |
| TC-S13-03 | Upload a CSV replacing the list | Entries replaced |
| TC-S13-04 | Delete a list used by a mapping | Refused (red) |
| TC-S13-05 | Delete an unused list | Removed; S12 no longer lists it |
| TC-S13-06 | Run Transform with an XREF rule (policy error) and an unlisted value in the data | Transform fails listing the values (up to 20); target table not replaced |

---

## S14 Checks - `/p/<project>/o/<object>/checks`

**Purpose**: confirm and manage the **checks that validate the target data**. **Only confirmed checks run.** An `error` check blocks the load; a `warn` check only reports.

**Layout**
1. Explanation ("Checks validate the target data - and only the checks you have confirmed ever run...").
2. Card **Rules from your target structure** (off until switched on): a table with one row per kind - **Mandatory fields must be filled** (n fields flagged mandatory), **Values must fit the length** (n fields with a length), **Values must match the data type** (n number/decimal/date/boolean fields), **Key fields must be unique** (n fields flagged as key; checked together). For each kind a select: *off - not checked*, *warning - report only*, *error - blocks the load*; button **Save**. Saved in `checks.yaml` under `from_mapping`. Green "Saved. Confirmed rules: mandatory, key." (or "none").
3. **Run checks** button ("Runs only the confirmed checks below and above.").
4. Hint to the Proposals tab.
5. Table of **your own checks**: Name (+ note), Type, Structure, Severity badge (`error`, or amber `warn`), Settings (`key=value; ...`), **Delete** (confirm).
6. **Add** form: *Type*, *Name*, *Structure*, *If it fails* (block the load (error) / only warn), the settings of the chosen type (the form shows only that type's fields), *Note (optional)*, **Add**.

**Check types** (settings in the form; semantics in chapter 05 section 5)

| Type | Form fields | A row fails when |
|---|---|---|
| UNIQUE | Fields that together must be unique (comma separated) | the combination of those fields occurs more than once |
| ALLOWED_VALUES | Field; Allowed values (comma separated) | the value (as text) is not in the list (NULL values are not checked; an empty text value is) |
| REFERENTIAL | Field; Must exist in structure; ... in field; ... of object (blank = this object) | a non-empty value has no match in the referenced structure's field (compared as text) |
| NULL_RATE | Field; Maximum % empty | the share of empty values exceeds the limit (one failure for the whole field) |
| PATTERN | Field; Regular expression (whole value) | a non-empty value does not match the regex **completely** |
| RANGE | Field; Minimum; Maximum (at least one) | a non-empty value is not a number, or is outside the range |
| OUTLIER | Field; Maximum z-score (default 4) | the value is further than that many standard deviations from the mean (needs at least 3 numeric values and a non-zero deviation) |
| SQL_ASSERT | SQL returning the bad rows | the query returns a row (include `"_dmc_row_no"` in the select to point at the source row) |

Structure-based kinds (from `from_mapping`): **mandatory** (empty or blank), **length** (text longer than the field length), **data_type** (integer, decimal, ISO date `YYYY-MM-DD`, boolean `true/false/1/0`; empty values are not type errors), **key** (combination of the key fields repeated).

**Messages**

| Situation | Message (red title "Not added.") |
|---|---|
| name missing/invalid | "Give it a name (letters, digits, '_'; starts with a letter)." |
| required field empty | `<field label>: A required value is empty.` |
| number not numeric | `<field label>: 'x' is not a number.` |
| same name again | "There is already an entry called 'x'." |
| unknown structure / field | `check 'x': unknown structure 'y'` / `check 'x': structure 's' has no target field 'f'` |
| bad regex | `pattern: invalid regular expression: ...` |
| RANGE without bounds | `at least one of 'min' or 'max' is required` |
| success | green "Added. Re-run the step to see the effect." |
| delete | green "Deleted." / red "Not deleted." + "No entry called 'x'." |

**Run results** (flash on S04/S14/S15): per check `PASS`, `FAIL` (error), `WARN` (warn), `ERROR` (the check could not run: unknown column, bad SQL). Failing rows are stored with their **source row number** (S19). A message reads `N row(s) failed (e.g. v1, v2, v3)` (+ a hint, e.g. "the field is mandatory but not mapped yet").

| ID | Steps | Expected |
|---|---|---|
| TC-S14-01 | New object, run checks | "No checks are confirmed yet, so nothing was run..." |
| TC-S14-02 | Set Mandatory to *error*, Save, run Transform + Check | Check `mandatory.<structure>.<field>` per mandatory field; PASS/FAIL accordingly |
| TC-S14-03 | Mandatory *warning* with an empty mandatory value | WARN (not blocking); load allowed |
| TC-S14-04 | Mandatory *error* with an unmapped mandatory field | FAIL "... mandatory but empty ... the field is mandatory but not mapped yet"; load blocked |
| TC-S14-05 | Add UNIQUE on a field with duplicates | FAIL listing duplicates `duplicate key (FIELD=value)`; rejects have source row numbers |
| TC-S14-06 | Add ALLOWED_VALUES with values `0001,0030,0060` and data containing `0090` | FAIL for that row (e.g. `0090`) |
| TC-S14-07 | Add PATTERN `\d{10}` on a key with letters | FAIL "does not match \d{10}" |
| TC-S14-08 | Add RANGE min 0 max 1000 with 5000 in data | FAIL "outside the allowed range" |
| TC-S14-09 | Add OUTLIER with a clear outlier | FAIL "is an outlier (z-score x.y)" |
| TC-S14-10 | Add NULL_RATE 5% on a field that is 20% empty | FAIL "is empty in 20.0% of rows (allowed 5%)" |
| TC-S14-11 | Add REFERENTIAL bank.PARTNER -> general.PARTNER with an orphan | FAIL "not found in general.PARTNER" |
| TC-S14-12 | Add SQL_ASSERT with `{{ tgt.general }}` | Runs; rows returned are failures |
| TC-S14-13 | Add a check on a field name that does not exist | Red "has no target field" |
| TC-S14-14 | Add a check twice with the same name | Red "There is already an entry called ..." |
| TC-S14-15 | Delete a check | Removed |
| TC-S14-16 | Run checks before Transform | Red "target table(s) not found: ...; run transform first" |

---

## S15 Loads - `/p/<project>/o/<object>/loads`

**Purpose**: define where finished target data goes and run the load. **Loading always runs the confirmed checks first** and stops if any error-severity check fails.

**Layout**
1. Explanation (file in the project's `output` folder or a database table; atomic writes).
2. Card stating either "**N confirmed check(s) and M confirmed structure rule(s)** are run before every load..." or "**No checks are confirmed, so nothing can hold this load back.**", with **Run load** (disabled with "Add a load destination below first." when none exists; otherwise "Needs the target tables (run Transform first).").
3. Hint: a default destination per structure is offered on the Proposals tab.
4. Table **Structure | Kind | Destination | Delete**: file `path (format)`; table `connection: table (mode)`.
5. **Add a destination**: *Structure*; *Kind* = File or Database table; **File**: *Path in the project* (default `output/<object>.csv`), *Format* csv/xlsx/parquet, *CSV delimiter*, *include row-tracking columns*; **Table**: *Connection*, *Table* (`schema.table` allowed), *If it exists* = replace/append.

**Load behaviour**
- All configured destinations are written one after another, each as its own step `load:<structure>:<kind>`.
- Files: written to a temporary file then renamed (atomic); folders are created; CSV uses the delimiter and encoding (UTF-8 default); Excel uses a sheet named after the structure (31 characters); Parquet needs all chunks in memory. Row-tracking columns `_dmc_row_no`, `_dmc_run_id` are excluded unless ticked.
- Tables: one transaction; `replace` drops and recreates the table, `append` creates it if missing and adds rows; column types are derived from the data.
- After writing, the number of rows read must equal the number written, otherwise the load fails ("structure 'x': read N rows but wrote M").
- Rows are written in source-row order (`_dmc_row_no`).

**Messages**: green "Load destination added." / red "Not added." + reason (`duplicate load destination(s): ...`, `load: unknown structure 'x'`, `load of 'x': unknown connection 'c'`, `load of 'x': path '..\..' leaves the project`); green "Deleted." / red "Not deleted." + "No such load."

| ID | Steps | Expected |
|---|---|---|
| TC-S15-01 | No destination | Run load disabled with the hint |
| TC-S15-02 | Add a CSV destination for a structure, Transform, **Run load** | Green "Loaded 1 destination(s)."; file in `output/`; row count equals the target table |
| TC-S15-03 | Add the same path twice | Red duplicate message |
| TC-S15-04 | Path `../../x.csv` | Red "leaves the project" |
| TC-S15-05 | Add Excel and Parquet destinations | Both written; Excel sheet named after the structure |
| TC-S15-06 | Add a database table destination (connection `working`, table `out_x`, replace), run load twice | Table recreated each time (rows unchanged); with *append* rows double |
| TC-S15-07 | Confirmed error check failing, Run load | Red "Nothing was loaded: checks are blocking the load." + failing checks; destination files untouched (an older file is not overwritten) |
| TC-S15-08 | Warning-only failures | Load proceeds |
| TC-S15-09 | With no confirmed check | Card says nothing can hold the load back; load proceeds |
| TC-S15-10 | Tick *include row-tracking columns* | File has `_dmc_row_no`, `_dmc_run_id` columns |
| TC-S15-11 | Download link on S04 for a file destination | File downloads (only below `output/` or `reports/`) |
| TC-S15-12 | Delete a destination | Removed; if it was the last one the `loads` key disappears |

---

## S16 Reconciliation - `/p/<project>/o/<object>/recon`

**Purpose**: prove that nothing was lost or altered between the staged source and the target. Same page structure as S14 (explanation, **Run reconciliation**, table of reconciliations, **Add** form), but without structure-based rules.

**Reconciliation types**

| Type | Form fields | Meaning |
|---|---|---|
| COUNT | Allowed difference in rows (optional) | the number of target rows equals the number of source rows (minus rows deliberately excluded; with row sets, the sum of all row sets' source rows) |
| KEYS | Source key fields; Target key fields (comma separated, same count) | every source key exists in the target and vice versa (keys compared as text, trimmed) |
| TOTALS | Source field; Target field; Calculation (`sum`, `count`, `count_distinct`, `min`, `max`); Group by source/target fields (optional); Allowed difference (optional) | a control total agrees, optionally per group |
| FIELDS | Source key; Target key; Fields to compare (`auto` or `SOURCE:TARGET` pairs); Numeric tolerance (optional); Ignore upper/lower case | for matching keys, field values agree (`auto` = every MOVE field of the main source; joined columns are skipped) |

**Common optional fields** (all types): *Compare against this table instead* (a table in the working database, e.g. an extract of the target system) and *SQL condition for source rows deliberately not migrated* (a condition over alias `s`; those rows are not expected in the target).

**Results**: statuses and blocking behave like checks (`error` severity differences block; reported as `DIFFERENCES` in the CLI). Failures give source/target row numbers, field, value and message (`key is in the source but missing in the target`, `key is in the target but not in the source`, `sum is X in the source but Y in the target`, ...).

**Limitations**: KEYS/FIELDS/TOTALS read the structure's **own (main) source** only; with row sets they do not cover the other row sets (COUNT does). Proposals therefore offer only COUNT for structures with row sets.

| ID | Steps | Expected |
|---|---|---|
| TC-S16-01 | Add COUNT, run after Transform | PASS when counts equal |
| TC-S16-02 | Add a COUNT with an `exclude_source` condition matching 1 row, with that row not migrated | PASS (the excluded row is not expected in the target; the "intentionally excluded" note only appears when counts still differ) |
| TC-S16-03 | Delete a row from the target table (Files/DB) and run COUNT | FAIL "source has N rows ..., so N expected; target has N-1 (difference -1)" |
| TC-S16-04 | KEYS with a missing and an extra key | Two failure kinds listed |
| TC-S16-05 | TOTALS sum of an amount after changing one target value | FAIL "sum is X in the source but Y in the target" |
| TC-S16-06 | FIELDS auto after changing a target value | FAIL for that row and field |
| TC-S16-07 | Run before Transform | Red "table(s) not found: ...; run transform first" / "run extract" |
| TC-S16-08 | Structure with a row set: COUNT | PASS when target = sum of both row sets |

---

## S17 Data (tables) - `/p/<project>/o/<object>/data`

Lists the tables each step has built for this object (only tables that exist): **Stage** (`Staging (source as read)`, `XREF lookup`, `Target (after transform)`), **Table** (link to S18), **Rows**. If none: "Nothing yet - run Extract first."

## S18 Table preview - `/p/<project>/o/<object>/data/<table>`

Shows the table list and, below it, the chosen table: `<table> (N rows)`; **Filter** (text contained in **any** column, using the database's `LIKE`, so case sensitivity follows the database: SQLite ignores case for plain letters, PostgreSQL does not); the first page of **50** rows ordered by `_dmc_row_no`; `Page x of y` with previous/next. A table that does not belong to the object gives 404 "Table '...' is not part of this object."

| ID | Steps | Expected |
|---|---|---|
| TC-S17-01 | After Extract/Transform | Staging, XREF and target tables listed with counts matching Flow |
| TC-S18-01 | Open a staging table | Columns `_dmc_row_no`, `_dmc_run_id` then source columns; text values keep leading zeros |
| TC-S18-02 | Filter by a value | Only matching rows; total updates |
| TC-S18-03 | Open a target table | One column per mapped field; *(not mapped)* fields empty; NOT_MIGRATED fields absent |
| TC-S18-04 | Page through a table with > 50 rows | Previous/next work |
| TC-S18-05 | Edit the URL to another object's table | 404 "not part of this object" |

---

## S19 Results - `/p/<project>/o/<object>/results[?check=<name>]`

**Purpose**: the detail behind the Flow's status: every check and reconciliation result, the rejected rows and the column profile.

**Layout**
1. **Overall** badge (OK / ATTENTION / BLOCKED / FAILED). If nothing has run: "Nothing has been run for this object yet."
2. **Checks** table and **Reconciliation** table: Check (link to its rejected rows when it has failures), Type, Structure, Severity, Result badge (PASS / FAIL / WARN / ERROR), Rows, Failures (`+` appended when the stored rejects were capped), Message.
3. **Rejected rows** (all, or `for <check>` with a *(show all)* link): Check, Structure, **Source row** (row number in the original file or table), Field, Value, Why. Up to 500 rows per check are listed (up to 10,000 are stored per check).
4. **Column profile** per staged table: Column, Rows, Empty, Distinct, Min, Max, Length (min-max), Top values (4), Top shapes (3) - shape: digit=`9`, capital=`A`, lower case=`a`.

The data comes from the **newest run of each step kind**.

| ID | Steps | Expected |
|---|---|---|
| TC-S19-01 | Before running anything | "Nothing has been run for this object yet." |
| TC-S19-02 | After a run with failing checks | Failing checks have links; rejects show source row numbers matching the source file lines |
| TC-S19-03 | `?check=<name>` | Only that check's rejects |
| TC-S19-04 | Run Profile, reopen | Profile tables per staged table with top values/shapes |
| TC-S19-05 | Warn-severity failure | Result badge WARN (amber); overall ATTENTION |

## S20 SQL - `/p/<project>/o/<object>/sql`

Shows, per structure, the **exact SQL Transform runs** (`DROP TABLE IF EXISTS ...;` and `CREATE TABLE ... AS SELECT ...`), generated from the current files. Custom SQL appears as written after template rendering; SQL rules appear inside the SELECT; row sets appear as `UNION ALL` of one SELECT per row set; joins as `LEFT/INNER JOIN`; XREFs as `LEFT JOIN xref_...`. The same text is written to `build/<object>/<structure>.sql` by Transform. The page needs no data in the database.

| ID | Steps | Expected |
|---|---|---|
| TC-S20-01 | Open for a mapped structure | `CREATE TABLE tgt_... AS SELECT` with every non-NOT_MIGRATED field |
| TC-S20-02 | Add a join / row set | `JOIN` / `UNION ALL` appear |
| TC-S20-03 | Compare with `build/<object>/<structure>.sql` after Transform | Identical SQL |

## S21 Run history - `/p/<project>/runs`

The last **60** runs of the project, newest first: **When** (UTC time of start), **Object** (link), **Steps** (comma-separated step names such as `extract:lfa1, extract:lfbk`), **Status** (FAILED when the run or any step failed, otherwise SUCCESS), **Run id**. Empty: "Nothing has run yet."

Test cases: TC-S21-01 every pressed step adds a run; TC-S21-02 a failed step shows FAILED; TC-S21-03 a no-check run adds no `checks` step.

## S22 Project files (advanced editor) - `/p/<project>/files`

**Purpose**: edit the YAML, SQL and XREF files directly.

**Layout**: left **Files** list (links); right the editor of the chosen file (`<path>`, text area, **Save**).

**Editable files only**: `project.yaml`; `objects/<o>/object.yaml`, `checks.yaml`, `recon.yaml`; `objects/<o>/mappings/*.yaml`; `objects/<o>/sql/*.sql`; `objects/<o>/xref/*.csv`. Any other path: 404 "That file cannot be edited here." (also for `..` paths).

**Saving**: YAML must parse ("Not valid YAML: ..."); the project is then validated; if the save would add a problem, the **file is restored** and a red "Not saved - the file was left as it was." lists the problems. Green "Saved." otherwise. Windows line endings are converted to `\n`.

| ID | Steps | Expected |
|---|---|---|
| TC-S22-01 | Open `project.yaml` | Content shown |
| TC-S22-02 | Break the YAML syntax and Save | Red "Not valid YAML"; file unchanged |
| TC-S22-03 | Reference a source that does not exist in `object.yaml` and Save | Red with "unknown source ..."; file unchanged |
| TC-S22-04 | Valid edit (change a description) | Green "Saved."; change visible on S03 |
| TC-S22-05 | URL `?path=../outside.txt` | 404 "That file cannot be edited here." |
| TC-S22-06 | Create `objects/<o>/sql/custom.sql`, reference it as `custom_sql` for a structure (replacing `mapping`) | Structure builds from the SQL (S20 shows it) |

## S23 Error and security pages

| Situation | Page |
|---|---|
| Unknown project/object/structure/table/XREF | HTTP 404 "Not found" + reason |
| Invalid project files | HTTP 422 "This project has problems" + one line per problem (+ link to Files) |
| Request from a non-local host | HTTP 403 "This tool only answers on localhost." |
| Cross-site POST | HTTP 403 "Cross-site request refused." |
| Download outside `output/` / `reports/` | 404 "That file is not available." |

Test cases: TC-SEC-01 open `/p/nope` -> 404; TC-SEC-02 request with a foreign `Host` header -> 403; TC-SEC-03 POST with a foreign `Origin` -> 403; TC-SEC-04 `/p/<p>/download?path=project.yaml` -> 404; TC-SEC-05 `?path=../x` on download -> 404.
