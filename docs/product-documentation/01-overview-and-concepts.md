# 01 - Overview and concepts

## 1. Purpose and scope

Data_Migration_Concept is a **workbench for moving data from legacy sources to a target layout** and proving that the move was correct. It was designed engine-first: the library and the `dmc` command line are the product; the web UI is a convenient, local front end on the same engine.

**What it does**

- Reads CSV, Excel and Parquet files and any SQL database that SQLAlchemy can reach.
- Lets you define the **target structure** (fields, type, length, mandatory, key) *before* mapping - from an SAP Migration Cockpit template, an Excel/CSV list, a pasted list, or by copying the source columns.
- Maps every target field with a **rule** (move, constant, value-list translation, concatenation, substring, SQL, not migrated, not mapped yet); joins several source tables into one structure; adds more rows with UNION ALL.
- Generates readable, inspectable **ANSI SQL** (works on SQLite and PostgreSQL; other dialects through SQLGlot, best effort) and builds target tables from it.
- Validates the result with **checks**, writes it to files/tables only if no blocking check fails, and **reconciles** source against target.
- Profiles columns and writes **reports** (Excel, HTML, JSON).
- **Proposes** rules, lists, checks, reconciliations and loads from the target structure, a built-in library and the data itself - and applies a proposal only when the user accepts it.

**What it does not do (today)** - see chapter 09 for the full list: no user accounts or roles, no approval workflow, no scheduling, no incremental/delta loads, no direct write to SAP, target columns keep the staged text types, single-user local operation.

## 2. The pipeline

```
 sources (CSV / XLSX / Parquet / SQL)
        |  Extract           -> stg_<object>_<source>      (text-preserving staging tables)
        v
 staging + XREF files
        |  Transform         -> xref_<object>_<name>, tgt_<target_table>
        v
 target tables
        |  Check             -> results + rejected rows in the run log  (only confirmed checks)
        v
        |  Load              -> output files / destination tables      (runs the checks first)
        v
        |  Reconcile         -> results in the run log                 (source vs target)
        |  Profile           -> column profile in the run log
        |  Report            -> reports/<object>_report.xlsx|html|json
```

| Step | Reads | Writes | Fails when |
|---|---|---|---|
| Extract | source files / databases | `stg_<object>_<source>` | file unreadable, column names duplicate or reserved, a mapped/joined column is missing |
| Transform | staging tables, XREF CSV files | `xref_<object>_<name>`, `tgt_<table>` | staging missing, XREF file invalid, XREF value without an entry (policy `error`), join key repeats (policy `error`), database refuses the SQL |
| Check | target tables | run-log rows (`dmc_run_check`, `dmc_reject`) | target tables missing; **blocks the load** when an error-severity check fails or any check cannot run |
| Load | target tables | files in `output/` or database tables | checks block, no destination defined, row count read != written |
| Reconcile | staging + target tables | run-log rows | tables missing; reports differences |
| Profile | staging (or target) tables | `dmc_profile` | tables missing |
| Report | the run log | `reports/` | nothing recorded yet |

## 3. Architecture

| Package (`src/data_migration_concept/`) | Responsibility |
|---|---|
| `config/` | pydantic models (`models.py`) for every YAML file; project loader and cross-file validation (`loader.py`) |
| `connectors/` | builds SQLAlchemy engines from environment-variable URLs (`factory.py`) |
| `extract/` | file/SQL readers (`readers.py`), staging writer (`staging.py`), extract runner with column validation (`runner.py`) |
| `transform/` | SQL generator (`generator.py`), runner (`runner.py`), XREF loader (`xref.py`), preview (`preview.py`) |
| `checks/` | check framework (`base.py`), built-in checks (`builtin.py`), runner (`runner.py`) |
| `recon/` | reconciliation checks (`checks.py`) and runner (`runner.py`); reuses the check result types |
| `load/` | file/table writers (`writers.py`) and the gated load runner (`runner.py`) |
| `profile/` | column profiling (`profile.py`) and runner (`runner.py`) |
| `report/` | collects the report data (`collect.py`), writes Excel / HTML / JSON |
| `runlog/` | run, step, schema, check, reject and profile tables (`log.py`) |
| `proposals/` | pure engine that suggests things (`engine.py`), the built-in library (`catalog.py`, `countries.py`), data classes (`model.py`) |
| `ui/` | local FastAPI app (`app.py`), project workspace (`workspace.py`), step running (`pipeline.py`), validated file editing (`editor.py`), target-structure templates (`targets.py`), proposals glue (`proposals_service.py`), mapping Excel spec (`mapping_sheet.py`), Jinja templates and a little vanilla JS |
| `cli.py`, `demo.py` | the `dmc` command and the demo runner |

Design decisions are recorded as ADRs in `docs/adr/` (0001-0011); chapter 09 lists them.

## 4. Vocabulary

| Term | Meaning |
|---|---|
| **Project** | A folder with `project.yaml` and an `objects/` folder. The unit you keep in git. |
| **Object** | One thing you migrate (supplier, material). One folder under `objects/`. |
| **Source** | Where legacy data comes from: a CSV/Excel/Parquet file or a SQL table/query. An object can have many. |
| **Structure** | One target layout of an object (for example "general data", "bank details"). It is built from one main source (plus joins and row sets) using a mapping, or from custom SQL. It produces one target table. |
| **Target field** | One column of a structure, with description, data type, length, mandatory flag and key flag. |
| **Mapping** | The list of target fields of a structure, each with a **rule**. Stored in `mappings/<structure>.yaml`. |
| **Rule** | How one target field is filled: `MOVE`, `CONSTANT`, `XREF`, `CONCAT`, `SUBSTRING`, `NOT_MIGRATED`, `SQL`, `UNMAPPED`. |
| **XREF** | A value list `legacy_value -> target_value` (country codes, units, payment terms). File `xref/<name>.csv`. |
| **Join** | Another source brought into a structure; its columns are used as `source.COLUMN`. |
| **Row set (UNION ALL)** | An additional set of rows for the same target table, with its own main source, joins and rule overrides. |
| **Check** | A validation rule on a target table. Severity `error` blocks the load; `warn` only reports. |
| **Structure-based rules** (`from_mapping`) | Mandatory, length, data-type and key rules implied by the target structure; each kind must be confirmed (and given a severity) before it runs. |
| **Reconciliation** | A comparison of staged source against the target: counts, keys, control totals, field values. |
| **Load destination** | Where finished target data is written: a file or a database table. |
| **Run / step** | One execution recorded in the run log; a run contains steps (for example `extract:lfa1`, `transform:general`). |
| **Lineage** | Every staged and target row carries `_dmc_row_no` (row number in its source) and `_dmc_run_id`. |
| **Proposal** | A ready-made suggestion (mapping, value list, check, reconciliation, load) that is applied only when the user accepts it. |
| **Flash message** | The one-time message box shown after an action. |

## 5. Data model of the working database

The working database holds **three kinds of data tables** and **six run-log tables**.

### 5.1 Data tables (names come from `project.yaml` prefixes)

| Table | Created by | Content |
|---|---|---|
| `stg_<object>_<source>` | Extract (dropped and recreated on every extract) | The source exactly as read. CSV and Excel values are staged as **text** (leading zeros are kept); Parquet and SQL sources keep native types. Columns: `_dmc_row_no` (1..n), `_dmc_run_id`, then the source columns. |
| `xref_<object>_<name>` | Transform | `legacy_value`, `target_value`, `_dmc_row_no`, `_dmc_run_id`. Recreated on every transform (and by Preview). |
| `tgt_<target_table>` | Transform (dropped and recreated) | One column per target field (except `NOT_MIGRATED` fields), plus `_dmc_row_no`, `_dmc_run_id`. Column types follow the staged types (text for files). |

### 5.2 Run-log tables

| Table | Content |
|---|---|
| `dmc_run` | one row per run: `run_id` (UUID), project, object, status (`RUNNING`/`SUCCESS`/`FAILED`), start/finish time |
| `dmc_run_step` | steps of a run: name (`extract:<source>`, `xref:<name>`, `transform:<structure>`, `checks`, `load:<structure>:<kind>`, `recon`, `profile:<table>`), status, start/finish, `rows_in`, `rows_out`, message |
| `dmc_run_schema` | columns seen per staged table and run |
| `dmc_run_check` | one row per check/reconciliation result: name, type, structure, field, severity, status (`PASS`/`FAIL`/`ERROR`), checked count, failed count, truncated flag, message |
| `dmc_reject` | one row per failing row (capped per check): check name, structure, severity, source row number, field, value, message |
| `dmc_profile` | per table and column: row count, empty count, distinct count, min/max value, min/max length, top values (JSON), top patterns (JSON) |

### 5.3 Files the product creates in a project folder

| Path | Purpose |
|---|---|
| `project.yaml` | project settings and connections |
| `objects/<object>/object.yaml` | sources, structures (joins, unions), loads |
| `objects/<object>/mappings/<structure>.yaml` | field mapping of a structure |
| `objects/<object>/xref/<name>.csv` | value lists |
| `objects/<object>/checks.yaml` / `recon.yaml` | confirmed checks and reconciliations |
| `objects/<object>/sql/*.sql` | custom SQL for structures |
| `objects/<object>/data/*` | uploaded source files |
| `objects/<object>/proposals_dismissed.json` | ids of dismissed proposals (UI only) |
| `build/<object>/<structure>.sql` | the generated SQL written by Transform (CLI and UI) |
| `output/*` | load destinations (files) |
| `reports/<object>_report.xlsx\|html\|json` | reports |
| `work.db` | the SQLite working database used by the UI (unless `DMC_WORKING_DB_URL` is set) |
| `<workspace>/.dmc_projects.json` | list of projects added by path (UI) |
| `.dmc_probe/` | temporary folder used while checking an uploaded file; removed immediately |

## 6. Naming rules

| Thing | Rule |
|---|---|
| Project folder name (UI) | `^[A-Za-z][A-Za-z0-9_-]{0,63}$` |
| Object, source, structure, target table, check, reconciliation, XREF, row set | identifier: `^[A-Za-z][A-Za-z0-9_]*$` |
| Source names `s` and `x1`, `x2`, ... | **reserved** (used as aliases in generated SQL); a joined source cannot use them |
| Row set name `main` | reserved (the structure's own rows) |
| Table-load target table | `schema.table` or `table` (letters, digits, `_`) |
| Uploaded file name | sanitised to letters, digits, `.`, `_`, `-`; up to 100 characters; stored in the object's `data/` folder |
| When an uploaded file's source name already exists | `<name>_<n>` is used |

## 7. Safety model

| Aspect | Behaviour |
|---|---|
| **Validated writes** | Every edit made through the UI is written to the project files, then the whole project is validated. If the change *adds* a problem that was not there before, **all files are restored** and the problem is shown ("Not saved"). So the UI cannot leave a project invalid. |
| **Local only** | The web app answers only for hosts `localhost`, `127.0.0.1`, `[::1]` (and the test host). Other hosts get HTTP 403 "This tool only answers on localhost." The server binds to `127.0.0.1`. |
| **Cross-site requests** | A POST whose `Origin`/`Referer` host differs from the request host is refused with 403 "Cross-site request refused." |
| **Downloads** | Only files below `output/` and `reports/` can be downloaded. HTML reports opened inline are served with a restrictive Content-Security-Policy. |
| **Editable files** | The file editor only opens `project.yaml`, `object.yaml`, `checks.yaml`, `recon.yaml`, mapping YAML, custom SQL and XREF CSV files. |
| **Secrets** | Database URLs are never stored in a project: connections name an **environment variable** (`url_env`). |
| **Untrusted data** | HTML reports escape all values; Excel report text cells are stored as text (no formula injection). XML templates containing a DTD are refused. |
| **One operation at a time** | The UI holds a global lock while a project operation runs (single-user local tool). |
| **Atomic loads** | A file load is written to a temporary file and renamed; a table load is one transaction. A failed load never leaves a half-written destination. |
| **Upload limits** | Source files 200 MB; template files 30 MB; zip templates at most 600 members; template XML/CSV members over 5 MB are ignored. |
| **Proposals** | Never applied without an explicit accept; applied by id re-computed on the server, so a form cannot claim something the engine did not propose. |

## 8. Behavioural rules worth remembering (they drive many test cases)

1. **Target structure before mapping.** A new object starts with a source only and no structure. Proposals, mapping, checks and transform need a structure.
2. **Only confirmed checks run.** With none confirmed, the Check step runs nothing, records nothing, and cannot block a load.
3. **Load always checks first** (confirmed checks only) and cannot skip them.
4. **XREF policy** per field: `error` (default; transform stops and lists up to 20 unmapped values), `default` (use a given value), `blank` (leave empty).
5. **Joins never multiply rows silently.** A join key that repeats stops the transform (policy `error`) or the first row is used (policy `first`).
6. **Unmapped fields stay in the result**, empty (`UNMAPPED`); `NOT_MIGRATED` fields are left out of the target table.
7. **Re-run after changing anything**: the Flow shows an "out of date" badge when an earlier step ran more recently than a later one.
