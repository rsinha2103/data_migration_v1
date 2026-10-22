# ADR 0001: Engine-first, with hybrid transformations

Status: accepted

## Context
The original idea was a full governance web application. The core value is the migration engine itself: extract, transform, validate, load, reconcile and report.

## Decision
- Build a Python library and `dmc` CLI first; a web UI comes later.
- Connect to files and any SQL database through SQLAlchemy.
- Transformations are hybrid: simple rules (move, constant, XRef, concat, substring, not-migrated) are declarative and generate SQL; complex rules are hand-written SQL files.
- Migration objects are YAML + SQL files kept in git.

## Consequences
Generated SQL can be inspected and reviewed, so documentation and logic stay aligned. SQL dialect differences are handled with sqlglot; Postgres and SQLite are guaranteed, other databases are best effort.
