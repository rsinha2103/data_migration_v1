# 03a - UI screens: projects, flow, proposals, sources, structures

This chapter and its two companions (03b mapping screens, 03c rules/data screens) describe **every screen of the web interface**: what it is for, every control, what each action does, the exact messages, and test cases to run.

## 0. Common elements

### 0.1 Page frame (every page)

- **Header bar**: product name, **Projects** (home), **How it works**. Inside a project it also shows the project name (link to the project page), **Run history** and **Files**.
- **Flash message** box at the top of the content after an action: green (`ok`) or red (`error`) with a bold title and optional detail lines. It disappears when the page is reloaded without the `?f=` token.
- **Breadcrumbs** `Projects / <project> / <object>` on project and object pages.

### 0.2 Object header (every object page)

Title = object name, then the **tab bar**:

| Tab | Screen | URL |
|---|---|---|
| Flow | S04 | `/p/<project>/o/<object>` |
| Proposals | S05 | `/p/<project>/o/<object>/proposals` |
| 1 Sources | S06 | `.../sources` |
| 2 Structures & mapping | S07, S08, S09-S11 | `.../structures` |
| 3 XREF lists | S12, S13 | `.../xref` |
| 4 Checks | S14 | `.../checks` |
| 5 Loads | S15 | `.../loads` |
| 6 Reconciliation | S16 | `.../recon` |
| Data | S17, S18 | `.../data` |
| Results | S19 | `.../results` |
| SQL | S20 | `.../sql` |

The numbering 1-6 is the intended order of setup work.

### 0.3 Behaviours shared by all forms

- **Busy buttons**: forms that run a step (`data-busy`) disable their buttons and show "Running..." while the step runs.
- **Confirmation dialogs**: destructive buttons (Remove, Delete) ask for confirmation in the browser.
- **Validated write**: any action that changes project files is validated against the whole project; if it would add an error the files are restored and the red message says "... not saved/added" with the reasons.
- **Error pages**: unknown project/object/structure/table -> HTTP 404 page "Not found" with the reason. A project whose files are invalid -> HTTP 422 page "This project has problems" listing each problem (with a link to the Files screen).

### 0.4 Test conventions

"Expected" results are what the product must show. Where a screen has a sample dataset, the test guide (chapter 08) provides it.

---

## S01 Home - `/`

**Purpose**: list projects, create a project, create the demo, add an existing project folder.

**Layout**
1. Heading **Projects**; sub-line `Workspace folder: <path>. New projects are created here.`
2. If no projects: green explanation "Start here. Try the demo project..." with a link to **How it works**.
3. **Project cards** (grid): project folder name (link to S03); either the project title and `N object(s)`, or a red badge **Has problems** plus the first error; the full path.
4. **Try the demo** form: *Folder name* (default `acme_demo`), checkbox *Include seeded data problems (checks will block the load)*, button **Create demo project**.
5. **Create your own project** form: *Folder name*, *Title (optional)*, button **Create project**.
6. **Open a project folder you already have**: *Full path to the folder that contains project.yaml*, button **Add to the list**.

**Actions and results**

| Action | Result |
|---|---|
| Create project, valid name | Project folder with `project.yaml` (connection `working` -> `DMC_WORKING_DB_URL`) and an empty `objects/`. Redirect to S03, green "Project created. Add your first object below." |
| Create project, invalid name | Red "Could not create the project." + "Project names start with a letter and may contain letters, digits, '_' and '-' (up to 64 characters)." |
| Create project, name exists | Red "... A project called 'x' already exists." |
| Create demo | Copies `examples/acme_demo` into the workspace (with seeded defects if ticked). Redirect to S03, green "Demo project created. Open an object and press 'Run everything'." |
| Add existing, folder has no `project.yaml` | Red "Could not add the project." + "That folder has no project.yaml." |
| Add existing, already listed | Red "... That project is already listed." |
| Add existing, ok | Project is remembered in `<workspace>/.dmc_projects.json`; green "Project added." |

**Notes**: surrounding quotes in the pasted path are removed; `~` is expanded. Projects are listed from sub-folders of the workspace that contain `project.yaml`, plus the added ones.

**Test cases**

| ID | Steps | Expected |
|---|---|---|
| TC-S01-01 | Open `/` with an empty workspace | Heading Projects; green "Start here" box; three forms |
| TC-S01-02 | Create project `my_mig` (title empty) | S03 opens, title shows `my_mig`; green "Project created..." |
| TC-S01-03 | Create project `1bad` | Red message about project names; no folder created |
| TC-S01-04 | Create project `my_mig` again | Red "A project called 'my_mig' already exists." |
| TC-S01-05 | Create demo `acme_demo` | S03 with objects `material` and `supplier` |
| TC-S01-06 | Create demo with defects ticked | Same, but data files contain the seeded defects (verified later: checks block the load) |
| TC-S01-07 | Add an existing folder without `project.yaml` | Red "That folder has no project.yaml." |
| TC-S01-08 | Add a valid folder, then reopen `/` | Card with the folder name; adding again says "already listed" |
| TC-S01-09 | Break a project (edit `project.yaml` to invalid YAML), reopen `/` | Its card shows **Has problems** and the first error |

---

## S02 How it works - `/how-it-works`

**Purpose**: static explanation. Shows the six steps (extract ... profile, descriptions generated from the same text the Flow uses, each with *Reads* and *Writes*), then **Report**, then "The ideas behind it" (project, object, structure, source, mapping, XREF, checks, reconciliation, row numbers) and "A typical session" (5 steps). Ends with "All data and settings stay on this computer. Pages and files are only reachable from localhost."

**Test cases**: TC-S02-01 page opens from the header link and shows 7 step boxes; TC-S02-02 the Reads/Writes texts match S04's step cards.

---

## S03 Project page - `/p/<project>`

**Purpose**: overview of one project; add an object.

**Layout**
1. Breadcrumb, project title (`name` from `project.yaml`).
2. Sub-line **Working database: ...** (SQLite file path or "environment variable DMC_WORKING_DB_URL"). For projects added by path, a **Remove from list** button (confirms "Remove from the list? The folder is kept.").
3. **Objects** grid: one card per object with name (link to S04), description, and six status badges: `extract`, `transform`, `checks`, `load`, `recon`, `profile`. Badge colours: OK (green), FAILED (red), NOT RUN (grey), STALE (amber = "older than a step before it"). Hovering a badge shows `<step>: <status>`.
4. **Add an object** form: *Object name* (letters, digits, `_`), *Data file* (.csv, .xlsx, .parquet), *Excel sheet* (optional), **Create object**.

**Create object - what happens**
- The file is **probed first** in a temporary folder (`.dmc_probe`) to be sure it can be read; nothing is written to the project if it cannot.
- The file is copied to `objects/<name>/data/`. A source is created named after the file (lower-case identifier of the stem). CSV delimiter (`,` `;` tab `|`) and encoding (utf-8, utf-8-sig, cp1252) are detected. For Excel, the optional sheet is stored.
- `object.yaml` contains the source and **no structures** ("target structure first").
- Redirect to **S07** with the green message "Object created with its source. Next, define the target structure: upload a template, paste a field list, or copy the source columns."

**Messages**

| Situation | Message |
|---|---|
| bad object name | "Object names use letters, digits and '_' and start with a letter." |
| object exists | "An object called 'x' already exists." |
| unsupported extension | "Upload a .csv, .txt, .tsv, .xlsx or .parquet file." |
| file > 200 MB | "The file is larger than 200 MB." |
| unreadable file (empty CSV, bad Excel, missing sheet) | "Could not read '...'" with the reader's reason |

**Test cases**

| ID | Steps | Expected |
|---|---|---|
| TC-S03-01 | Open an existing project | Title, working database line, object cards |
| TC-S03-02 | Create object `supplier` from `lfa1.csv` | Redirect to S07; Sources tab lists `lfa1`; no structure yet |
| TC-S03-03 | Create object with a `;`-delimited CSV | Source shows delimiter `;` on S06 |
| TC-S03-04 | Create object from an Excel file with a sheet name | Source shows `sheet <name>` |
| TC-S03-05 | Create object from a `.pdf` | Red "Upload a .csv, ..." ; no folder created |
| TC-S03-06 | Create object `supplier` twice | Red "already exists" |
| TC-S03-07 | Create object from an empty CSV | Red "Could not read ..." ; `objects/<name>` not created |
| TC-S03-08 | Run steps on an object, return to S03 | Badges reflect the latest status; after re-running extract, transform badge turns STALE |
| TC-S03-09 | Project added by path: click Remove from list, confirm | Back on S01, green "Removed from the list. The folder was not deleted."; folder still exists |

---

## S04 Object flow - `/p/<project>/o/<object>`

**Purpose**: run the pipeline and see where things stand.

**Layout (top to bottom)**
1. **Run everything** card: button runs `extract, transform, check, load, recon, profile, report` in that order. Extract or transform failing stops the sequence (later steps cannot work); later problems do not stop it.
2. Hint "Not sure what to add? **Proposals** ..." (link to S05).
3. **Six step cards** with arrows: `1. extract`, `2. transform`, `3. checks`, `4. load`, `5. recon`, `6. profile`. Each card: status badge (**OK**, **FAILED**, **NOT RUN**), badge **out of date** when an earlier step ran more recently, description, **Reads:** / **Writes:**, `rows in X -> out Y`, last error message (red), `last run <time>`, button **Run <step>**.
4. **Write report** card: button builds Excel/HTML/JSON reports from the latest result of each step. When a report exists it shows links **Open HTML report**, **Excel**, **JSON**.
5. **Where things stand: <OVERALL>** with three cards: **Checks** (`n of m passed`, blocking, warnings; link "See results & rejected rows"), **Reconciliation** (`n of m agree`, `k differ`), **Failed steps**. If checks block, a box **Why the load is blocked** lists each failing error-severity check with a link to its rejected rows.
6. **Setup at a glance**: **Sources**, **Structures** (link to each mapping, `<- source, table <name>`), **Load destinations** (with *download* link for file loads, or "none yet - add one before loading").

**Results of a run** are shown as one flash message listing, per step, `OK - <step>: <summary>` or `PROBLEM - <step>: <summary>` and up to 12 detail lines each, e.g. `OK - extract: Staged 2 source(s).`, `lfa1 ...: 12 rows, 5 columns`.

**Step outcome texts**

| Step | Success summary | Problem summary |
|---|---|---|
| extract | "Staged N source(s)." + one line `stg_x: R rows, C columns` per source | "Extract failed." + reason |
| transform | "Built N target table(s)." + `tgt_x: R rows from S staged` | "Transform failed." + reason |
| check | "A of B checks passed." / with blocking: "...; K blocking the load." + `BLOCKING: name - message`; none confirmed: "No checks are confirmed yet, so nothing was run and nothing can block the load." | "Check failed." (e.g. target tables missing) |
| load | "Loaded N destination(s)." + `structure -> destination: R rows` | blocked: "Nothing was loaded: checks are blocking the load." + failing checks; otherwise "Load failed." |
| recon | "A of B reconciliations agree." | "...; K differ." + differences |
| profile | "Profiled N table(s)." + `table: C columns` | "Profile failed." |
| report | "Report written: overall status X." + file names | "No report yet." "... Run some steps first." |

**Overall status** (report): `BLOCKED` (an error-severity check/reconciliation failed or could not run), `FAILED` (a step other than checks/recon failed), `ATTENTION` (only warnings), `OK`.

**"Out of date" rule**: a card is marked out of date (hover the badge for the reason) when
1. an earlier step among extract, transform, checks started after its own last run (transform/checks/load/recon cards), **or**
2. a file it depends on was **changed after it ran**: extract - `object.yaml`, `data/*`; transform - `object.yaml`, `mappings/*`, `xref/*`, `sql/*`; checks - `object.yaml`, `mappings/*`, `checks.yaml`; load - those of transform plus `checks.yaml`; recon - `object.yaml`, `mappings/*`, `recon.yaml`.

This matters because **Load only writes the target table that Transform last built**: if you change a mapping and press Load without Transform, the file contains the old result (for example a field you have just mapped is still empty). Always re-run Transform first when the Flow shows it out of date.

**Test cases**

| ID | Steps | Expected |
|---|---|---|
| TC-S04-01 | Fresh object (source only, no structure), press **Run extract** | OK; staging table created; transform card still NOT RUN |
| TC-S04-02 | Press **Run transform** with no structure | Red PROBLEM: "object '<name>' has no target structure yet; define one before transforming" |
| TC-S04-03 | Press **Run transform** before extract | Red: "staging table 'stg_...' not found; run extract first" |
| TC-S04-04 | Run checks with no checks confirmed | Green: "No checks are confirmed yet, so nothing was run..." ; checks card stays NOT RUN; no run-log step |
| TC-S04-05 | Run load with no destination | Load button is disabled on S15; on S04 a PROBLEM "object '<name>' has no 'loads' defined in object.yaml" |
| TC-S04-06 | **Run everything** on the demo project | 7 lines OK/PROBLEM; badges update; HTML/Excel/JSON links appear |
| TC-S04-07 | Demo with seeded defects, Run everything | Checks: blocking; load: "Nothing was loaded: checks are blocking the load."; overall BLOCKED; "Why the load is blocked" lists the checks |
| TC-S04-08 | Run extract again after transform | Transform/checks/load/recon cards show **out of date** |
| TC-S04-12 | Run extract, transform and load; then change a field's rule on S09 and Save | Transform and load cards show **out of date**, hover text "mappings/<structure>.yaml was changed after this step ran." Pressing Load now writes the **old** result (field still empty); after Run transform and Run load the file has the new value |
| TC-S04-13 | Upload another source file after Extract | Extract card shows out of date |
| TC-S04-09 | Click a check name in "Why the load is blocked" | S19 filtered to that check's rejected rows |
| TC-S04-10 | **Write report** then open HTML report | Report page with Steps, Checks, Reconciliation, Rejects (sample), Profile |
| TC-S04-11 | Download Excel link | File `<object>_report.xlsx` downloads |

---

## S05 Proposals - `/p/<project>/o/<object>/proposals`

**Purpose**: show ready-made suggestions. **Nothing here is applied until accepted.** Reference of every proposal kind: chapter 07.

**Layout**
1. Explanation box ("Proposals are suggestions, already written for you - nothing here has been applied...").
2. **Red box "Mandatory fields that are still not mapped"**: per structure the unmapped mandatory fields (first 12, then "..."), each structure linking to its mapping.
3. If there is no structure: "Proposals need a target structure. Define one first." If a structure exists but nothing is extracted: green "Run Extract to get proposals based on your data. Until then you see the ones that need no data."
4. A form with **Apply selected** and **Dismiss selected** buttons and the count. Proposals grouped by kind with a group heading and *select all in this group*: **Field mappings**, **Value lists (XREF)**, **Checks**, **Reconciliations**, **Load destinations**.
5. Each proposal card: checkbox, bold title, confidence badge (`high`/`medium` confidence), origin badge (`target structure`, `library`, `your data`), structure name, the reason, `Would add: <summary>`, notes (a note starting with "Heads-up" is shown as a red warning), and for lists/checks a collapsible **Show the list (N entries)** / **Show exactly what would be written** with the JSON.
6. **Dismissed (N)** section: collapsible list with checkboxes and **Bring back selected**, plus **Bring back all dismissed**.

**Actions**

| Action | Result |
|---|---|
| Apply selected, nothing ticked | Red "Nothing was selected." |
| Apply selected | The engine **re-creates** the proposals on the server and applies those whose ids were ticked (so a tampered form cannot apply anything else). Flash: `N proposal(s) applied. Re-run Transform, then Check, to see their effect.` with lines `applied: <title>`, `skipped: <title> (...)`, `refused: <title> - <reasons>`; red `"N applied, M refused."` when any refused. Each proposal is validated against the whole project; one that would break the project is refused and rolled back. |
| Apply a proposal that no longer exists | Red "Those proposals are no longer available." |
| Dismiss selected | Ids stored in `objects/<object>/proposals_dismissed.json`; green "N proposal(s) dismissed. You can bring them back below." |
| Bring back | Green "N proposal(s) brought back." |

**Test cases**

| ID | Steps | Expected |
|---|---|---|
| TC-S05-01 | Object with a structure but no data extracted | Proposals that need no data are shown (target-structure based, library); green "Run Extract..." box |
| TC-S05-02 | Open the page, then compare project files before/after | **No file changed** just by opening the page |
| TC-S05-03 | After Extract, reopen | Additional proposals based on data (value lists, allowed values, formats, ranges, mappings by name) |
| TC-S05-04 | Tick one mapping proposal, **Apply selected** | The field changes from not mapped to MOVE in S09; green message lists `applied:` |
| TC-S05-05 | Apply a value-list proposal | `xref/<name>.csv` created and the fields switch from MOVE to XREF (policy `error`) |
| TC-S05-06 | Apply "Mandatory fields must be filled" | S14 shows Mandatory rule on as the proposed severity |
| TC-S05-07 | Dismiss two proposals, reload | They disappear from the groups and appear under Dismissed; **Bring back all** restores |
| TC-S05-08 | Apply nothing selected | Red "Nothing was selected." |
| TC-S05-09 | A proposed check the data already breaks | Proposed as **warn** with a red "Heads-up" note (never blocks the load by default) |
| TC-S05-10 | Unmapped mandatory field | Listed in the red box; link opens S09 |

---

## S06 Sources - `/p/<project>/o/<object>/sources`

**Purpose**: manage where the object's data comes from.

**Layout**
1. Explanation (files are copied into `data`; values are read as text).
2. **Sources table**: Name, Kind (`file`/`sql`), Where (file path, or `connection: table/query`), Options (file format; CSV delimiter and encoding; Excel sheet), **Remove** button (confirm "Remove source <name>?").
3. **Add a file**: *File*, *Excel sheet (optional)*, **Upload**.
4. **Add a database source**: *Name*, *Connection* (from `project.yaml`), *Table*, *or SQL query*, **Add**. Hint: connections are defined in `project.yaml`; the database URL comes from an environment variable.

**Rules and messages**

| Action | Result |
|---|---|
| Upload file | Copied to `data/`; new source named after the file (`<name>_<n>` when taken); green "Source added. Run Extract to stage it." |
| Upload unsupported/oversize/unreadable | Red "Could not add the source." + reason |
| Add database source with both or neither of table and query | Red "Give either a table name or a query, not both and not neither." |
| Add database source, name invalid / connection unknown | Red with the validation message (e.g. unknown connection) |
| Add database source ok | Green "Database source added." |
| Remove source still used by a structure/join/row set | Red "Could not remove the source." with "unknown source ..." (project validation) - the source stays |
| Remove source ok | Green "Source removed." |

**Notes**: a database source's columns are only known after one Extract (the mapping screens say "Run Extract once so the columns of this database source are known.").

**Test cases**: TC-S06-01 upload a second file -> new source row, green message; TC-S06-02 upload `.docx` -> red; TC-S06-03 add a SQL source with table and query both filled -> red; TC-S06-04 remove a source used by a structure -> red, source remains; TC-S06-05 remove an unused source -> green; TC-S06-06 add a database source for connection `working` with table of the staging DB, Extract -> staging table appears on S17.

---

## S07 Structures (define the target structure) - `/p/<project>/o/<object>/structures`

**Purpose**: define the **target structure** first, then open a structure to map it.

**Layout**
1. Explanation ("Two steps. First define the target structure ... Then open a structure and map each target field...").
2. **Your target structures** table: Structure (link to S09), Fed by source, Fields, Mapped (`n of m`, or "custom SQL"), Mandatory not mapped (amber badge when > 0), Target table (`tgt_<table>`), **Remove** (confirm; removes the structure, its mapping file and its loads).
3. **Three ways to define a structure**:
   - **A. Upload a template file** - SAP Migration Cockpit template (`.xml`, or the `.zip` of CSV files) or your own `.xlsx` / `.csv` list. Several structures in one file are fine. Below it: "No template yet? Download a blank one to fill in: Excel - CSV".
   - **B. Type or paste the fields** - *Structure name* and a text box: one field per line `name`, then optionally description, type, length, mandatory (X); paste from Excel works. Button **Continue**.
   - **C. Copy the source columns** - *Structure name*, *Target table*, *Copy columns of* (a source). Button **Create**; every source column becomes a target field that moves itself.

**Actions and messages**

| Action | Result |
|---|---|
| A or B, readable | Redirect to **S08** (confirmation) |
| A or B, unreadable / empty / wrong type | Red "Could not read the target structure." + reason (e.g. "Choose a file or paste a list of fields.", "That is not a valid zip file.", "That XML file contains a DTD, which is not accepted.", "No fields were found in the 'Field List'.") |
| C ok | Structure + mapping `mappings/<name>.yaml` created; redirect to S09 with green "Structure created by copying the source columns (every field moves its own column)." |
| C, invalid names | Red "Names use letters, digits and '_' and start with a letter." |
| Remove structure | Green "Structure removed (its loads too)." |

**Blank templates** (`/templates/target-structure.xlsx` and `.csv`): header `Structure, Field, Description, Data type, Length, Mandatory, Key` with four EXAMPLE rows; the Excel file has a second sheet "How to fill in" explaining each column (types Text, Number, Decimal, Integer, Date, Boolean; mandatory/key = `X`).

**Test cases**

| ID | Steps | Expected |
|---|---|---|
| TC-S07-01 | Object without structures | Table says "No target structure yet - define one below." |
| TC-S07-02 | Download both blank templates | Excel with 2 sheets; CSV with header and 4 example rows |
| TC-S07-03 | Way C with source `lfa1`, name `general`, table `bp_general` | S09 opens with one MOVE field per source column |
| TC-S07-04 | Way B with pasted lines (tab separated, Excel paste) | S08 shows the detected structure |
| TC-S07-05 | Way A with the blank Excel template filled in | S08 lists the structure(s) in the file |
| TC-S07-06 | Way A with a Cockpit `.xml` | "SAP Migration Cockpit Excel XML template", structures per SAP structure, mandatory/key detected |
| TC-S07-07 | Way A with a Cockpit `.zip` of CSVs | structures from CSV headers; `(k)` = key, `(*)` = mandatory |
| TC-S07-08 | Way A with a `.png` | Red "Could not read the target structure." |
| TC-S07-09 | Remove a structure (confirm) | Row disappears; its mapping file and loads are gone |
| TC-S07-10 | Structure with mandatory fields unmapped | Amber badge with the count in "Mandatory not mapped" |

---

## S08 Confirm the target structure - `/p/<project>/o/<object>/structures/template/<token>`

**Purpose**: check what was found in the uploaded/pasted template before creating anything. The uploaded content is kept in server memory under a short-lived token (the 12 newest uploads are kept; an expired token gives "That upload has expired. Please upload the file again.").

**Layout**
- Title **Check what was found** (or **Update <structure> from a template** when opened from S09). Line `File: <name>` plus a note ("SAP Migration Cockpit CSV templates", "... Excel XML template", ...).
- For plain tables (Excel/CSV/pasted): the card **Which column is which?** with a select per role - *Field name (required)*, *Description*, *Data type*, *Length*, *Mandatory flag*, *Key flag*, *Structure (when one file lists several)* - plus *Excel sheet* (if several) and *Structure name (if no structure column)*; button **Update preview** (re-reads with your choices); a preview of the first 8 rows and a count. Roles are guessed from the headings; a headerless paste is read as name, description, type, length, mandatory.
- If no field column is chosen: red "No structure found yet. Choose the field-name column above and press 'Update preview'."
- **Create mode**: table `N structure(s) found` with, per structure: a tick box (mandatory structures and a single structure are pre-ticked unless the name clashes), *Structure name* (badges "name already used", "mandatory structure"), *Target table*, *Filled from source*, counts of fields / mandatory / key, the first fields. Buttons **Create the selected structures** and **Cancel**.
- **Update mode** (from S09): radio list of the structures in the file, with the best match pre-selected; button **Update the structure**. Explanation: fields are added (unmapped); description, type, length, mandatory and key are updated; mapping rules stay; fields not in the template are kept.

**Type recognition**: Text/CHAR -> string; Number/INT/SMALLINT/BIGINT -> integer; DEC/DECIMAL/CURR/QUAN/FLTP/FLOAT/DOUBLE/NUMBER/NUMERIC/CURRENCY/QUANTITY/REAL -> decimal; DATS/DATE/DATUM/DATETIME/TIMESTAMP/TIME -> date; BOOL/BOOLEAN/FLAG/BIT -> boolean; anything else -> string. **Mandatory** flag values: `x y yes true 1 m mandatory required r j ja *`. In the Cockpit Excel XML: *Type* `Date` -> date, `Number` -> integer (decimal when *Decimal* > 0), *Importance* containing "mandatory" -> mandatory, *Group Name* = "Key" -> key. In Cockpit CSV zips: header `FIELD(k/*)`.

**Actions and messages**

| Action | Result |
|---|---|
| Create selected | Structures and mapping files created in one validated step (all or none); green "N target structure(s) created. Open one to map its fields from the source." |
| Create with nothing ticked | Red "Nothing was created." + "Nothing was selected." |
| Create with a clashing name/table or invalid name | Red "Nothing was created." + reason (e.g. duplicate structure/target_table names) |
| Update the structure | Green "Target structure updated: A field(s) added, U detail(s) updated, K field(s) not in the template (kept)." |
| Duplicate field names in the template | Red "These field names appear more than once: ... Each target field must be listed once." |

**Test cases**

| ID | Steps | Expected |
|---|---|---|
| TC-S08-01 | Paste 4 fields and continue | One structure found, ticked; field/mandatory counts correct |
| TC-S08-02 | Change the role of a column to *(none)* and press Update preview | Preview re-read; counts change |
| TC-S08-03 | Create with a table name already used | "name already used" badge; creating gives red message |
| TC-S08-04 | Upload an Excel with two structures in a *Structure* column | Two rows; tick one; only that one is created |
| TC-S08-05 | Merge: update an existing structure from a newer template with 1 new field and a changed length | Message "1 field(s) added, 1 detail(s) updated, ..."; mapping rules unchanged |
| TC-S08-06 | Wait for token expiry (restart server) and press Create | Red "That upload has expired..." |
| TC-S08-07 | Duplicate field name in the template | Red duplicate-field message; nothing created |
