# 08 - Test guide

How to test the product thoroughly, by hand and automatically.

1. **Section 1** - prepare the sample data (invented; no real client data).
2. **Section 2** - end-to-end scenarios `TC-E2E-nn` with exact expected results.
3. **Section 3** - data-handling cases `TC-DATA-nn`.
4. **Section 4** - non-functional and security cases.
5. **Section 5** - the automated test suite and how it maps to the manual cases.
6. The screen-level cases are in chapters 03a-03c (`TC-S..`), the CLI cases in chapter 06, proposals in chapter 07.

**Rules for testers**: use a fresh workspace for each scenario (`dmc ui --workspace <empty folder>`); after each action compare the *flash message* title and lines with the "Expected" column; record the project folder (it is plain text, so you can diff it with git).

## 1. Sample data

Create these files in a scratch folder (UTF-8, comma separated, Windows or Unix line endings).

**`customers.csv`** (3 rows)
```
ID,NAME,COUNTRY,AMOUNT
0001,Alpha Ltd,UK,10.5
0002,Beta GmbH,DE,20
0003,Gamma SA,Atlantis,30.25
```

**`addresses.csv`** (2 rows)
```
ID,CITY
0001,Paris
0002,Rome
```

**Vendor data set** (for joins and UNION ALL)

`lfa1.csv` (3 rows)
```
LIFNR,NAME1
1,Acme
2,Beta
3,Gamma
```
`lfb1.csv` (3 rows - company codes)
```
LIFNR,BUKRS
1,1000
1,2000
2,1000
```
`lfm1.csv` (4 rows - purchasing organisations)
```
LIFNR,EKORG
1,P100
3,P100
3,P200
3,P300
```

**Pasted target structure for customers** (tab-separated; paste exactly)
```
ID	Customer id	Text	4	X
NAME	Name	Text	20	X
COUNTRY	Country	Text	2
AMOUNT	Amount	DEC	10
```

**Pasted target structure for vendors**
```
LIFNR	Vendor	Text	10	X
NAME1	Name	Text	30
BP_ROLE	Role	Text	6	X
ORG	Organisation	Text	4
```

## 2. End-to-end scenarios

### TC-E2E-01 Quick tour with the demo project

| Step | Action | Expected |
|---|---|---|
| 1 | CLI: `uv run dmc demo --dir dmc_demo --force` | `material: OK` - checks passed 13/13, `loaded: output/material_basic.csv (10 rows)`, reconciliations 4/4; `supplier: OK` - checks passed 27/27, `loaded: output/supplier_general.csv (12 rows)`, `loaded: output/supplier_bank.xlsx (13 rows)`, reconciliations 5/5; report paths |
| 2 | Open `dmc_demo/reports/supplier_report.html` | Overall OK; Steps, Checks, Reconciliation, Profile sections |
| 3 | CLI: `uv run dmc demo --defects --dir dmc_demo_bad --force` | `material: BLOCKED` - checks passed 11/13, `LOAD BLOCKED by: weight_in_range`; `supplier: BLOCKED` - checks passed 23/27, `LOAD BLOCKED by: mandatory.general.NAME_ORG1, partner_unique, partner_is_numeric, bank_partner_exists`, reconciliations passed 4/5; no output files are written |
| 4 | UI: create demo `acme_demo` with the defects box ticked; object `supplier` -> **Run everything** | Flash: extract OK, transform OK, check PROBLEM (blocking), load PROBLEM ("Nothing was loaded: checks are blocking the load."), recon/profile/report OK; Flow overall BLOCKED; "Why the load is blocked" lists the failing checks |
| 5 | Click a failing check; open S19 | Rejected rows with source row numbers that match the line numbers of `lfa1.csv` (row 1 = first data row) |

### TC-E2E-02 Build a migration object from scratch (customers)

| Step | Screen | Action | Expected |
|---|---|---|---|
| 1 | S01 | Create project `cust_demo` | S03, green "Project created..." |
| 2 | S03 | Create object `customer` with `customers.csv` | S07; Sources tab shows source `customers`; **no structure** |
| 3 | S04 | Press **Run transform** | Red: "object 'customer' has no target structure yet; define one before transforming" |
| 4 | S07 | Way B: structure name `general`, paste the customer structure, Continue | S08 shows 1 structure, 4 fields, 2 mandatory, ticked; table name `general`; source `customers` |
| 5 | S08 | **Create the selected structures** | Green "1 target structure(s) created..." |
| 6 | S07 | Open `general` | S09; Progress `0 of 4` mapped; two amber "not mapped" mandatory badges |
| 7 | S05 | Open Proposals (before Extract) | Green "Run Extract..." box; red box lists mandatory fields ID, NAME not mapped; proposals (10): four mappings "Fill ID from ID", "Fill NAME from NAME", "Fill COUNTRY from COUNTRY", "Fill AMOUNT from AMOUNT" (the source columns are known from the file); structure rules "Mandatory fields must be filled: 2 field(s)", "Values must fit the field length: 4 field(s)", "Values must match the field's data type: 1 field(s)"; the check "COUNTRY: Country codes are two capital letters (ISO 3166)"; the reconciliation "general: the target has as many rows as the source"; the load "Write general to output/general.csv". **Compare the project files before/after opening the page: identical** |
| 8 | S09 | **Auto-map by matching names** | Green "4 field(s) mapped by matching names..."; all four MOVE |
| 9 | S04 | **Run extract** | OK "Staged 1 source(s)"; `stg_customer_customers: 3 rows, 4 columns` |
| 10 | S18 | Open `stg_customer_customers` | `ID` values `0001`, `0002`, `0003` (leading zeros kept); `_dmc_row_no` 1-3 |
| 11 | S05 | Reopen | Mapping proposals are gone (all mapped). New data-based proposals: "Translate general.COUNTRY through a country list" (note "Not recognised, so left out: Atlantis. The transform will stop and list them until you add them yourself."), "AMOUNT: no negative values", "ID: keep the layout 9999", "general: the total of AMOUNT is unchanged"; plus the rules/check/recon/load of step 7 |
| 12 | S12 | Create XREF `country` with `UK,GB` and `DE,DE` | Listed, 2 entries |
| 13 | S09 | COUNTRY: rule XREF, source `COUNTRY`, list `country`, policy `error`; Save | Green "Mapping saved..." |
| 14 | S10 | **Preview the result** | 3 rows; COUNTRY shows GB, DE and (empty); XREF card: amber "1 value(s) without an entry" with `Atlantis (1x)`, policy "the transform stops" |
| 15 | S04 | **Run transform** | Red PROBLEM; detail "structure 'general' has unmapped values:" / "field 'COUNTRY': no entry in xref 'country' for 'Atlantis'"; target table not created |
| 16 | S13 | Open list `country`; the box shows `Atlantis`; **Add them to the list**; fill `XX`; Save | Green "XREF saved..." |
| 17 | S04 | **Run transform** | OK "Built 1 target table(s)"; `tgt_general: 3 rows from 3 staged`; file `build/customer/general.sql` exists |
| 18 | S18 | Open `tgt_general` | COUNTRY = GB, DE, XX; AMOUNT `10.5`, `20`, `30.25` |
| 19 | S14 | Run checks with nothing confirmed | "No checks are confirmed yet, so nothing was run..." |
| 20 | S14 | Set Mandatory, Length, Data type to *error*, Save (Key stays off: no key field) | Green "Saved. Confirmed rules: mandatory, length, data_type." |
| 21 | S14/S04 | **Run checks** | "7 of 7 checks passed." (mandatory ID, NAME; length ID, NAME, COUNTRY, AMOUNT; data type AMOUNT) |
| 22 | S14 | Add ALLOWED_VALUES `country_ok` on `general.COUNTRY`, values `GB,DE`, severity *block the load (error)* | Green "Added." |
| 23 | S04 | **Run checks** | "7 of 8 checks passed; 1 blocking the load." `BLOCKING: country_ok - 1 row(s) failed (e.g. XX)`; S19 shows the rejected row with source row **3** |
| 24 | S15 | Add a file destination `output/customer.csv` csv; **Run load** | Red "Nothing was loaded: checks are blocking the load." ; no `output/customer.csv` |
| 25 | S14 | Delete `country_ok`; add it again with severity *only warn*; **Run load** | Green "Loaded 1 destination(s)." `general -> output/customer.csv: 3 rows`; Flow overall ATTENTION after Write report (warn failure) |
| 26 | file | Open `output/customer.csv` | Header `ID,NAME,COUNTRY,AMOUNT` (no lineage columns); first row `0001,Alpha Ltd,GB,10.5` |
| 27 | S16 | Add COUNT recon `rows`, TOTALS `amount_total` (source AMOUNT -> target AMOUNT, sum); **Run reconciliation** | "2 of 2 reconciliations agree." (sum 60.75) |
| 28 | S04 | **Run profile**, **Write report**; open the HTML report | Profile for `stg_customer_customers`: ID length 4-4, shapes `9999`; Rejects (sample) lists the warn failure |
| 29 | S21 | Open Run history | One run per pressed step, newest first; the failed transform shows FAILED |
| 30 | S04 | Re-run **Extract** only | Transform/checks/load/recon cards show **out of date** |

### TC-E2E-03 Joins (several source tables for one structure)

Continue from TC-E2E-02 (or repeat quickly).

| Step | Action | Expected |
|---|---|---|
| 1 | S06: upload `addresses.csv` | Source `addresses` added |
| 2 | S09 (general): **Joined sources**: source `addresses`, left, key `ID` = `ID`, repeated key `stop`; **Add join** | Green "Source joined..." |
| 3 | S09: **+ Add a field** `CITY`, rule MOVE, source `addresses.CITY` (type-ahead offers it); Save | Saved |
| 4 | S04: Run extract, Run transform | OK; `tgt_general: 3 rows from 3` |
| 5 | S18: `tgt_general` | CITY: Paris, Rome, (empty) for the third customer (left join keeps all) |
| 6 | S09: remove the join (the CITY rule is reset to *(not mapped)*), add it again as **inner**, set CITY to MOVE `addresses.CITY` again, Save, Run transform | **2 rows** (only customers with an address) |
| 7 | Add the line `0001,Lyon` to `objects/customer/data/addresses.csv` **on disk**, set the join back to *left, stop* (remove and add again), Run extract, Run transform | Red: "structure 'general': the join with 'addresses' would multiply rows because its key (ID) is not unique: 0001 appears 2 times; ... Fix the data, or set the join to use the first row only." Target table unchanged |
| 8 | Remove and re-add the join with *repeated key: first row*, set CITY again, Save, Run transform | OK; 3 rows; CITY for 0001 = `Paris` (first row) |
| 9 | S20 | SQL shows `LEFT JOIN (SELECT * ... MIN("_dmc_row_no") ... GROUP BY ...) AS addresses` |
| 10 | S09: remove the join | The CITY rule becomes *(not mapped)* |

### TC-E2E-04 UNION ALL row sets (vendor example)

| Step | Action | Expected |
|---|---|---|
| 1 | New project; object `vendor` from `lfb1.csv`; S06 upload `lfa1.csv` and `lfm1.csv` | Sources `lfb1`, `lfa1`, `lfm1` |
| 2 | S07 way B: name `vendor`, paste the vendor structure; S08: target table `bp_vendor`, **source `lfb1`**; Create | Structure created |
| 3 | S09: **Joined sources**: `lfa1`, left, `LIFNR` = `LIFNR`, stop | Green |
| 4 | Map: LIFNR MOVE `LIFNR`; NAME1 MOVE `lfa1.NAME1`; BP_ROLE CONSTANT `FLVN00`; ORG MOVE `BUKRS`; Save | Saved |
| 5 | **Row sets**: name `FLVN01`, reads `lfm1`, joined with `lfa1`, key `LIFNR` = `LIFNR`; **Add row set** | Green "Row set added: its rows are put below the main rows (UNION ALL)..." |
| 6 | In the row set grid: BP_ROLE -> CONSTANT `FLVN01`; ORG -> MOVE `EKORG`; **Save FLVN01** | "Row set 'FLVN01' saved: 2 field(s) with their own rule, the rest follow the main mapping." Status column shows **own rule** for BP_ROLE and ORG; LIFNR and NAME1 show *as main* |
| 7 | Run extract; open S10 | Column **Row set** (`main`, `FLVN01`); main rows show `FLVN00`/`1000`...; FLVN01 rows show `FLVN01`/`P100`... with the joined name |
| 8 | Run transform | `tgt_bp_vendor: 7 rows from 7 staged` (3 + 4 source rows) |
| 9 | S18 `tgt_bp_vendor`, sort by reading | Rows (LIFNR, NAME1, BP_ROLE, ORG): (1, Acme, FLVN00, 1000), (1, Acme, FLVN00, 2000), (2, Beta, FLVN00, 1000), (1, Acme, FLVN01, P100), (3, Gamma, FLVN01, P100), (3, Gamma, FLVN01, P200), (3, Gamma, FLVN01, P300) |
| 10 | S20 | SQL has two SELECTs separated by `UNION ALL` |
| 11 | S16: add COUNT `rows`; Run reconciliation | PASS: expected 7 (3 + 4), actual 7 |
| 12 | S05 | Only the row-count reconciliation is proposed for `vendor` |
| 13 | Put `EKORG` misspelt in a row-set rule (e.g. `EKORX`), save row set, Run extract | Red: "source 'lfm1' does not match the mapping: structure 'vendor', row set 'FLVN01', field 'ORG': missing source column(s) EKORX" |
| 14 | Remove the row set | Green "Row set removed."; Transform gives 3 rows |

### TC-E2E-05 Bulk tools and Excel specification round trip (uses E2E-04 or E2E-02)

| Step | Action | Expected |
|---|---|---|
| 1 | S09: tick two fields; bulk **Set this rule** CONSTANT `X`; do **not** save; reload | Back to the saved rules (changes were page-only) |
| 2 | Repeat, then **Save mapping** | Rules persisted |
| 3 | Tick fields, **Set review** *approved*, Save | Progress shows the approved count |
| 4 | **Download Excel** | Workbook with `Mapping` (+ `Rows FLVN01` if present), `How to read`, `List ...`; Review column shows `approved` / `open`; Logic column in plain words (e.g. "Copy NAME.", "Always 'X'.") |
| 5 | **Import back** the unchanged file | "The file matches the current mapping - there is nothing to import." |
| 6 | Edit in Excel: change one Rule/Source, add a Note, set Review `changes needed`; upload | Table lists the three changes with Now / In the file; nothing is changed yet (check S09) |
| 7 | Press **Import N change(s)** | Green "N change(s) imported. Re-run Transform to apply them."; S09 shows the changes; Progress counts "need changes" |
| 8 | Import a file with rule `TELEPORT` for a field | "Rows that cannot be imported" lists it; the rule is unchanged |
| 9 | Excel `Rows FLVN01`: change a Setting 1; import | Change tagged with that sheet; override updated |
| 10 | S09 sort **not mapped first**, filter **only mandatory**, Review filter | Rows reorder/hide as described in S09.4 |

### TC-E2E-06 Defining the target structure from a file

| Step | Action | Expected |
|---|---|---|
| 1 | S07: download the blank Excel template; fill two structures (`general`, `bank`) with `X` for mandatory/key; upload (way A) | S08 lists both; mandatory/key counts correct |
| 2 | Tick both, set tables, **Create** | Two structures |
| 3 | Open one, **Update the target structure** with the same file where one field's length changed and one field added | "1 field(s) added, 1 detail(s) updated, 0 field(s) not in the template (kept)."; rules unchanged |
| 4 | Upload a SAP Migration Cockpit template (`.xml` or `.zip`) that you own | Structures per SAP structure; mandatory/key detected. (Do **not** place real SAP files in the repository.) |

### TC-E2E-07 Safety: invalid edits are refused and rolled back

| Step | Action | Expected |
|---|---|---|
| 1 | S22: open `objects/<o>/object.yaml`; change a structure's `source` to `nosuch`; Save | Red "Not saved - the file was left as it was." + "unknown source 'nosuch'"; reopen: unchanged |
| 2 | S22: break the indentation of a YAML file | Red "Not valid YAML: ..." |
| 3 | S14: add a check for field `NOPE` | Red "... has no target field 'NOPE'" |
| 4 | S06: remove a source used by a structure | Red; source remains |
| 5 | S09: rename a field that a check references | Red "Mapping not saved." with "has no target field" |
| 6 | Compare the project folder with git before/after (`git status`) | no partial changes |

### TC-E2E-08 PostgreSQL

| Step | Action | Expected |
|---|---|---|
| 1 | `docker compose up -d db`; set `DMC_WORKING_DB_URL` to the PostgreSQL URL; `uv run dmc ui` | Project page shows "Working database: environment variable DMC_WORKING_DB_URL" |
| 2 | Repeat TC-E2E-02 and TC-E2E-04 | Same results as SQLite |
| 3 | A SQL rule with a multi-row sub-select | Fails on PostgreSQL ("more than one row returned by a subquery") with the readable message; works (first row) on SQLite |
| 4 | `uv run pytest -m postgres` with `DMC_TEST_POSTGRES_URL` set | passes |

### TC-E2E-09 CLI parity

Run the scripted sequence of chapter 06 on the project folder created in the UI (set `DMC_WORKING_DB_URL` to the project's `work.db` as `sqlite:///.../work.db`). Expected: the same counts and results as the UI, and the UI's Run history shows the CLI runs.

## 3. Data-handling cases

| ID | Case | Expected |
|---|---|---|
| TC-DATA-01 | CSV with leading zeros in keys | Staged and loaded unchanged (`0001`) |
| TC-DATA-02 | CSV saved in Windows-1252 with `Müller` | Upload detects `cp1252`; S06 shows encoding; data shows `Müller` in S18 and the loaded UTF-8 file |
| TC-DATA-03 | Semicolon-delimited CSV | Delimiter `;` detected; columns split correctly |
| TC-DATA-04 | Tab-delimited `.tsv` / `.txt` | Accepted as CSV with tab delimiter |
| TC-DATA-05 | Excel with title text, header on row 1, blank trailing header cells | Read; trailing blanks ignored |
| TC-DATA-06 | Excel with a blank header cell between columns | Error "a column in the header row has no name" at upload |
| TC-DATA-07 | Excel with whole numbers (`5`) and dates | Whole numbers as `5` (no `.0`), dates as ISO text |
| TC-DATA-08 | A CSV with a column named `_dmc_row_no` | Extract error "reserved column name(s): _dmc_row_no" |
| TC-DATA-09 | CSV with only a header row | Staged with 0 rows; transform gives 0 rows; checks pass trivially; recon COUNT 0 = 0 |
| TC-DATA-10 | Parquet file | Native types kept in staging |
| TC-DATA-11 | A source file removed from `data/` after the object was created | Project page shows "has problems" (file not found); Files editor can repair |
| TC-DATA-12 | A source with 200,000 rows | Extract/Transform complete (see non-functional) |
| TC-DATA-13 | Empty cells | Empty cells in CSV are NULL; mandatory check flags them; profile counts them as empty |
| TC-DATA-14 | A value with a quote or comma inside quotes | Parsed correctly; XREF keys with quotes work (quotes doubled in SQL) |

## 4. Non-functional and security cases

| ID | Case | Expected |
|---|---|---|
| TC-NFR-01 | Extract + transform a 200,000-row CSV | Completes without running out of memory (chunks of 10,000); note the time |
| TC-NFR-02 | Run two UI operations at once (two browser tabs) | They are serialised (the second waits) |
| TC-NFR-03 | Stop the UI during a step, restart | Run is left `RUNNING`; re-running works; tables are recreated |
| TC-NFR-04 | Re-run every step twice | Same results (idempotent) |
| TC-NFR-05 | Open `/` from another computer (host header not localhost) | 403 "This tool only answers on localhost." (the server also binds to 127.0.0.1) |
| TC-SEC-01..05 | see chapter 03c S23 | |
| TC-SEC-06 | A CSV value `=1+1` or `<script>alert(1)</script>` | Appears as text in the Excel/HTML reports and in the UI (escaped), never executed |
| TC-SEC-07 | XML template with `<!DOCTYPE` | Refused: "That XML file contains a DTD, which is not accepted." |
| TC-SEC-08 | Zip template with > 600 members | Refused: "The zip file contains too many files." |
| TC-SEC-09 | Database URL | Never appears in any project file; only the environment variable name |
| TC-SEC-10 | Upload a file > 200 MB as a source | Refused "The file is larger than 200 MB." |

## 5. Automated tests

`uv run pytest` runs **330+ tests** in about a minute (SQLite; the PostgreSQL test is skipped unless `DMC_TEST_POSTGRES_URL` is set). Run `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` too; CI does all four on every push.

| Test file | Covers |
|---|---|
| `test_config_models.py`, `test_loader.py` | model validation, YAML errors, cross-file validation |
| `test_connectors.py` | URL from environment variable |
| `test_extract.py` | readers (CSV encodings/delimiters, Excel quirks, Parquet), staging, reserved/duplicate columns, mapping/join column validation |
| `test_transform.py`, `test_unmapped.py` | rule SQL (golden files), XREF policies, NOT_MIGRATED, UNMAPPED, custom SQL |
| `test_joins.py`, `test_ui_joins.py` | joins: SQL, duplicate-key safety, inner/left, `first`, extract validation, UI add/remove |
| `test_unions.py` | row sets: SQL, transform result, preview labels, recon COUNT, validation, grid saving, SQL rule templating and readable errors, Excel spec with row sets |
| `test_ui_preview.py` | preview results, flags, XREF outcomes, no target table created, repeated join key |
| `test_mapping_sheet.py` | Excel spec export/import, problems, CSV import, notes/review |
| `test_checks.py`, `test_checks_confirmed.py`, `test_dq.py` | all check types, blocking rules, opt-in behaviour, rejects, DQ checks |
| `test_load.py` | file/table loads, atomicity, blocking, include_lineage |
| `test_recon.py` | COUNT/KEYS/TOTALS/FIELDS, exclusions, tolerance, severities |
| `test_report.py`, `test_runlog.py` | collect/Excel/HTML/JSON, escaping, run log |
| `test_proposals.py`, `test_ui_proposals.py` | every proposal kind, dry-run impact, never-applied guarantee, apply/refuse/dismiss |
| `test_targets.py`, `test_ui_targets.py` | target structure parsing (Cockpit XML/zip, Excel, CSV, paste), merge, blank templates |
| `test_ui.py` | UI routes: projects, objects, sources, mapping save, XREF, checks, loads, files, downloads, security headers |
| `test_cli.py`, `test_demo.py` | CLI commands and exit codes, the demo with and without defects |
| `test_transform_postgres.py` | PostgreSQL parity (marker `postgres`) |
| `tests/golden/*.sql` | golden SQL for the supplier general structure and the join |

**How manual and automated testing relate**: the automated tests protect behaviour at the engine and route level (they use the PlainTextClient/HTTP client, not a real browser). Manual screen testing is still needed for the **look and the JavaScript behaviour** of the browser pages: bulk tools, sorting, filters, type-ahead lists, show/hide of rule settings, confirmation dialogs and the busy buttons (the JS lives in `src/data_migration_concept/ui/static/app.js`; there are no automated browser tests yet).
