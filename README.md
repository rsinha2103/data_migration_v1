# Data_Migration_Concept

An open source data migration workbench: **extract, transform, validate, load, reconcile and report**, with data quality checks at every stage.

- Each migration object is YAML plus SQL files you can review and keep in git.
- Simple rules (move, constant, cross-reference, concat, substring) are declarative and generate SQL you can inspect; complex rules are hand-written SQL.
- Reads CSV, Excel, Parquet and any SQL database (through SQLAlchemy); loads files or tables.
- Validation, data-quality and reconciliation checks, with the failing rows traced back to the source row.
- Excel, HTML and JSON reports from a full run history stored in the working database.
- A library plus the `dmc` command line tool. All sample data is fictitious.

Status: v0.1, early development. The engine is complete; a web UI and the governance layer (approvals, defect log) are not built.

## Try it in one command

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) (`pip install uv`).

```bash
uv sync
uv run dmc demo
```

This copies the fictitious Acme project into `dmc_demo/`, runs every step on SQLite and writes reports to `dmc_demo/reports/` (open the `.html`). To see what a failing migration looks like:

```bash
uv run dmc demo --dir dmc_demo_bad --defects
```

## Commands

| Command | Purpose |
|---|---|
| `dmc validate-config <project>` | Validate all project files |
| `dmc extract <project> [object]` | Sources to staging tables |
| `dmc transform <project> [object]` | Staging to target tables (SQL saved in `build/`) |
| `dmc show-sql <project> [object]` | Print the generated SQL |
| `dmc profile <project> [object]` | Column statistics |
| `dmc check <project> [object]` | Validation and data-quality checks |
| `dmc load <project> [object]` | Write files/tables, only if no check blocks |
| `dmc recon <project> [object]` | Source vs target reconciliation |
| `dmc report <project> [object]` | Excel / HTML / JSON reports |
| `dmc demo` | Run the example project end to end |

Set `DMC_WORKING_DB_URL` (see `.env.example`) to the database the working tables should live in, for example `sqlite:///work.db` or a PostgreSQL URL.

Read the [user guide](docs/user-guide.md) to set up your own project. Design decisions are in [docs/adr](docs/adr).

## Development

```bash
uv sync
uv run pytest            # tests (SQLite)
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

PostgreSQL integration test (needs Docker):

```bash
docker compose up -d db
DMC_TEST_POSTGRES_URL=postgresql+psycopg://dmc:dmc@localhost:5432/dmc uv run pytest -m postgres
```

## License

Apache 2.0, see `LICENSE`.
