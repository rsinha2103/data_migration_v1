# User guide

Data_Migration_Concept moves data through a fixed pipeline. You describe each migration object in YAML and SQL files, and the `dmc` command runs the steps and records everything it does.

```
sources -> extract -> staging tables -> transform -> target tables -> check -> load -> files/tables
                                                          \-> recon (source vs target)      \
                                                          \-> profile                        -> report
```

Try it first (no setup, uses SQLite):

```bash
uv run dmc demo                 # clean run: everything passes and loads
uv run dmc demo --dir dmc_demo_bad --defects   # seeded problems: checks block the load
```

Open `dmc_demo/reports/supplier_report.html` to see what a report looks like. The demo project lives in `examples/acme_demo`; all names and numbers in it are invented.

## Using the web interface

```bash
uv run dmc ui            # opens http://localhost:8765
```

Everything in this guide can be done on screen. The pages of an object follow the order of the work:

| Tab | What you do there |
|---|---|
| Proposals | **Ready-made suggestions, never applied for you.** Field mappings, value lists (XREF), checks, reconciliations and load destinations, each with the reason and exactly what would be written. Tick what you want and press Apply selected; Dismiss hides what you do not want. See below. |
| Flow | See the seven steps with status, row counts and "out of date" warnings; run one step or everything; open the report. |
| 1 Sources | Upload CSV / Excel / Parquet files (delimiter and encoding are detected) or add a database table or query. |
| 2 Structures & mapping | **Step 1, define the target structure** - the fields the target system expects (name, description, type, length, mandatory, key). Upload a template, paste a field list, or copy the source columns. **Step 2, map it:** for each target field say how it is filled - move a column, fixed value, XREF translation, concat, substring, not migrated, or SQL. A field you have not mapped yet stays in the result, empty. Mandatory, length, type and key become checks automatically. |
| 3 XREF lists | Create or paste value lists. When data has been extracted, values that have no entry yet are listed so you can add them in one click. |
| 4 Checks | Add uniqueness, allowed values, references, null rate, pattern, range, outlier and SQL checks, as errors (block the load) or warnings. |
| 5 Loads | Choose where the result goes: a file in `output/` or a database table. |
| 6 Reconciliation | Add count, key, total and field comparisons between source and target. |
| Data | Look at the staging, XREF and target tables at any point, with search and paging. |
| Results | Check and reconciliation results, rejected rows with their source row number, and the column profile. |
| SQL | The exact SQL generated for each structure. |

### Proposals

Once an object has a target structure, the **Proposals** tab already holds suggestions. They are only ever *shown*: nothing is written to the project until you tick items and press **Apply selected**. Looking at the tab, or dismissing a suggestion, never changes your mappings, lists, checks or loads.

Where they come from:
- **Your target structure:** mappings by matching name (a source column called like the target field), a count reconciliation per structure, key and field comparisons when keys are marked, control totals for numeric fields, and a default CSV load destination.
- **A built-in library:** value lists for country (ISO 3166, with alpha-3 codes, English names and spellings such as UK), currency (ISO 4217), unit of measure and language, offered when a mapped field looks like one; plus format checks by field name (country and currency codes, e-mail, IBAN, phone numbers, company code). Library lists are a starting point: unit and language targets in particular must be checked against your target system.
- **Your own data** (after Extract): legacy columns recognised by name variants (such as `LAND1` for `COUNTRY`) or by their values (a column that is almost all country codes), the values actually used for a value list (values that are not recognised are left out on purpose, so the transform stops and names them), allowed values for fields with few distinct values, a fixed layout shared by every value, fields that are always filled, non-negative numbers, and an uniqueness check for a column whose values are all different.

**Every proposed check is test-run on your staged data first.** If the data already breaks it (for example a country-format check when some rows hold "Germany"), a red "Heads-up" shows how many rows would be flagged and which values, and the check is proposed as a *warning* so it cannot block your load; change it to an error once the data is clean. The top of the tab also lists **mandatory fields that are not mapped yet**, because those fail the automatic mandatory check and block the load until you map them, give them a fixed value, or clear their mandatory flag.

Each suggestion says why it is made, how sure it is (high or medium), and what it would add ("Show exactly what would be written"). Several similar suggestions are grouped, for example one phone-number format check for nine fields. Applying a value list also switches the fields it covers from MOVE to XREF in one validated step; a suggestion that would break the project is refused and reported while the others still go through. The server re-creates every proposal when you apply, so only suggestions it really made can be applied. Applied suggestions disappear from the tab; dismissed ones can be brought back.

### Defining the target structure

An object starts with its source only. Open **2 Structures & mapping** and choose a way in:

- **A. Upload a template file.** Supported: the SAP Migration Cockpit template as Excel XML (`.xml`, read from its "Field List" sheet, which carries description, type, length and mandatory flag), the zipped CSV templates (`.zip`, one CSV per structure; `(k)` marks key fields and `(*)` mandatory ones), the Excel version of the Cockpit template, or your own `.xlsx` / `.csv`. For your own files a column picker lets you say which column is the field name, description, type, length, mandatory flag, key flag and (when one file lists several structures) the structure. A file can hold many structures; a preview lets you tick the ones to create (structures the file marks as mandatory are ticked for you) and name them.
- **B. Type or paste the fields.** One field per line: name, then optionally description, type, length and X for mandatory. Pasting from Excel works.
- **C. Copy the source columns.** A quick start when there is no template.

A **blank template** to fill in is available as Excel or CSV on the same page (one sheet can list several structures). Types are recognised in common spellings (Text/CHAR, Number/INT, Decimal/DEC/CURR/QUAN, Date/DATS, Boolean); anything else becomes text.

Key fields (`key`) get an automatic uniqueness check on their combination. To combine two files, define the structure from one and use **Update the target structure** on the mapping page with the other: new fields are added, description, type, length, mandatory and key are updated, and your mapping rules stay. For the Cockpit, the XML has the descriptions, types and lengths, while the zip marks every key field.

On the mapping page, **Auto-map by matching names** sets every not-yet-mapped field whose name equals a source column (ignoring case, spaces and `_`) to MOVE, and the progress box shows how many fields and mandatory fields are still unmapped. Large structures can be filtered by name, "only not mapped", "only mandatory" and review state, and sorted by name or "not mapped first".

#### Several source tables for one structure
When a structure needs data from more than one table (for example addresses next to the general data), add the tables as sources of the object, then use **Joined sources** on the mapping page: choose the table and the key columns (main column = joined column). In rules, write its columns as `table.COLUMN`; the structure's own source needs no prefix. A *left* join keeps every row of the main source; an *inner* join only the matching ones. If the joined table repeats a key, the transform stops and names the keys, or - if you chose "first row" - uses the first one. In `object.yaml`:

```yaml
structures:
  - name: general
    source: lfa1
    joins:
      - source: addr
        type: left            # or inner
        on_duplicates: error  # or first
        keys: [{main: LIFNR, other: LIFNR}]
    mapping: mappings/general.yaml
```

#### Several sets of rows for one structure (UNION ALL)
To put more rows below the main ones - for example one vendor row per company code (`BP_ROLE = FLVN00`, from LFB1) *and* one per purchasing organisation (`BP_ROLE = FLVN01`, from LFM1) - add a **row set** in the **Row sets (UNION ALL)** card. Each row set has its own main source and optional join (here LFM1 joined with LFA1 on LIFNR) and **uses the structure's mapping**; list only the fields that differ (`BP_ROLE`, the organisation column) under "Set own rule". The target table holds the rows of all row sets; Preview shows them labelled, and the row-count reconciliation adds up every row set's source rows. A field must be `NOT_MIGRATED` in all row sets or in none. Key/value reconciliations and the bulk tools and Excel specification work on the main rows; rules of a row set are edited in its card. In `object.yaml`:

```yaml
structures:
  - name: vendor
    source: lfb1
    joins: [{source: lfa1, keys: [{main: LIFNR, other: LIFNR}]}]
    unions:
      - name: FLVN01
        source: lfm1
        joins: [{source: lfa1, keys: [{main: LIFNR, other: LIFNR}]}]
        overrides:
          BP_ROLE: {type: CONSTANT, value: FLVN01}
          ORG:     {type: MOVE, source_field: EKORG}
    mapping: mappings/vendor.yaml
```

Use custom SQL instead when the rows need logic a mapping cannot express.

#### Mapping many fields quickly
- **Bulk tools**: tick fields in the first column, then *Set this rule* (one rule for all of them), *Copy first rule to the rest*, or *Set review*. Nothing is saved until **Save mapping**.
- **Import from Excel/CSV**: *Import back* on the mapping page reads a table with the columns Target field, Rule and Source (plus Setting 1-3, Note, Review). It shows every difference first; you confirm before anything is written.
- **Preview the result** shows the first rows as Transform would build them, the source values under each result, mandatory/too-long problems, and the values an XREF list does not cover - before you run anything.

#### Mapping specification for business review
**Download Excel** on the mapping page gives one row per target field with the rule in plain words. Reviewers may change Rule, Source, Setting 1-3, Note and Review (open / approved / changes needed) and send the file back; **Import back** shows the differences and applies them after confirmation. The progress box counts approved fields.

With row sets (UNION ALL) the workbook has one sheet per row set, named `Rows <name>`, next to `Mapping`. Each lists every field with the rule it uses in that row set ("own rule" or "as main"); `How to read` says which source and joins the row set uses. Importing compares each sheet with its own rules: a changed rule on a row-set sheet becomes that row set's own rule, a rule set back to the main one removes the override, and a change on `Mapping` is followed by every row-set field that has no own rule. Notes and review state exist on the `Mapping` sheet only. Everything is applied in one step after you confirm.

Every change you make is checked against the whole project before it is kept; if it would break something, the file is left as it was and you are told why. Saving from a form rewrites that YAML file without its comments; the **Files** page lets you edit the raw YAML, SQL and XREF files instead. The working database is a SQLite file in the project folder unless you set the environment variable named in `project.yaml` (for example to a PostgreSQL URL) before starting `dmc ui`. The interface only answers on localhost and runs one operation at a time.

## 1. Concepts

| Term | Meaning |
|---|---|
| Project | A folder with `project.yaml` and an `objects/` folder. Keep it in git. |
| Object | One thing you migrate (supplier, material). One folder under `objects/`. |
| Source | Where the legacy data comes from: a CSV, Excel or Parquet file, or a SQL table/query. |
| Structure | A target layout (for example "general data" or "bank details") built from one source. |
| Working database | The database where staging, XREF and target tables live. Any SQLAlchemy URL; SQLite is fine for trying things, PostgreSQL is recommended for real work. |
| XREF | A value mapping file (`legacy_value,target_value`), for example country or unit of measure. |
| Check | A validation or data-quality rule on a target table. |
| Reconciliation | A comparison of the staged source with the target table. |
| Run | One execution of one command. Every run, step, check result and rejected row is stored in the working database. |

Table names: staging `stg_<object>_<source>`, XREF `xref_<object>_<name>`, target `tgt_<target_table>`. Every staged and target row carries `_dmc_row_no` (its row number in the source) and `_dmc_run_id`, so a problem can be traced back to the source row.

## 2. Project layout

```
my_project/
  project.yaml
  objects/
    supplier/
      object.yaml          # sources, structures, loads
      mappings/general.yaml
      xref/country.csv
      sql/                 # optional custom SQL
      checks.yaml          # optional
      recon.yaml           # optional
      data/                # your source files
```

### project.yaml

```yaml
name: Acme Global - S/4 Transformation
working_connection: working
connections:
  working:
    url_env: DMC_WORKING_DB_URL     # the URL is read from this environment variable
  legacy:
    url_env: LEGACY_DB_URL
```

Secrets never go in YAML. Set the variables in your shell or a `.env` file (see `.env.example`), for example `DMC_WORKING_DB_URL=postgresql+psycopg://user:password@localhost:5432/dmc` or `sqlite:///work.db`.

### object.yaml

```yaml
name: supplier
sources:
  - kind: file                    # csv | xlsx | parquet
    name: lfa1
    path: data/lfa1.csv           # relative to the object folder
    format: csv
    delimiter: ","                # csv only
    encoding: utf-8               # csv only, e.g. cp1252
  - kind: file
    name: lfbk
    path: data/lfbk.xlsx
    format: xlsx
    sheet: LFBK                   # default: first sheet
  - kind: sql
    name: vendors
    connection: legacy            # a connection from project.yaml
    table: LFA1                   # or: query: "select ... from ..."
structures:
  - name: general
    target_table: bp_general      # becomes tgt_bp_general
    source: lfa1
    mapping: mappings/general.yaml     # or: custom_sql: sql/general.sql
loads:
  - kind: file                    # csv | xlsx | parquet
    structure: general
    path: output/supplier_general.csv  # relative to the project folder
    format: csv
  - kind: table                   # a database table
    structure: general
    connection: working
    table: bp_out
    mode: replace                 # or append
```

CSV and Excel values are staged as text so keys keep their leading zeros. Parquet and SQL sources keep their native types.

## 3. Mapping rules

`mappings/<structure>.yaml` lists the target fields:

```yaml
fields:
  - target_field: PARTNER
    description: Business partner number
    mandatory: true               # derived "mandatory" check
    length: 10                    # derived "length" check
    data_type: string             # string | integer | decimal | date | boolean (derived data-type check)
    rule: { type: MOVE, source_field: LIFNR }
```

| Rule | Meaning |
|---|---|
| `MOVE` | Copy `source_field`. |
| `CONSTANT` | Fixed `value` (text, number, true/false or null). |
| `XREF` | Look up `source_field` in `xref/<xref>.csv`. `on_unmapped`: `error` (default, the transform fails and lists the values), `default` (use `default:`) or `blank`. |
| `CONCAT` | Join `source_fields` (at least two) with `separator`; empty values count as empty text. |
| `SUBSTRING` | `length` characters of `source_field` from `start` (1-based). |
| `NOT_MIGRATED` | Documented but left out of the target table. |
| `UNMAPPED` | The target field exists (it came from the target structure) but has no source yet. It stays in the target table and load file, empty. A mandatory field left unmapped fails the mandatory check. |
| `SQL` | A free SQL `expression` per row; alias `s` is the staging table. |

Anything more complex is a **custom SQL** structure: set `custom_sql: sql/my_structure.sql` instead of `mapping`. The file is one `SELECT` that must return `_dmc_row_no` and `_dmc_run_id` if you want row-level tracing. It is a template: `{{ stg.<source> }}`, `{{ xref.<name> }}` and `{{ tgt.<structure> }}` give table names.

The SQL generated for mappings is plain ANSI that runs on SQLite and PostgreSQL. SQL written by hand (`SQL` rules, custom SQL, `SQL_ASSERT`) is used as written, so it must suit your working database. See the generated SQL any time with `dmc show-sql`.

## 4. Checks (`checks.yaml`)

**Only checks you have confirmed run.** Nothing is checked automatically, and with nothing confirmed the Check step runs nothing and the load is never held back.

The rules your target structure implies (mandatory flags, lengths, data types, keys) are confirmed per kind, each as `error` (blocks the load) or `warn` (reports only):

```yaml
from_mapping:
  mandatory: error
  length: warn
  data_type: warn
  key: error
```

In the web interface this is the "Rules from your target structure" card on the Checks tab, or a proposal on the Proposals tab. Add your own checks under `checks:`. Every check has `name`, `structure`, `severity` (`error` blocks the load, `warn` is reported only) and an optional `description`.

| Type | Settings |
|---|---|
| `UNIQUE` | `fields: [A, B]` |
| `ALLOWED_VALUES` | `field`, `values: [...]` |
| `REFERENTIAL` | `field`, `ref_structure`, `ref_field`, optional `ref_object` |
| `NULL_RATE` | `field`, `max_pct` |
| `PATTERN` | `field`, `pattern` (a regular expression that must match the whole value) |
| `RANGE` | `field`, `min` and/or `max` |
| `OUTLIER` | `field`, `max_zscore` (default 4) |
| `SQL_ASSERT` | `sql`: a query returning the offending rows |

A check that cannot run (for example a missing column) shows up as `ERROR` and also blocks the load. Failing rows are stored with their source row number; use the report to read them.

## 5. Reconciliation (`recon.yaml`)

Compares the staged source with the target table, after the fact.

| Type | Settings |
|---|---|
| `COUNT` | optional `tolerance` (rows) |
| `KEYS` | `source_key: [...]`, `target_key: [...]` |
| `TOTALS` | `source_field`, `target_field`, `agg` (sum, count, count_distinct, min, max), optional `group_by_source`/`group_by_target`, `tolerance` |
| `FIELDS` | `source_key`, `target_key`, `fields: auto` (all MOVE fields) or a list of `{source, target}`, `tolerance`, `trim`, `ignore_case` |

Common settings: `exclude_source` (a SQL condition over alias `s` for rows that are deliberately not migrated, so they are not expected in the target) and `target_table` (compare against another table in the working database, such as an extract of the target system).

## 6. Commands

Every command takes the project folder, and most take an optional object name (default: all objects).

| Command | What it does |
|---|---|
| `dmc validate-config <project>` | Checks all YAML/SQL/XREF files and lists every problem. |
| `dmc extract <project> [object]` | Reads sources into staging tables. Fails if the mapping needs a column the source lacks. |
| `dmc transform <project> [object]` | Loads XREF tables and builds the target tables. Saves the SQL in `build/`. |
| `dmc show-sql <project> [object] [--dialect ...]` | Prints the generated SQL, no database needed. |
| `dmc profile <project> [object] [--stage staging\|target]` | Column statistics: empty %, distinct values, ranges, lengths, common values and shapes. |
| `dmc check <project> [object]` | Runs all checks; exit code 1 if any blocks. |
| `dmc load <project> [object]` | Runs the checks, and only if none blocks writes the `loads:` destinations. |
| `dmc recon <project> [object]` | Runs the reconciliations; exit code 1 on error-severity differences. |
| `dmc report <project> [object] [--run ID] [--format xlsx,html,json] [--out DIR]` | Writes the reports (default folder `<project>/reports`). |
| `dmc demo [--dir DIR] [--defects] [--force]` | Runs the example project end to end. |

A normal sequence is `extract`, `transform`, `check`, `load`, `recon`, `report`; run `profile` after `extract` to look at the source data early.

## 7. Reading a report

- **Overall**: `OK`, `ATTENTION` (only warnings), `BLOCKED` (a check or reconciliation failed at error severity) or `FAILED` (a step failed).
- **Steps**: each stage with rows in and out.
- **Checks / Reconciliation**: one line per rule with its result and failure count.
- **Rejects**: the failing rows (up to `--max-rejects` per check) with source row, field and value. The full list stays in the `dmc_reject` table.
- **Profile**: statistics for every profiled table.

A report shows the newest run of each stage. Use `--run <id>` (run ids are printed by each command) for one specific run.

## 8. Troubleshooting

| Message | What to do |
|---|---|
| `environment variable '...' is not set` | Set the variable named in `project.yaml` (see `.env.example`). |
| `missing source column(s) ...` | The source file does not have a column the mapping reads. Fix the mapping or the file. |
| `no entry in xref '...' for ...` | Add the listed legacy values to the XREF file, or set `on_unmapped` to `default` or `blank`. |
| `staging table ... not found; run extract first` | Run the earlier step first. |
| `... blocking check(s) failed` | Run `dmc check` or `dmc report` and fix the rows listed under Rejects. |
| `file ... leaves the project` | Load paths must stay inside the project folder. |

## 9. Limits

- Staging, target and compared tables must all live in the working database.
- Profiling reads at most 1,000,000 rows per table into memory; reconciliation keeps keys in memory.
- SQLite and PostgreSQL are the tested databases. Others get best-effort SQL translation for generated SQL only.
- Reports show the newest run of each stage rather than one atomic pipeline run.
- Design decisions are recorded in `docs/adr/`.
