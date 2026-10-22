# ADR 0004: Gated load and column profiling

Status: accepted

## Decision
- `dmc load` always runs the checks first, in the same run. If any result is blocking (see ADR 0003) nothing is written and the command exits 1. There is no option to skip the checks.
- Destinations are listed under `loads:` in `object.yaml`: `file` (csv, xlsx, parquet; path relative to the project folder and must stay inside it) or `table` (any defined connection; `replace` or `append`). Lineage columns (`_dmc_*`) are left out unless `include_lineage: true`.
- Each destination is written atomically: files go to a temporary name and are renamed on success, tables are written in one transaction. A failed or blocked load leaves earlier output untouched. Rows read must equal rows written.
- Profiling (`dmc profile`, staging by default or `--stage target`) stores per-column statistics in `dmc_profile`: empty %, distinct count, min/max, min/max length, top values and top "shapes" (digits → 9, upper → A, lower → a). It reads up to 1,000,000 rows per table into memory.
- Threshold checks for data quality live in `checks.yaml` next to the validation checks: `NULL_RATE`, `PATTERN` (full-match regex), `RANGE`, `OUTLIER` (z-score). They return the same `CheckResult` and can be `warn` or `error`.

## Consequences
Source-data quality can be inspected right after extract, before any mapping exists. Very large tables are profiled on a sample until streaming statistics are added.
