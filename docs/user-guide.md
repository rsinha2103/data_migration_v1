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
| `SQL` | A free SQL `expression` per row; alias `s` is the staging table. |

Anything more complex is a **custom SQL** structure: set `custom_sql: sql/my_structure.sql` instead of `mapping`. The file is one `SELECT` that must return `_dmc_row_no` and `_dmc_run_id` if you want row-level tracing. It is a template: `{{ stg.<source> }}`, `{{ xref.<name> }}` and `{{ tgt.<structure> }}` give table names.

The SQL generated for mappings is plain ANSI that runs on SQLite and PostgreSQL. SQL written by hand (`SQL` rules, custom SQL, `SQL_ASSERT`) is used as written, so it must suit your working database. See the generated SQL any time with `dmc show-sql`.

## 4. Checks (`checks.yaml`)

Mandatory, length and data-type checks come from the mapping automatically. Add the rest here. Every check has `name`, `structure`, `severity` (`error` blocks the load, `warn` is reported only) and an optional `description`.

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
