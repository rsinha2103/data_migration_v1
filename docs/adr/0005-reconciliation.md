# ADR 0005: Reconciliation

Status: accepted

## Decision
- Reconciliations are declared in `objects/<object>/recon.yaml` and run with `dmc recon`. They reuse the check framework: each one is a `Check` that returns a `CheckResult` (type `RECON_*`), stored in `dmc_run_check` and `dmc_reject`, with the same `error` / `warn` severity and blocking rules (ADR 0003). Reconciliation does not gate the load; it is run afterwards.
- They compare the **staged source** of a structure with its **target table**. `target_table:` points at any other table in the working database instead (for example an extract of the target system loaded as a source), so a load can be proved against what the target system really holds.
- Types:
  - `COUNT`: rows expected (source minus explained exclusions) vs rows found, with an absolute tolerance.
  - `KEYS`: keys missing in the target and keys unexpected in the target, compared as text (trimmed by default). Composite keys are supported.
  - `TOTALS`: `sum`, `count`, `count_distinct`, `min`, `max` of a field, optionally per group, with tolerance.
  - `FIELDS`: values of rows with matching keys; `fields: auto` compares every MOVE-rule field. Numeric tolerance applies only when `tolerance > 0` (otherwise text must match, so `0001` and `1` differ); options `trim` and `ignore_case`.
- **Explained drops:** `exclude_source` is a SQL condition over alias `s`. Source rows it matches are intentionally not migrated and are removed from the expectation. Nothing is excluded implicitly.
- Comparisons that are not simple set operations run in Python over streamed rows (keys and aggregates in memory), so they behave the same on every database and avoid correlated subqueries.

## Limits
Memory use is proportional to the number of keys. Duplicate keys: `KEYS` and `FIELDS` use the first row per key; use a `UNIQUE` check to find duplicates. Source and target must be in the working database; there is no cross-database comparison.
