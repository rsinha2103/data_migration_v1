# CLAUDE.md — Data_Migration_Concept

Open source data migration workbench: extract → transform → validate → load → reconcile → report. Engine-first (library + `dmc` CLI); a web UI and governance layer are later modules. Decisions are in `docs/adr/`; user-facing docs in `docs/user-guide.md`.

## Commands
- Setup: `uv sync` (uv can be run as `python -m uv` if not on PATH)
- Tests: `uv run pytest` (Postgres test needs `DMC_TEST_POSTGRES_URL`; start DB with `docker compose up -d db`)
- Lint/format: `uv run ruff check .` and `uv run ruff format .`
- Types: `uv run mypy`
- Try it: `uv run dmc demo` (add `--defects` for a failing run)

## Layout
`src/data_migration_concept/`:
- `config/` pydantic models (`models.py`) and the project loader (`loader.py`)
- `connectors/` engines from env-var URLs; `runlog/` run, step, check, reject and profile tables
- `extract/` readers and staging; `transform/` SQL generation, XREF loading, runner
- `checks/` check framework and built-ins; `recon/` reconciliation (reuses the check types)
- `load/` writers and gated load; `profile/` column profiling; `report/` Excel/HTML/JSON
- `demo.py` and `cli.py` (Typer app)

Tests in `tests/`; fixture projects are `examples/acme_supplier` (small, used by most tests) and `examples/acme_demo` (the demo). Fictitious data only.

## Conventions
- Python >=3.12, type hints on public functions, pydantic v2 for config, SQLAlchemy 2.x.
- Simple mapping rules generate ANSI SQL (works on SQLite and PostgreSQL); complex logic is hand-written SQL. Generated SQL is inspectable (`dmc show-sql`) and covered by golden files in `tests/golden/`.
- Secrets only via environment variables, never in YAML or code.
- Every check returns a `CheckResult`; blocking = error-severity FAIL or any ERROR. `dmc load` always runs the checks first and cannot skip them.
- Migration data is untrusted in reports: HTML is escaped, Excel text cells are stored as text.
- New behaviour needs tests: config validation, SQL generation, checks, reconciliation, CLI. SQLite for unit tests, Postgres for integration (`@pytest.mark.postgres`).
- No SAP proprietary content or real client data. Generic table/field names (e.g. `LFA1-LIFNR`) are fine.
- Record significant decisions as short ADRs in `docs/adr/`.
- Small commits with clear messages.
