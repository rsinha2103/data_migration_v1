# 05 - Engine reference

What each step really does, so testers can predict results and developers can find the code. The UI and the CLI call the same functions.

## 1. Run log and steps

Every UI step and every CLI command starts a **run** (`dmc_run`, status `RUNNING`) and ends it `SUCCESS` or `FAILED`. Inside a run each unit of work is a **step** (`dmc_run_step`) recorded with start/finish, `rows_in`, `rows_out` and a message. Steps are named:

| Step name | Written by |
|---|---|
| `extract:<source>` | Extract (one per source) |
| `xref:<name>` | Transform (one per XREF list used by a mapping or override) |
| `transform:<structure>` | Transform (one per structure) |
| `checks` | Check / the check phase of Load (only when at least one check is confirmed) |
| `load:<structure>:<file\|table>` | Load (one per destination) |
| `recon` | Reconcile |
| `profile:<table>` | Profile (one per table) |

The Flow (S04) takes, for each kind (extract, transform, checks, load, recon, profile), the **newest run containing a step of that kind**; `xref:` steps count as transform. A kind is FAILED if any step of that run failed.

## 2. Extract (`extract/`)

1. For each source of the object, in order, a step `extract:<source>` runs.
2. **Reading**: file sources via `readers.read_file`; SQL sources via `read_sql` (streamed in chunks of 10,000 rows; `table` -> `SELECT *`, `query` -> the statement). CSV: the whole file is decoded with the configured encoding, then parsed with **all columns as text** (no type inference). Excel: first row header; cells converted to text (see chapter 04 section 2.1). Parquet: native types.
3. **Validation before anything is dropped** (`staging.write_staging`, `extract/runner._column_validator`):
   - duplicate column names: `source 'x': duplicate column(s): A, B`
   - reserved names `_dmc_row_no`, `_dmc_run_id`: `source 'x': reserved column name(s): ...`
   - mapping/join consistency: every column that a mapping rule (including row-set overrides), a join key (`main`/`other`) reads from *this* source must exist: `source 'x' does not match the mapping:` + one line per problem, e.g. `structure 'general', field 'PARTNER': missing source column(s) LIFNR`, `structure 'vendor', row set 'FLVN01', join with 'lfa1': missing key column(s) LIFNR`.
4. **Writing**: the staging table `stg_<object>_<source>` is dropped and recreated with `_dmc_row_no` (1..n, in file order), `_dmc_run_id` and the source columns; rows are inserted in chunks; afterwards the stored count must equal the read count (`read N rows but staged M`).
5. Column types: text for CSV/Excel; integers -> BIGINT, floats -> FLOAT, boolean, date, datetime, decimal -> NUMERIC, everything else TEXT.
6. The schema (column names and types) is stored in `dmc_run_schema`.
7. Failure of one source stops the extract of the object (first failure wins).

Other errors: `source 'x': file 'p' not found`; `source 'x': cannot read 'p': <reason>`; `source 'x': sheet 'S' not found`; `source 'x': sheet is empty`; `source 'x': a column in the header row has no name`; `source 'x': query failed: <reason>`; `source 'x': produced no data`; `unknown object 'x' (known: ...)`.

## 3. XREF loading (`transform/xref.py`)

Before building target tables, Transform loads each XREF list that a mapping rule or row-set override uses into `xref_<object>_<name>` (recreated each time). Validation: file exists (`xref 'n': file 'n.csv' not found`), readable, columns `legacy_value`/`target_value` present (`missing column(s) ...`), no empty legacy value (`empty legacy_value found`), no duplicates (`legacy_value mapped more than once: ...`). The same loading happens in the **Preview**.

## 4. Transform (`transform/`)

### 4.1 Flow

1. The object must have at least one structure (`object 'x' has no target structure yet; define one before transforming`).
2. **Generate** SQL for every structure (4.2-4.8); write it to `build/<object>/<structure>.sql` (with a header) unless disabled.
3. Load the XREF lists (section 3).
4. For each structure, in a step `transform:<structure>`:
   - every staging table it reads (main sources of all row sets) must exist: `structure 's': staging table 't' not found; run extract first`
   - joined staging tables must exist (`... of the joined source 'x' not found; run extract first`)
   - **duplicate-key check** for joins with `on_duplicates: error` (4.5)
   - **XREF pre-check** for rules with policy `error` (4.6)
   - run `DROP TABLE IF EXISTS tgt_...` and `CREATE TABLE tgt_... AS SELECT ...` in **one transaction** (an error leaves the previous target table in place)
   - record `rows_in` (sum of the row sets' main source rows) and `rows_out` (target rows).
5. If the database refuses the SQL (typically a SQL rule or custom SQL): `structure 's': the database rejected the generated SQL: <reason>. In SQL rules and custom SQL, name other sources as {{ stg.<source> }} (the real table name is stg_<object>_<source>) and qualify columns with an alias, e.g. a."LIFNR" = s."LIFNR".`

### 4.2 Shape of the generated SQL

Real example (golden file `tests/golden/supplier_join.sql`):

```sql
DROP TABLE IF EXISTS "tgt_bp_general";

CREATE TABLE "tgt_bp_general" AS
SELECT
  s."_dmc_row_no" AS "_dmc_row_no",
  s."_dmc_run_id" AS "_dmc_run_id",
  s."LIFNR" AS "PARTNER",
  'SUPL' AS "BU_GROUP",
  s."NAME1" AS "NAME_ORG1",
  x1."target_value" AS "COUNTRY",
  addr."STRAS" AS "STREET",
  COALESCE(CAST(addr."ORT01" AS TEXT), '') || ', ' || COALESCE(CAST(s."LAND1" AS TEXT), '') AS "CITY",
  SUBSTR(CAST(s."NAME1" AS TEXT), 1, 10) AS "SEARCH_TERM",
  COALESCE(CAST(s."LIFNR" AS TEXT), '') || '-' || COALESCE(CAST(s."NAME1" AS TEXT), '') AS "LEGACY_REF"
FROM "stg_supplier_lfa1" AS s
LEFT JOIN "stg_supplier_addr" AS addr
  ON CAST(s."LIFNR" AS TEXT) = CAST(addr."LIFNR" AS TEXT)
LEFT JOIN "xref_supplier_country" AS x1
  ON CAST(s."LAND1" AS TEXT) = x1."legacy_value";
```

- Identifiers are double-quoted. The two lineage columns are always first.
- Aliases: `s` = main source row, `<source name>` = a joined source, `x1`, `x2`, ... = XREF lookups (numbered per row set).
- The SQL is ANSI and runs unchanged on SQLite and PostgreSQL; for other dialects it is translated with SQLGlot (`dmc show-sql --dialect`).

### 4.3 Column order and typing

Target columns follow the order of the mapping, except `NOT_MIGRATED` fields (omitted). Types are whatever the expression yields: staged text stays text; `CAST(NULL AS TEXT)` for unmapped; the declared `data_type` is **not** cast into the table. (`DATA_TYPE` checks validate the text.) If every field is `NOT_MIGRATED`: `structure 's': every field is NOT_MIGRATED`.

### 4.4 Rule expressions

See chapter 04 section 3.1. Notes: a MOVE of an empty source value gives an empty/`NULL` value as staged; CONCAT never returns NULL (empty parts are empty text); SUBSTRING positions are 1-based (`SUBSTR`).

### 4.5 Joins

`LEFT JOIN`/`INNER JOIN` on `CAST(main AS TEXT) = CAST(other AS TEXT)` for every key pair, AND-ed. With `on_duplicates: first` the joined table is replaced by a sub-select that keeps only the row with the lowest `_dmc_row_no` per key. With `error` (default), **before** building, the runner checks that no (non-null) key repeats in the joined staging table; if one does:

`structure 's': the join with 'src' would multiply rows because its key (COL) is not unique: v1 appears 2 times; ... Fix the data, or set the join to use the first row only.` (up to 5 keys shown).

### 4.6 XREF policies

| Policy | SQL | Pre-check |
|---|---|---|
| `error` | the looked-up value | one query per such field returns up to **20** distinct source values (non-null) without an entry; if any: `structure 's' has unmapped values:` + `field 'F': no entry in xref 'n' for 'v1', 'v2' (first values shown)` (the last text when 20 were found). The target table is not touched. |
| `default` | `COALESCE(x."target_value", 'default')` | none |
| `blank` | the looked-up value (NULL when no entry) | none |

An *empty/NULL* source value never fails the pre-check (only non-null values are tested); its result is NULL (or the default).

### 4.7 Row sets (UNION ALL)

For `unions`, one SELECT is generated **per row set** from the same mapping (each field's rule = the override if present, else the mapping's rule; main joins/source replaced by the row set's) and the SELECTs are combined with `UNION ALL` in the order main, then the row sets in file order, inside the same `CREATE TABLE ... AS`. Lineage `_dmc_row_no` restarts per source, so **it is not unique across row sets**. The runner reads, checks and counts every row set's staging tables (`rows_in` = sum). XREF pre-checks run for every row set.

### 4.8 SQL rules and custom SQL

- **SQL rule** (`expression`): the text is rendered with Jinja (so `{{ stg.lfa1 }}` becomes `stg_<object>_lfa1`) and wrapped in parentheses. Inside it `s` is the current row of the row set's main source, joined sources by their names. Example (lookup in another staged source):

  ```sql
  select a."NAME1" from {{ stg.lfa1 }} a where a."LIFNR" = s."LIFNR"
  ```

  Always **qualify columns** (`a."LIFNR"`, `s."LIFNR"`): an unqualified name binds to the innermost table, so `where lifnr = LIFNR` would compare a column with itself. A scalar sub-select returning several rows takes the first one on SQLite and **fails on PostgreSQL**; prefer a join + MOVE `source.COLUMN`.
- **Custom SQL file**: whole-structure SELECT, see chapter 04 section 7.

### 4.9 Preview (`transform/preview.py`)

`preview_structure` builds the same plan as Transform for each row set, runs `SELECT <expressions + source columns> ... ORDER BY s._dmc_row_no LIMIT n`, flags mandatory-empty and too-long values, and computes the XREF "missing values" per XREF field (grouped, top 10). It never creates or replaces a target table.

## 5. Checks (`checks/`)

### 5.1 Which checks run

`build_checks` = derived checks (from `from_mapping`, per confirmed kind and severity) + explicit checks (`checks:`). **With none, `run_checks` returns an empty list, records nothing, and the step is "not run".**

Derived checks (named `mandatory.<structure>.<field>`, `length.<structure>.<field>`, `datatype.<structure>.<field>`, `key.<structure>`): skip `NOT_MIGRATED` fields; mandatory also applies to unmapped fields (with the hint "the field is mandatory but not mapped yet"); length only for fields with a length; data type only when not `string`; key = UNIQUE over all fields flagged key.

### 5.2 Execution

- All target tables of the object must exist (`target table(s) not found: ...; run transform first`).
- Checks run as **one step** `checks`. For each check: count the rows examined (`checked_count`), stream the offending rows (set-based SQL; type/pattern/range/outlier checks evaluate values in Python on streamed rows), cap stored failures at **10,000** per check (`truncated` flag), build the summary message.
- A check that cannot run (missing column, bad SQL, unknown `ref_object`) gets status **ERROR** with the database/validation reason; ERROR always **blocks**.
- Every result goes to `dmc_run_check`; every failing row to `dmc_reject` (`row_no` = source row number `_dmc_row_no`).
- The step is FAILED if anything blocks. **Blocking** = ERROR, or FAIL with severity `error`.

### 5.3 Semantics of each check

| Check | SQL / logic |
|---|---|
| MANDATORY | `col IS NULL OR TRIM(CAST(col AS TEXT)) = ''` -> "`F` is mandatory but empty" |
| LENGTH | `LENGTH(CAST(col AS TEXT)) > n` -> "`F` is longer than n" |
| DATA_TYPE | non-NULL, non-blank values validated in Python: integer `^[+-]?\d+$`; decimal = finite `Decimal`; date = ISO `YYYY-MM-DD`; boolean `true/false/1/0` (any case) -> "`F` is not a whole number / a number / an ISO date (YYYY-MM-DD) / true/false/1/0" |
| UNIQUE | rows whose field combination occurs > 1 time -> "duplicate key (F=v, G=w)" |
| ALLOWED_VALUES | `col IS NOT NULL AND CAST(col AS TEXT) NOT IN (...)` -> "`F` value is not allowed" |
| REFERENTIAL | `NOT EXISTS` match on `CAST(...) AS TEXT` -> "`F` not found in S.RF" |
| NULL_RATE | one failure when `% empty > max_pct` -> "`F` is empty in 20.0% of rows (allowed 5%)" |
| PATTERN | non-blank values must `fullmatch` -> "`F` does not match <regex>" |
| RANGE | non-blank values: not a number -> "`F` is not a number"; outside -> "`F` is outside the allowed range" |
| OUTLIER | z-score = \|x - mean\| / population-std; needs >= 3 numeric values and std > 0 -> "`F` is an outlier (z-score 3.2)" |
| SQL_ASSERT | rendered SQL; each returned row is a failure "assertion 'name' violated" (row number from `_dmc_row_no` column if present) |

## 6. Load (`load/`)

1. Object must have `loads` (`object 'x' has no 'loads' defined in object.yaml`).
2. **Run the checks** (section 5). If anything blocks -> `LoadBlockedError` (no destination touched). With no confirmed check this is a no-op.
3. For each destination, a step `load:<structure>:<kind>`: read the target table in chunks ordered by `_dmc_row_no` and write it (formats and atomicity: chapter 03c S15). Count check `read N rows but wrote M`.
4. Lineage columns are removed from the output unless `include_lineage`.

## 7. Reconciliation (`recon/`)

- Requires `recon.yaml` (`object 'x' has no recon.yaml`) and the needed tables (`table(s) not found: ...; run extract / transform first`).
- Source side = the structure's **staging table** (main source). `exclude_source` removes rows from the expectation (`(SELECT * FROM stg AS s WHERE CASE WHEN (<cond>) THEN 1 ELSE 0 END = 0) AS s`, Jinja-rendered).
- Results reuse the check types `RECON_COUNT`, `RECON_KEYS`, `RECON_TOTALS`, `RECON_FIELDS` and the same severities/blocking rules; they are stored in `dmc_run_check`/`dmc_reject`.

| Type | Algorithm and messages |
|---|---|
| COUNT | `expected` = source rows after exclusions (+ all rows of other row sets); `actual` = target rows; fails when `abs(actual - expected) > tolerance`: `source has T rows (E intentionally excluded), so X expected; target has A (difference +/-D)` |
| KEYS | key tuples (trimmed text, NULL = empty) of source and target; `key is in the source but missing in the target` / `key is in the target but not in the source` (value `k1\|k2`) |
| TOTALS | aggregate (sum/count/count_distinct/min/max) per group on each side; `sum is X in the source but Y in the target` (`group g: ` prefix); non-numeric values for sum/min/max -> ERROR `non-numeric value 'x' cannot be used for sum` |
| FIELDS | matched on the key; per field pair: numeric values compared with tolerance, otherwise text (trim, optional case-folding); missing target keys are KEYS' business; message `<key>: SRC is 'a' in the source but 'b' in the target` |

## 8. Profile (`profile/`)

Per table (staging by default, or target), per non-lineage column, over the first `max_rows` rows (default 1,000,000; the proposals engine uses 100,000): row count, empty count (NULL or blank), distinct count of non-empty values, min/max value (native for numeric/date columns, otherwise text order), min/max length of the text, **top 5 values** (shown truncated to 50 characters) and **top 5 shapes** (digits -> `9`, capitals -> `A`, lowercase -> `a`, other characters kept). Stored in `dmc_profile`; shown on S19 and in the reports.

## 9. Reports (`report/`)

`collect_report` gathers, for the object, the **newest run of each step kind** (or one explicit run id): steps, check results, reconciliation results, rejects (limited per check; default 100, UI results page 500), profiles. Overall status (`BLOCKED` / `FAILED` / `ATTENTION` / `OK`) as in S04. Files, overwritten on each report: `reports/<object>_report.xlsx|html|json`.

| Format | Content |
|---|---|
| **xlsx** | sheets **Summary**, **Steps**, **Checks**, **Reconciliation**, **Rejects**, **Profile** (text cells stored as text) |
| **html** | one self-contained page: overall badge, summary, Steps, Checks, Reconciliation, Rejects (sample), Profile; all values HTML-escaped |
| **json** | the collected data (`tool`, `generated_at`, `project`, `object`, `overall`, `summary`, `runs`, `steps`, `checks`, `reconciliations`, `rejects`, `profiles`) |

## 10. Demo (`demo.py`)

`run_demo` copies the example project, sets `DMC_WORKING_DB_URL` to `<dir>/work.db` for the run, and for each object runs extract, transform, checks (load only if nothing blocks), recon, profile and report. With `--defects` it appends: a duplicate supplier key, a supplier without a name, a non-numeric key, a bank account of an unknown supplier, and a material with weight 5000. Expected demo result: both objects `OK` without defects; with defects **both** objects are `BLOCKED` (supplier: missing name, duplicate key, non-numeric key, orphan bank account - blocking checks `mandatory.general.NAME_ORG1`, `partner_unique`, `partner_is_numeric`, `bank_partner_exists`; material: `weight_in_range`) and their loads are not written. Verified numbers: material 13 checks / 10 rows / 4 reconciliations; supplier 27 checks / 12 + 13 rows / 5 reconciliations.

## 11. Concurrency and performance notes

- The UI serialises operations with a global lock; the CLI has no lock (do not run two commands against one working database at the same time).
- Staging inserts use chunks of 10,000 rows; checks stream results (5,000 per fetch); Parquet loads are held in memory.
- Every step drops and recreates its tables; steps are idempotent: re-running reproduces the result from the files.
