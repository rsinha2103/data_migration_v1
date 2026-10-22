# ADR 0006: Reports

Status: accepted

## Decision
- Reports are built only from the run log (`dmc_run*`, `dmc_run_check`, `dmc_reject`, `dmc_profile`), so any past run can be reported on again.
- Each CLI command creates its own run, so by default `dmc report` assembles the **newest run of each kind** (extract, transform, checks, load, recon, profile) for the object. `--run <id>` reports on that single run.
- One data structure (`collect_report`) feeds three writers: Excel (`Summary`, `Steps`, `Checks`, `Reconciliation`, `Rejects`, `Profile`), a self-contained HTML page and JSON. Files are named `<object>_report.<ext>` in `<project>/reports/` (or `--out`) and overwritten on each run.
- Overall status: `BLOCKED` (a blocking check or reconciliation result), `FAILED` (a pipeline step failed), `ATTENTION` (only warnings) or `OK`.
- Rejected rows are listed up to `--max-rejects` per check (default 100); the full set stays in `dmc_reject`.
- Migration data in reports is untrusted: the HTML is escaped (Jinja autoescape, no scripts), and Excel text cells are always stored as text so a value such as `=1+1` can never become a formula.

## Consequences
A report reflects the latest state of each stage, not one atomic pipeline run. Comparing two mock cycles needs the run ids; run-to-run comparison is a later addition.
