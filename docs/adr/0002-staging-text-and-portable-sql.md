# ADR 0002: Text staging and portable generated SQL

Status: accepted

## Decision
- CSV and Excel values are staged as text so keys keep leading zeros. Parquet and SQL sources keep native types. Type conversion and type validation happen later (validation checks, Phase 4), not during staging.
- Generated SQL is plain ANSI that runs unchanged on SQLite and PostgreSQL: double-quoted identifiers, `||`, `CAST(x AS TEXT)`, `SUBSTR`, `CREATE TABLE ... AS`. Other databases are served by transpiling with sqlglot (best effort).
- Raw `SQL` rules and custom `.sql` files are inserted as written (not transpiled), so they must be valid for the working database. Custom SQL is a Jinja2 template with `stg.<source>`, `xref.<name>` and `tgt.<structure>` names.
- XREF files (`legacy_value,target_value`) are loaded into `xref_<object>_<name>` tables and joined with LEFT JOIN. A legacy value may appear only once. Unmapped policies: `error` (default; the transform fails and lists the values), `default`, `blank`. The word `null` is not used because YAML reads it as None.
- Every staged and target row carries `_dmc_row_no` and `_dmc_run_id` so later checks can point back to the source row.

## Consequences
Generated SQL can be reviewed in `build/` and compared against golden files. A failed transform leaves the previous target table untouched.
