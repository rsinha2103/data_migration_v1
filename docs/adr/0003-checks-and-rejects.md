# ADR 0003: Check framework and reject storage

Status: accepted

## Decision
- Every check implements `Check.run(ctx) -> CheckResult` with status `PASS`, `FAIL` or `ERROR` (the check itself could not run) and severity `error` or `warn`.
- A result is **blocking** when it is an `error`-severity `FAIL`, or any `ERROR`. The load step (Phase 5) refuses to run while blocking results exist; `dmc check` exits 1.
- Mandatory, length and data-type checks are derived from the mapping (`mandatory`, `length`, `data_type`). Everything else is declared in `objects/<object>/checks.yaml`: `UNIQUE`, `ALLOWED_VALUES`, `REFERENTIAL` (same or another object), `SQL_ASSERT` (a query returning the offending rows).
- Checks run against the target tables (after transform). Set-based checks are SQL; data-type checks run in Python so they behave the same on every database.
- Results go to `dmc_run_check`; failing rows go to `dmc_reject` (run, check, structure, `row_no`, field, value, message), capped at 10,000 per check. `failed_count` is always exact and `truncated` says when the cap was hit. `row_no` is the staged source row number.
- Data-type rules: integer = optional sign and digits; decimal = parseable finite number with `.`; date = ISO `YYYY-MM-DD`; boolean = true/false/1/0.

## Consequences
Bad config (unknown structure or field) is caught when the project loads; problems that only show in the data become `ERROR` results instead of crashes. DQ profiling (Phase 5) and reconciliation (Phase 6) reuse the same `Check` / `CheckResult` types.
