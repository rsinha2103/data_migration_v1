# 04 - Project files reference

A project is a folder of text files that you can keep in git. All files are validated by pydantic models (`src/data_migration_concept/config/models.py`) with **`extra: forbid`** - an unknown key is an error, which catches typos. `dmc validate-config <folder>` (or opening the project in the UI) reports **every** problem at once.

```
my_project/
  project.yaml
  objects/
    supplier/
      object.yaml
      mappings/general.yaml
      xref/country.csv
      checks.yaml
      recon.yaml
      sql/custom.sql
      data/lfa1.csv
  build/            (generated)    output/ (loads)    reports/ (reports)    work.db (UI)
```

> **YAML 1.1 gotcha**: the words `on`, `off`, `yes`, `no`, `null` are read as booleans/null when used as *keys*. That is why the join setting is called `keys`, and the XREF "leave empty" policy is called `blank`. Quote such words as values if needed.

## 1. `project.yaml`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | text | required | project title shown in the UI and used in the run log |
| `description` | text | `""` | |
| `working_connection` | text | required | name of the connection (below) used for staging/XREF/target tables and the run log; must exist in `connections` |
| `connections` | map name -> `{url_env: ENV_NAME}` | required | named database connections. **The URL is read from the environment variable `url_env`** (never stored) |
| `staging_prefix` | text | `stg_` | prefix of staging tables: `<prefix><object>_<source>` |
| `target_prefix` | text | `tgt_` | prefix of target tables: `<prefix><target_table>` |
| `xref_prefix` | text | `xref_` | prefix of XREF tables: `<prefix><object>_<name>` |
| `objects_dir` | text | `objects` | folder (relative to the project) holding the objects |

```yaml
name: Acme Global - S/4 Transformation
description: Fictitious demo programme.
working_connection: working
connections:
  working:
    url_env: DMC_WORKING_DB_URL
  legacy:
    url_env: LEGACY_DB_URL        # used by a database source or a table load
```

## 2. `objects/<object>/object.yaml`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | identifier | required | object name (keep it equal to the folder name; the UI addresses objects by folder name) |
| `description` | text | `""` | |
| `sources` | list (at least 1) | required | see 2.1 |
| `structures` | list | `[]` | see 2.2. A new UI object has none until the target structure is defined |
| `loads` | list | `[]` | see 2.3 |

Uniqueness rules: source names, structure names and target-table names must each be unique within the object; load destinations (file path, or connection+table) must be unique.

### 2.1 Sources

**File source** (`kind: file`)

| Key | Default | Meaning |
|---|---|---|
| `name` | required | identifier (`s` and `x<digits>` are reserved) |
| `path` | required | path relative to the **object folder**, e.g. `data/lfa1.csv` |
| `format` | required | `csv`, `xlsx` or `parquet` |
| `sheet` | first sheet | Excel sheet name |
| `delimiter` | `,` | single character (CSV) |
| `encoding` | `utf-8` | any Python codec, e.g. `cp1252`, `latin-1` |

CSV and Excel are staged as **text**. For Excel the first row is the header; trailing blank header cells are ignored, blank header cells inside are an error; blank rows are skipped; numbers that are whole are written without `.0`; dates as ISO text; booleans `true`/`false`. Parquet keeps native types.

**SQL source** (`kind: sql`)

| Key | Meaning |
|---|---|
| `name` | identifier |
| `connection` | a connection name from `project.yaml` |
| `table` **or** `query` | exactly one: `schema.table` (reads `SELECT *`), or a SELECT statement |

### 2.2 Structures

| Key | Default | Meaning |
|---|---|---|
| `name` | required | identifier |
| `target_table` | required | identifier; the target table is `<target_prefix><target_table>` |
| `source` | required | the **main** source (must exist) |
| `joins` | `[]` | other sources joined to the main source (2.2.1) |
| `unions` | `[]` | extra row sets added with UNION ALL (2.2.2) |
| `mapping` | | mapping file relative to the object folder (e.g. `mappings/general.yaml`) |
| `custom_sql` | | SQL file relative to the object folder (e.g. `sql/general.sql`) |

Exactly one of `mapping` / `custom_sql` is required. `joins` and `unions` need a `mapping` (custom SQL does its own joins/unions). A source cannot be joined to itself and a source can be joined only once per row set.

#### 2.2.1 Joins

| Key | Default | Meaning |
|---|---|---|
| `source` | required | identifier of another source of the object (not `s`, not `x<digits>`) |
| `type` | `left` | `left` keeps all main rows; `inner` only matching rows |
| `keys` | required (>= 1) | list of `{main: <column>, other: <column>}`; `main` is a column of the main source (or an earlier joined source as `source.column`), `other` a column of the joined source. Compared as text |
| `on_duplicates` | `error` | `error`: the transform stops when a key occurs more than once in the joined source; `first`: use the row with the lowest `_dmc_row_no` per key |

#### 2.2.2 Row sets (`unions`)

| Key | Meaning |
|---|---|
| `name` | identifier, unique within the structure, not `main` |
| `source` | the row set's main source |
| `joins` | joins of this row set (same format) |
| `overrides` | map `target_field -> rule` (same rule format as in the mapping) replacing the mapping's rule for that field in this row set |

Every other field uses the structure's mapping. A field must be `NOT_MIGRATED` in every row set or in none. `overrides` keys must be mapped target fields. XREF files used by overrides must exist.

```yaml
structures:
  - name: vendor
    target_table: bp_vendor
    source: lfb1
    joins:
      - {source: lfa1, keys: [{main: LIFNR, other: LIFNR}]}
    unions:
      - name: FLVN01
        source: lfm1
        joins:
          - {source: lfa1, keys: [{main: LIFNR, other: LIFNR}]}
        overrides:
          BP_ROLE: {type: CONSTANT, value: FLVN01}
          ORG:     {type: MOVE, source_field: EKORG}
    mapping: mappings/vendor.yaml
```

### 2.3 Loads

**File load** (`kind: file`): `structure` (required), `path` (relative to the **project** folder; must stay inside it), `format` (`csv`/`xlsx`/`parquet`), `delimiter` (`,`), `encoding` (`utf-8`), `include_lineage` (`false`; keep `_dmc_*` columns).

**Table load** (`kind: table`): `structure`, `connection` (a connection name), `table` (`name` or `schema.name`), `mode` (`replace` default, or `append`), `include_lineage` (`false`).

## 3. `objects/<object>/mappings/<structure>.yaml`

```yaml
fields:
  - target_field: PARTNER          # required, unique within the file
    description: Business partner number
    data_type: string              # string (default) | integer | decimal | date | boolean
    length: 10                     # optional, > 0
    mandatory: true                # default false
    key: false                     # default false
    note: ""                       # optional comment for the reviewer
    review: ""                     # "" (open) | approved | changes
    rule: { type: MOVE, source_field: LIFNR }
```

`fields` needs at least one entry. `data_type`, `length`, `mandatory` and `key` are **not applied in SQL**; they are used by the structure-based checks (when confirmed), by proposals and by the preview flags. `note` and `review` are ignored by the generator.

### 3.1 Rules

| `type` | Keys | Generated expression (`s` = main row) |
|---|---|---|
| `MOVE` | `source_field` | `s."COL"` (or `alias."COL"` for `alias.COL` of a joined source) |
| `CONSTANT` | `value` (text, number, boolean or null) | the literal (quotes doubled; numbers/booleans as such; null -> `NULL`) |
| `XREF` | `source_field`, `xref` (list name), `on_unmapped` (`error` default / `default` / `blank`), `default` (required when `on_unmapped: default`) | value of `xN."target_value"` from `LEFT JOIN xref_<object>_<name> AS xN ON CAST(col AS TEXT) = xN."legacy_value"`; `default` -> `COALESCE(value, 'default')`; `error` also runs a pre-check that stops on unmapped values |
| `CONCAT` | `source_fields` (>= 2), `separator` (`""`) | `COALESCE(CAST(a AS TEXT),'') \|\| 'sep' \|\| COALESCE(CAST(b AS TEXT),'') ...` |
| `SUBSTRING` | `source_field`, `start` (>= 1, default 1), `length` (>= 1) | `SUBSTR(CAST(col AS TEXT), start, length)` |
| `SQL` | `expression` | `(<expression>)`, after Jinja rendering (names `stg.<source>`, `xref.<name>`, `tgt.<structure>`) |
| `NOT_MIGRATED` | none | the field is omitted from the target table |
| `UNMAPPED` | none | `CAST(NULL AS TEXT)` - the field stays, empty |

A reference `source.COLUMN` addresses a column of a **joined** source; an unprefixed name addresses the row set's main source.

## 4. `objects/<object>/checks.yaml`

```yaml
from_mapping:          # structure-based rules you have confirmed, each with a severity
  mandatory: error     # mandatory | length | data_type | key  ->  error | warn
  length: error
  data_type: warn
  key: error
checks:
  - name: partner_unique          # identifier, unique within the file
    structure: general            # must exist
    type: UNIQUE
    severity: error               # error (default) | warn
    description: One business partner per legacy vendor number.
    fields: [PARTNER]
```

**Nothing runs unless listed.** `from_mapping` kinds not listed are not checked.

| `type` | Extra keys | Notes |
|---|---|---|
| `UNIQUE` | `fields` (>= 1) | combination must be unique |
| `ALLOWED_VALUES` | `field`, `values` (>= 1; text/number/boolean) | compared as text; booleans as `true`/`false` |
| `REFERENTIAL` | `field`, `ref_structure`, `ref_field`, `ref_object` (default: same object) | referenced structure must exist and have the field |
| `SQL_ASSERT` | `sql` | Jinja: `{{ tgt.<structure> }}`, `{{ stg.<source> }}`, `{{ xref.<name> }}`; returns the offending rows; include `"_dmc_row_no"` to identify the source row |
| `NULL_RATE` | `field`, `max_pct` (0-100) | |
| `PATTERN` | `field`, `pattern` (regex, valid) | full-value match (`re.fullmatch`) |
| `RANGE` | `field`, `min`, `max` (at least one; min <= max) | numeric |
| `OUTLIER` | `field`, `max_zscore` (> 0, default 4.0) | population standard deviation |

All checks must refer to existing structures; fields must exist among the target fields (for custom-SQL structures the columns are not known up front and are not validated).

## 5. `objects/<object>/recon.yaml`

```yaml
recons:
  - name: general_row_count
    structure: general
    type: COUNT
    severity: error          # error (default) | warn
    description: ""
    target_table: null       # compare against this working-DB table instead
    exclude_source: null     # SQL condition over alias s: rows deliberately not migrated
```

| `type` | Extra keys |
|---|---|
| `COUNT` | `tolerance` (int >= 0, default 0) |
| `KEYS` | `source_key`, `target_key` (same length), `trim` (default true) |
| `TOTALS` | `source_field`, `target_field`, `agg` (`sum` default, `count`, `count_distinct`, `min`, `max`), `group_by_source`, `group_by_target` (same length), `tolerance` (>= 0) |
| `FIELDS` | `source_key`, `target_key`, `fields` (`auto` default, or list of `{source:, target:}`), `tolerance` (numeric absolute, default 0 = exact text match), `trim` (true), `ignore_case` (false) |

## 6. XREF files `objects/<object>/xref/<name>.csv`

```
legacy_value,target_value
DE,DE
UK,GB
```

Rules enforced when the list is loaded by Transform/Preview: columns `legacy_value` and `target_value` required (extra columns ignored); no empty `legacy_value`; no legacy value more than once. Values are compared as **text** (exact, case-sensitive). The file is read with every column as text.

## 7. Custom SQL files `objects/<object>/sql/*.sql`

A SELECT that returns the columns of the target table. It is wrapped as `CREATE TABLE tgt_x AS <select>`. Jinja names: `{{ stg.<source> }}`, `{{ xref.<name> }}`, `{{ tgt.<structure> }}` (real table names). A trailing `;` is removed. Include `_dmc_row_no` (and `_dmc_run_id`) if you want row numbers in rejects. The Jinja environment is strict: an unknown name is an error ("structure 'x': ..."). Custom-SQL structures have no mapping grid, no preview, no joins/row sets in YAML (write them in the SQL).

## 8. Cross-file validation (what the loader checks)

All of these are reported together by `validate-config`, and make the UI refuse an edit that introduces them:

| Check | Message pattern |
|---|---|
| missing file | `<path>: file not found` |
| YAML syntax | `<path>: invalid YAML: ...` |
| schema | `<path>: <key path>: <pydantic message>` (e.g. `Extra inputs are not permitted`, `Field required`) |
| `objects/` folder missing | `objects/: directory not found` (every sub-folder of `objects/` must contain an `object.yaml`) |
| file source file missing | `<object.yaml>: source 'x': file 'data/x.csv' not found` |
| unknown connection | `source 'x': unknown connection 'c'` / `load of 's': unknown connection 'c'` |
| structure/join/row-set source unknown | `structure 's': unknown source 'x'` / `joined source 'x' does not exist` / `row set 'r': unknown source 'x'` |
| custom SQL file missing | `structure 's': SQL file 'sql/x.sql' not found` |
| XREF file missing | `<mapping>: field 'F': xref file 'xref/x.csv' not found` |
| row-set override problems | `override of unknown target field 'F'`; `field 'F' must be NOT_MIGRATED in every row set or in none`; xref file missing |
| load problems | `load: unknown structure 's'`; `path 'p' leaves the project`; duplicate destinations |
| checks/recons | unknown structure; `has no target field 'F'`; referential: `unknown ref_object`, `unknown ref_structure`, `has no field` |
| duplicates | duplicate object/source/structure/target_table/check/reconciliation names; duplicate `target_field` in a mapping |

## 9. Environment variables

| Variable | Used for |
|---|---|
| `DMC_WORKING_DB_URL` (name set by `url_env`) | working database URL (SQLAlchemy URL) |
| other `url_env` names | database sources / table loads |
| `DMC_TEST_POSTGRES_URL` | enables the PostgreSQL test |

`.env.example` in the repository shows the format.
