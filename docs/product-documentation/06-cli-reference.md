# 06 - Command-line reference (`dmc`)

Entry point: `dmc` (`data_migration_concept.cli:app`, Typer). Run it as `uv run dmc ...` (or `python -m uv run dmc ...`). `dmc` with no arguments prints the help.

All commands that touch data need the **working database URL** in the environment variable named by `working_connection` in `project.yaml` (normally `DMC_WORKING_DB_URL`). Commands take a **project folder** (the folder containing `project.yaml`) and optionally an **object name**; without an object name they process **all objects**.

**Exit codes**: `0` success; `1` a problem (invalid project, step failed, checks/reconciliation blocking); `2` bad option value (`profile --stage`).

| Command | Purpose |
|---|---|
| `version` | print the version |
| `validate-config [PATH]` | validate a project, report every problem |
| `show-sql PATH [OBJECT] [--dialect D]` | print generated SQL (no database needed) |
| `extract PATH [OBJECT]` | sources -> staging |
| `transform PATH [OBJECT]` | staging -> target (SQL saved under `build/`) |
| `check PATH [OBJECT]` | run the confirmed checks |
| `load PATH [OBJECT]` | check, then write the destinations |
| `recon PATH [OBJECT]` | reconcile source and target |
| `profile PATH [OBJECT] [--stage] [--max-rows]` | profile columns |
| `report PATH [OBJECT] [--run] [--format] [--out] [--max-rejects]` | write reports |
| `demo [--dir] [--defects] [--force] [--source]` | copy and run the Acme demo |
| `ui [--workspace] [--port] [--open/--no-open]` | start the web interface |

## Commands in detail

### `dmc version`
Prints the installed version (`0.1.0`).

### `dmc validate-config [PATH]`
Default `PATH` = current folder. Success: `OK: project '<name>' with N object(s), M structure(s).` Failure: `Invalid project (K problem(s)):` followed by `  - <problem>` lines on stderr, exit 1. No database needed.

### `dmc show-sql PATH [OBJECT] [--dialect postgresql]`
Prints, for each structure, `-- <object>.<structure> -> <target table>` and its SQL statements (DROP + CREATE TABLE AS SELECT) translated to the dialect (`sqlite`, `postgresql`, `mysql`, `mssql`, `oracle`). No database is needed. Unknown object -> `unknown object 'x'`, exit 1.

### `dmc extract PATH [OBJECT]`
Per object: starts a run, stages every source. Output: `<object>: OK (run <id>)` then `  stg_...: N row(s), C column(s)` per source; on error `<object>: FAILED (run <id>)` + the reason, exit 1 at the end (other objects still run).

### `dmc transform PATH [OBJECT]`
Builds target tables and writes `build/<object>/<structure>.sql`. Output: `<object>: OK (run <id>)` + `  tgt_x: R row(s) from S`. Failures as in chapter 05 section 4.

### `dmc check PATH [OBJECT]`
Runs the **confirmed** checks. Output per object:
- none confirmed: `<object>: no checks are confirmed, so nothing was run (see checks.yaml: from_mapping and checks).` (exit 0, nothing recorded)
- otherwise `<object>: OK (run <id>)` or `<object>: BLOCKED (run <id>)`, then one line per check: `  PASS <name>`, `  FAIL <name> - <message>` (error severity), `  WARN <name> - <message>`, `  ERROR <name> - <reason>`, each followed by up to 3 sample rows `        row N: <message> [value]`.
- exit 1 when anything blocks or the checks cannot run.

### `dmc load PATH [OBJECT]`
Runs the checks, and only if nothing blocks writes every destination. Output: `<object>: LOADED (run <id>)` + `  <structure> -> <destination>: R row(s)`; blocked: `<object>: NOT LOADED, checks blocked the load (run <id>)` + the failing checks (stderr), exit 1; other failures `<object>: FAILED (run <id>)` + reason.

### `dmc recon PATH [OBJECT]`
Without `OBJECT`, all objects that have a `recon.yaml` (none: `No object has a recon.yaml.`, exit 1). Output: `<object>: RECONCILED (run <id>)` or `DIFFERENCES`, with the result lines as for checks. Exit 1 on error-severity differences or when tables are missing.

### `dmc profile PATH [OBJECT] [--stage staging|target] [--max-rows N]`
Default stage `staging`, default `--max-rows 1000000`. Output per table and column: `    COL: R rows, P% empty, D distinct, len a-b, range min..max; top: v (n), ...`. `--stage` other than the two values -> exit 2.

### `dmc report PATH [OBJECT] [--run ID] [--format xlsx,html,json] [--out FOLDER] [--max-rejects N]`
Writes the reports from the run log (default: newest run of each kind; `--run` = one run; default folder `<project>/reports`; default 100 rejects per check). Output: `<object>: <OVERALL>` and the file paths. Errors: `unknown run 'x'`, `unknown format(s): ...`, `nothing recorded yet for object 'x'` (exit 1).

### `dmc demo [--dir dmc_demo] [--defects] [--force] [--source PATH]`
See chapter 02 section 3. Output per object: overall status, checks passed, "LOAD BLOCKED by: ..." when relevant, loaded destinations, reconciliations passed, report path. Errors: folder exists (use `--force` or another `--dir`), example not found, invalid example.

### `dmc ui [--workspace dmc_workspace] [--port 8765] [--open/--no-open]`
See chapter 02 section 4.

## Typical scripted run

```powershell
$env:DMC_WORKING_DB_URL = "postgresql+psycopg://dmc:dmc@localhost:5432/dmc"
uv run dmc validate-config .\my_project
uv run dmc extract   .\my_project supplier
uv run dmc transform .\my_project supplier
uv run dmc check     .\my_project supplier
uv run dmc load      .\my_project supplier
uv run dmc recon     .\my_project supplier
uv run dmc report    .\my_project supplier
```

`check`, `load` and `recon` return a non-zero exit code when they block, so they can gate a pipeline.

## CLI test cases

| ID | Steps | Expected |
|---|---|---|
| TC-CLI-01 | `dmc version` | `0.1.0` |
| TC-CLI-02 | `dmc validate-config examples/acme_demo` | `OK: project 'Acme Global - S/4 Transformation' with 2 object(s), 3 structure(s).` |
| TC-CLI-03 | Add a typo key to a YAML file, run validate-config | exit 1; problem lists `Extra inputs are not permitted` with the file and key |
| TC-CLI-04 | `dmc show-sql examples/acme_demo supplier --dialect sqlite` | CREATE TABLE AS SELECT for `general` and `bank` |
| TC-CLI-05 | Without `DMC_WORKING_DB_URL`: `dmc extract ...` | error "environment variable 'DMC_WORKING_DB_URL' is not set", exit 1 |
| TC-CLI-06 | Full scripted run on the demo | all commands exit 0; `LOADED`; `RECONCILED`; report files exist |
| TC-CLI-07 | Same with `dmc demo --defects` data | `check` prints `BLOCKED`, exit 1; `load` prints `NOT LOADED`, exit 1 |
| TC-CLI-08 | `dmc check` on a project with no confirmed checks | "no checks are confirmed", exit 0 |
| TC-CLI-09 | `dmc report ... --format pdf` | `unknown format(s): pdf`, exit 1 |
| TC-CLI-10 | `dmc profile ... --stage nothing` | exit 2 |
