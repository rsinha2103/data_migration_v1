# 02 - Installation and running

## 1. Requirements

| Item | Requirement |
|---|---|
| Python | 3.12 or newer (tested on 3.12 and 3.14 in CI) |
| Package manager | [uv](https://docs.astral.sh/uv/). If `uv` is not on the PATH, use `python -m uv ...` instead (this is how it is run on the development machine) |
| Working database | SQLite (nothing to install) or PostgreSQL 16 (recommended for real work; `docker compose up -d db` starts one) |
| Browser | any current browser; the UI is plain server-rendered HTML with a little JavaScript |
| Operating systems | developed on Windows 11; CI runs on Ubuntu |

Runtime libraries (from `pyproject.toml`): SQLAlchemy >= 2.0, polars, typer, pydantic >= 2.7, PyYAML, Jinja2, sqlglot, openpyxl, FastAPI, uvicorn, python-multipart. Optional extra `postgres`: `psycopg[binary]`.

## 2. Install

```bash
git clone https://github.com/rsinha2103/data_migration_v1.git
cd data_migration_v1
uv sync
```

`uv sync` creates `.venv` and installs the product and the development tools (pytest, ruff, mypy, httpx, psycopg). Run everything through `uv run ...` (or activate `.venv`).

## 3. Quick tour without any setup

```bash
uv run dmc demo
```

Copies the fictitious **Acme** project into `dmc_demo/`, uses a SQLite file `dmc_demo/work.db`, and runs extract, transform, check, load, reconcile, profile and report for both objects (supplier, material). It prints, per object, the overall status, checks passed, loaded files, reconciliations passed and the report path.

```bash
uv run dmc demo --defects --dir dmc_demo_bad --force
```

Seeds realistic data problems (duplicate key, missing name, non-numeric key, bank account of an unknown supplier, weight out of range). The checks then **block the load** and the reports show the rejected rows.

| Option | Meaning |
|---|---|
| `--dir` | where to create the demo (default `dmc_demo`) |
| `--defects` | seed data problems |
| `--force` | replace an earlier demo folder (only if it contains a `project.yaml`) |
| `--source` | copy another example project (default `examples/acme_demo`) |

## 4. Start the web interface

```bash
uv run dmc ui
```

| Option | Default | Meaning |
|---|---|---|
| `--workspace` | `dmc_workspace` | folder where projects are kept |
| `--port` | `8765` | port (localhost only) |
| `--open` / `--no-open` | `--open` | open the browser after 1.2 s |

The console prints the workspace and the URL, e.g. `http://localhost:8765`. Press **Ctrl+C** to stop.

**If the port is busy** (an earlier `dmc ui` still running), stop the old process first; otherwise you may see an old version of the pages. On Windows PowerShell:

```powershell
Get-NetTCPConnection -LocalPort 8765 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## 5. The working database

Every step reads and writes a **working database**. How it is chosen:

| Situation | Working database |
|---|---|
| **UI**, environment variable `DMC_WORKING_DB_URL` **not set** | a SQLite file `work.db` inside the project folder (the UI sets the variable for the duration of each operation and removes it afterwards) |
| **UI**, `DMC_WORKING_DB_URL` set in the shell | that database. The project page shows "Working database: environment variable DMC_WORKING_DB_URL". |
| **CLI** | the environment variable named by `working_connection` in `project.yaml` (normally `DMC_WORKING_DB_URL`) **must be set**; otherwise the command stops with "environment variable 'DMC_WORKING_DB_URL' is not set". |

Examples of URLs:

```powershell
$env:DMC_WORKING_DB_URL = "sqlite:///C:/work/migration.db"
$env:DMC_WORKING_DB_URL = "postgresql+psycopg://dmc:dmc@localhost:5432/dmc"
```

```bash
export DMC_WORKING_DB_URL="postgresql+psycopg://dmc:dmc@localhost:5432/dmc"
docker compose up -d db        # PostgreSQL 16, user/password/db = dmc/dmc/dmc, port 5432
```

Database **sources** and database **load destinations** use their own connections, each defined in `project.yaml` with an environment variable (see chapter 04).

## 5a. Supported SQL dialects

| Dialect | Status |
|---|---|
| SQLite | native; used by the UI and unit tests |
| PostgreSQL | native; integration-tested in CI |
| MySQL/MariaDB, SQL Server, Oracle | best effort: the generated SQL is translated with SQLGlot (`dmc show-sql --dialect ...`). Not covered by automated tests. |

## 6. Run the tests

```bash
uv run pytest                    # 330+ tests; the PostgreSQL test is skipped without DMC_TEST_POSTGRES_URL
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
uv run mypy                      # types
```

PostgreSQL integration test:

```powershell
docker compose up -d db
$env:DMC_TEST_POSTGRES_URL = "postgresql+psycopg://dmc:dmc@localhost:5432/dmc"
uv run pytest -m postgres
```

CI (`.github/workflows`) runs lint, format check, mypy and pytest on Python 3.12 and 3.14 with a PostgreSQL 16 service.

## 7. Repository layout

| Path | Content |
|---|---|
| `src/data_migration_concept/` | the product |
| `tests/` | automated tests; `tests/golden/*.sql` are golden files for generated SQL |
| `examples/acme_demo/` | the demo project (two objects, fictitious data) |
| `examples/acme_supplier/` | a small project used by most unit tests |
| `docs/` | `user-guide.md`, `adr/` (decision records), `product-documentation/` (this documentation) |
| `.env.example` | example environment variables |
| `docker-compose.yml` | PostgreSQL for integration tests |
| `CLAUDE.md` | development conventions |

## 8. Upgrading and removing

- There is no installer or database migration: a project is a folder of text files; the run log tables are created on demand (`ensure_schema`).
- To remove a UI project from the list use **Remove from list** (only for projects added by path); the folder is never deleted by the product.
