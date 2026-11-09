# 09 - Traceability, reference tables and production readiness

This chapter is the **reference index** for the move from demo to a production-ready product: every feature mapped to its code and tests, every web route, every limit and constant, the message catalogue, the decision records, and an honest list of what is *not* production-ready yet.

## 1. Feature -> code -> tests

| Feature | Where in the code (`src/data_migration_concept/`) | Automated tests (`tests/`) | Manual cases |
|---|---|---|---|
| Project/object/structure config models | `config/models.py`, `config/loader.py` | `test_config_models.py`, `test_loader.py` | TC-CLI-02/03, TC-E2E-07 |
| Connections from environment variables | `connectors/factory.py` | `test_connectors.py` | TC-CLI-05 |
| Run log (runs, steps, schema, checks, rejects, profiles) | `runlog/log.py` | `test_runlog.py` | S21 |
| Extract: CSV/Excel/Parquet/SQL readers | `extract/readers.py` | `test_extract.py` | TC-DATA-01..14 |
| Staging tables, lineage columns | `extract/staging.py` | `test_extract.py` | TC-E2E-02 step 9-10 |
| Extract validation of mapping/join columns | `extract/runner.py` | `test_extract.py`, `test_joins.py`, `test_unions.py` | TC-E2E-04 step 13 |
| Rule SQL generation (MOVE ... UNMAPPED) | `transform/generator.py` | `test_transform.py`, `test_unmapped.py`, `tests/golden/` | S20, TC-S09 |
| XREF loading and policies | `transform/xref.py`, `transform/generator.py`, `transform/runner.py` | `test_transform.py` | S12/S13, TC-E2E-02 |
| Joins and duplicate-key safety | `config/models.py` (JoinConfig), `transform/generator.py`, `transform/runner.py` | `test_joins.py`, `test_ui_joins.py` | TC-E2E-03 |
| Row sets (UNION ALL) | `config/models.py` (RowSet), `transform/generator.py`, `extract/runner.py`, `config/loader.py` | `test_unions.py` | TC-E2E-04 |
| SQL rules with `{{ stg.x }}`, readable DB errors | `transform/generator.py`, `transform/runner.py` (`db_error`) | `test_unions.py` | TC-S10-07 |
| Preview | `transform/preview.py`, `ui/templates/mapping_preview.html` | `test_ui_preview.py` | S10 |
| Custom SQL structures | `transform/generator.py` | `test_transform.py` | TC-S22-06 |
| Checks (all types) and rejects | `checks/builtin.py`, `checks/runner.py`, `checks/base.py` | `test_checks.py`, `test_dq.py` | S14 |
| Opt-in checks (`from_mapping`) | `checks/runner.py`, `ui/editor.py` | `test_checks_confirmed.py` | TC-S14-01..04 |
| Load (file/table, atomic, gated) | `load/runner.py`, `load/writers.py` | `test_load.py` | S15 |
| Reconciliation | `recon/checks.py`, `recon/runner.py` | `test_recon.py`, `test_unions.py` | S16 |
| Profiling | `profile/profile.py`, `profile/runner.py` | `test_dq.py` | S19 |
| Reports (Excel/HTML/JSON) | `report/` | `test_report.py` | TC-S04-10, E2E-01 |
| Proposals engine | `proposals/engine.py`, `catalog.py`, `countries.py`, `model.py` | `test_proposals.py` | chapter 07 |
| Proposals UI (apply/dismiss) | `ui/proposals_service.py`, `ui/app.py` | `test_ui_proposals.py` | S05 |
| Target structure templates (Cockpit, Excel, CSV, paste) | `ui/targets.py`, `ui/editor.py` | `test_targets.py`, `test_ui_targets.py` | S07/S08, E2E-06 |
| Mapping editor, auto-map, bulk tools | `ui/editor.py`, `ui/templates/mapping.html`, `ui/static/app.js` | `test_ui.py` (save/auto-map) | S09, E2E-05 (bulk tools are JavaScript: manual only) |
| Mapping specification Excel export/import | `ui/mapping_sheet.py` | `test_mapping_sheet.py`, `test_unions.py` | S11, E2E-05 |
| Validated writes with rollback | `ui/editor.py` (`apply_changes`) | `test_ui.py`, `test_ui_proposals.py` | TC-E2E-07 |
| Project workspace, demo creation | `ui/workspace.py`, `demo.py` | `test_ui.py`, `test_demo.py` | S01 |
| Pipeline running/flow state | `ui/pipeline.py` | `test_ui.py` | S04 |
| Security (localhost, CSRF-style check, downloads) | `ui/app.py` | `test_ui.py` | S23, TC-SEC |
| CLI | `cli.py` | `test_cli.py` | chapter 06 |
| PostgreSQL parity | all SQL generation | `test_transform_postgres.py` | E2E-08 |

## 2. Web routes (complete list)

`<p>` = project, `<o>` = object, `<s>` = structure. GET returns HTML unless noted; POST redirects (303) with a flash message unless noted.

| Method | Path | Screen / purpose |
|---|---|---|
| GET | `/` | S01 home |
| GET | `/how-it-works` | S02 |
| POST | `/projects/create`, `/projects/demo`, `/projects/add` | create project / demo / add by path |
| POST | `/p/<p>/forget` | remove an added project from the list |
| GET | `/p/<p>` | S03 project |
| POST | `/p/<p>/objects/add` | create object from a file |
| GET | `/p/<p>/o/<o>` | S04 flow |
| POST | `/p/<p>/o/<o>/run/<step>` | run `extract`, `transform`, `check`, `load`, `recon`, `profile`, `report` or `all` |
| GET | `/p/<p>/o/<o>/sources` | S06 |
| POST | `.../sources/upload`, `.../sources/sql`, `.../sources/remove` | add file / DB source, remove |
| GET | `.../structures` | S07 |
| GET | `/templates/target-structure.xlsx` / `.csv` | blank template download |
| POST | `.../structures/add` | way C (copy source columns) |
| POST / GET / POST | `.../structures/template`, `.../structures/template/<token>` | ways A/B, S08 preview, confirm |
| POST | `.../structures/<s>/template` | start "update from template" |
| POST | `.../structures/<s>/source` | change the main source |
| POST | `.../structures/<s>/joins`, `.../joins/remove` | add / remove a join |
| POST | `.../structures/<s>/rowsets`, `.../rowsets/remove` | add / remove a row set |
| POST | `.../structures/<s>/rowsets/<name>/rules` | save a row set's grid |
| POST | `.../structures/<s>/rowsets/override` | set/remove a single override (kept for scripts and tests; the UI uses the grid) |
| POST | `.../structures/remove` | remove a structure |
| GET / POST | `.../structures/<s>` | S09 mapping page / save mapping (`action=save` or `auto_map`; `add_missing` exists in the route but has no button) |
| GET | `.../structures/<s>/preview?rows=n` | S10 |
| GET | `.../structures/<s>/spec.xlsx` | download the specification workbook |
| POST / GET / POST | `.../structures/<s>/spec`, `.../spec/<token>` | import: upload, S11 preview, confirm |
| GET / POST | `.../xref`, `.../xref/new`, `.../xref/<name>`, `.../xref/<name>/upload`, `.../xref/<name>/delete` | S12/S13 |
| GET | `.../checks`, `.../recon` | S14, S16 |
| POST | `.../checks/from-mapping`, `.../checks/add`, `.../checks/delete`, `.../recon/add`, `.../recon/delete` | manage checks/reconciliations |
| GET / POST | `.../loads`, `.../loads/add`, `.../loads/delete` | S15 |
| GET | `.../proposals` | S05 |
| POST | `.../proposals/apply`, `.../proposals/dismiss`, `.../proposals/restore` | |
| GET | `.../data`, `.../data/<table>` | S17, S18 |
| GET | `.../results[?check=]` | S19 |
| GET | `.../sql` | S20 |
| GET | `/p/<p>/runs` | S21 |
| GET / POST | `/p/<p>/files` | S22 |
| GET | `/p/<p>/download?path=&inline=` | downloads below `output/` and `reports/` |
| GET | `/static/*` | CSS and JavaScript |

## 3. Limits and constants

| Constant | Value | Where |
|---|---|---|
| Source file upload | 200 MB | `ui/editor.py` `MAX_UPLOAD` |
| Template file | 30 MB; zip <= 600 members; CSV member > 5 MB ignored | `ui/targets.py` |
| Template rows read | 20,000 | `ui/editor.py` `MAX_TEMPLATE_ROWS` |
| Pending upload tokens in memory | 12 | `ui/targets.py` `PendingUploads` |
| Insert/read chunk | 10,000 rows | `extract/readers.py` `CHUNK_SIZE` |
| Check streaming fetch | 5,000 rows | `checks/builtin.py` `FETCH` |
| Rejects stored per check | 10,000 (`truncated` flag) | `checks/base.py` |
| Failure samples in CLI | 20 stored in result, 3 printed | `checks/base.py`, `cli.py` |
| Reject rows in reports | 100 per check (UI results 500) | `report/collect.py`, `ui/app.py` |
| Unmapped XREF values shown | 20 | `transform/generator.py` `MAX_UNMAPPED_SHOWN` |
| Join duplicate keys shown | 5 (query limit 10) | `transform/runner.py` |
| Preview rows | 5/10/25/50 (max 50 per row set); XREF missing values 10 | `transform/preview.py` |
| Profile rows (CLI/step) | 1,000,000 | `profile/profile.py` |
| Profile rows for proposals | 100,000; distinct values per column 2,000 | `ui/proposals_service.py` |
| Profile top values / shapes | 5 each; value text cut at 50 characters | `profile/profile.py` |
| Proposals: allowed-values limit | 12 distinct values | `proposals/engine.py` |
| Proposals: totals per structure | 3 | `proposals/engine.py` |
| Data preview page size | 50 (function allows up to 500) | `ui/pipeline.py` |
| Run history | last 60 runs | `ui/app.py` |
| Project name length | 64 | `ui/workspace.py` |
| Excel sheet names | 31 characters | `load/writers.py`, `ui/mapping_sheet.py` |

## 4. Message catalogue (most common)

| Message (or start) | Raised by | Meaning / remedy |
|---|---|---|
| `object 'x' has no target structure yet; define one before transforming` | Transform | define a structure (S07) |
| `staging table 't' not found; run extract first` | Transform/Preview | run Extract |
| `source 'x' does not match the mapping:` ... `missing source column(s)` | Extract | a mapped/joined column is not in the source; fix the mapping or the file |
| `structure 's' has unmapped values:` / `no entry in xref 'n' for ...` | Transform | add the values to the XREF list (S13) or change the policy |
| `the join with 'x' would multiply rows because its key (K) is not unique` | Transform/Preview | fix the data or use *first row* |
| `the database rejected the generated SQL: ...` | Transform/Preview | SQL rule/custom SQL error; use `{{ stg.x }}` and qualified columns |
| `xref 'n': legacy_value mapped more than once: ...` | Transform | remove duplicates |
| `target table(s) not found: ...; run transform first` | Check/Profile/Recon | run Transform |
| `No checks are confirmed yet, so nothing was run ...` | Check | by design; confirm checks (S14/S05) |
| `Nothing was loaded: checks are blocking the load.` | Load | see the failing checks (S19); fix data or severity |
| `object 'x' has no 'loads' defined in object.yaml` | Load | add a destination (S15) |
| `structure 's': read N rows but wrote M` | Load | destination problem; nothing is kept (file: temp file removed) |
| `object 'x' has no recon.yaml` / `table(s) not found: ...` | Recon | add reconciliations / run earlier steps |
| `environment variable 'X' is not set` | CLI | set the working-database URL |
| `Not saved - the file was left as it was.` | Files | the edit would add a project error; see the lines |
| `That upload has expired. Please upload the file again.` | S08/S11 | the server forgot the upload (restart or > 12 uploads) |
| `Names use letters, digits and '_' and start with a letter.` | many | identifier rule |
| `Extra inputs are not permitted` | validation | a misspelt key in YAML |
| `unknown source 'x'` | validation | a structure/join/row set refers to a removed source |
| `has no target field 'F'` | validation | a check/reconciliation refers to a field that does not exist |

## 5. Decision records (`docs/adr/`)

| ADR | Decision |
|---|---|
| 0001 | Engine first; hybrid transforms: simple rules generate SQL, complex rules are SQL |
| 0002 | Staging stores text; generated SQL is portable ANSI |
| 0003 | Checks, severities and reject tables |
| 0004 | Load is gated by the checks; profiling |
| 0005 | Reconciliation reuses the check framework |
| 0006 | Reports (Excel, HTML, JSON) from the run log |
| 0007 | Local web UI (FastAPI + server-rendered pages), safe edits |
| 0008 | Target structure first, then mapping; templates |
| 0009 | Proposals: suggested, never applied automatically |
| 0010 | Only confirmed checks run (supersedes the "automatic derived checks" of 0003/0009) |
| 0011 | Joins, UNION ALL row sets, bulk mapping, preview, Excel mapping specification |

## 6. Known limitations (current behaviour, not defects)

| Area | Limitation |
|---|---|
| Row sets | `_dmc_row_no` restarts per source (not unique across row sets). KEYS/FIELDS/TOTALS reconciliations, bulk tools, notes/review and "Used by" on XREF screens work on the main rows only. |
| Joins | at most the key pairs entered (UI: 3 for joins, 2 for row-set joins; YAML: any number); joins compare text; joins chain only from the main source |
| Typing | the target table keeps the staged (text) types; `data_type` is validated, not cast. A database load therefore writes text columns unless the source was typed (Parquet/SQL) |
| Mapping editor | saving rewrites the YAML without comments; sorting reorders fields in the file; `source.COLUMN` references are not validated until Extract |
| Structure update | merging a template never deletes fields |
| Checks | data-type validation of dates accepts ISO `YYYY-MM-DD` only |
| Reports | latest run of each step kind only (no run-to-run comparison) |
| Custom SQL | no mapping grid, no preview, no joins/row sets/bulk tools |
| Excel | loads to Excel are limited by Excel's own 1,048,576-row limit |
| UI | no automated browser tests; JavaScript behaviour is tested manually |

## 7. Production-readiness: open items

These are the items to plan when moving from the demo to a production-ready product. They are grouped by theme; none is implemented today.

### 7.1 Security and access
- **No authentication or authorisation.** The UI is single-user and localhost-only. A multi-user service needs sign-in, roles (e.g. mapper, reviewer, approver, operator) and per-project permissions.
- **Secrets**: only environment-variable URLs. Production needs a secret store, credential rotation and no secrets in process environment dumps.
- **Data protection**: reports, rejects and the run log contain source values. Needs retention rules, masking/pseudonymisation options and encryption at rest decisions.
- **Audit trail**: only the run log; there is no record of *who* changed a mapping or confirmed a check. Git history is the only change log.

### 7.2 Multi-user and operations
- The UI uses a **global lock and process-wide environment-variable switching** to point at the working database; a production service needs per-request/per-job configuration and proper concurrency control.
- **Steps run inside the HTTP request** (no background jobs, progress, cancel or timeouts). Large migrations need a job runner/queue and progress reporting.
- A crashed process leaves runs `RUNNING`; add heartbeat/cleanup.
- Flash messages and pending uploads are **in memory** (lost on restart, not shared between workers).
- **Scheduling, environment promotion** (dev -> test -> prod configuration), parameterised projects, and orchestration across objects (dependency order, cut-over plans) are not present.
- **Observability**: no structured logging, metrics or alerting; errors are shown to the user only.

### 7.3 Data and engine
- **Typed targets**: create target tables with real column types (from the structure's data type/length) and cast in SQL, with controlled error handling.
- **Incremental/delta loads**, load **rollback/undo**, idempotent keys on database loads, and reconciliation against the *target system* (a `target_table` comparison exists but needs an operational pattern).
- **Dialects**: PostgreSQL and SQLite are tested; SAP HANA, Oracle, SQL Server and MySQL are best effort (SQLGlot) and untested. Large-volume behaviour (profiling and Parquet loads are in memory; type/pattern checks run row by row in Python) needs performance testing and, where needed, set-based variants.
- **SAP-specific**: Migration Cockpit templates are *read* (structure definition); writing Cockpit-ready load files in the exact template format, SAP field-type handling, and connectors to SAP systems are not implemented.
- **Row sets**: a lineage column identifying the row set; full reconciliation support per row set.
- **Mapping model**: conditional rules (if/else), lookups with several keys, value-list versioning, reusable rule libraries.

### 7.4 Governance and collaboration
- Workflow for **review and sign-off** (today: an advisory `review` value and an Excel round trip), approval gates before load, change requests, comments per field with history.
- Multi-project dashboards, cross-object dependency views, and reporting across runs (trends, run comparison).
- Documentation generation from the project (the specification workbook is a start).

### 7.5 Quality
- Add **browser-level automated tests** (screens in chapter 03) and **performance/volume tests**; run the PostgreSQL suite by default in CI (it already runs in the CI matrix).
- Add **contract tests** for the file formats (YAML schemas as JSON Schema, generated from the pydantic models) and a **migration path** for future format changes (version key in `project.yaml`).
- Package for distribution (wheel/installer/container image), versioned releases and a changelog.

### 7.6 Suggested order
1. Multi-user foundation (authentication, per-request configuration, background jobs).
2. Typed targets and load safety (rollback, idempotency).
3. Governance workflow and audit trail.
4. SAP-specific load formats and connectors.
5. Scale and observability.

## 8. How to keep this documentation current

- Every new screen: add a section `Sxx` with purpose, controls, messages and test cases to chapter 03; add its route to section 2 here.
- Every new rule/check/reconciliation type: chapter 04 (settings), 05 (semantics), the form specs in `ui/editor.py` (`CHECK_SPECS`, `RECON_SPECS`) and the test guide.
- Every behaviour decision: a short ADR in `docs/adr/` and one line in section 5.
- Keep the test IDs stable; add new IDs rather than renumbering.
