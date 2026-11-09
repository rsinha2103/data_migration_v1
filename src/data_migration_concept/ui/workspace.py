"""Workspace: the folder of projects the UI works on, and a safe way to open one."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import Engine

from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.config.loader import LoadedProject
from data_migration_concept.connectors import get_working_engine
from data_migration_concept.demo import DemoError, default_example_dir, prepare_target
from data_migration_concept.runlog import RunLog

PROJECT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
REGISTRY = ".dmc_projects.json"
DEFAULT_DB_ENV = "DMC_WORKING_DB_URL"

# Steps change process-wide state (the working-database environment variable), so the UI runs
# one project operation at a time. It is a single-user local tool.
_LOCK = threading.RLock()


class WorkspaceError(Exception):
    """A request that cannot be honoured; the message is safe to show to the user."""


@dataclass
class ProjectInfo:
    name: str
    path: Path
    external: bool
    title: str = ""
    objects: int = 0
    error: str | None = None


@dataclass
class Ctx:
    """An opened project: parsed config, working-database engine and run log."""

    path: Path
    project: LoadedProject
    engine: Engine
    runlog: RunLog
    database: str  # human-readable description of the working database


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # --- registry of projects kept outside the workspace folder ---------------------

    def _external(self) -> list[Path]:
        reg = self.root / REGISTRY
        if not reg.is_file():
            return []
        try:
            return [Path(p) for p in json.loads(reg.read_text(encoding="utf-8"))]
        except (ValueError, OSError):
            return []

    def _save_external(self, paths: list[Path]) -> None:
        (self.root / REGISTRY).write_text(
            json.dumps([str(p) for p in paths], indent=2), encoding="utf-8"
        )

    # --- listing ---------------------------------------------------------------------

    def list_projects(self) -> list[ProjectInfo]:
        found: dict[str, ProjectInfo] = {}
        candidates = [(p, False) for p in sorted(self.root.iterdir()) if p.is_dir()]
        candidates += [(p, True) for p in self._external() if p.is_dir()]
        for path, external in candidates:
            if not (path / "project.yaml").is_file() or path.name in found:
                continue
            info = ProjectInfo(name=path.name, path=path.resolve(), external=external)
            try:
                loaded = load_project(path)
                info.title = loaded.config.name
                info.objects = len(loaded.objects)
            except ConfigError as exc:
                info.error = exc.errors[0] if exc.errors else "invalid project"
            found[path.name] = info
        return list(found.values())

    def project_path(self, name: str) -> Path:
        for info in self.list_projects():
            if info.name == name:
                return info.path
        raise WorkspaceError(f"Unknown project '{name}'.")

    # --- creating --------------------------------------------------------------------

    def _check_new_name(self, name: str) -> Path:
        if not PROJECT_NAME.match(name):
            raise WorkspaceError(
                "Project names start with a letter and may contain letters, digits, "
                "'_' and '-' (up to 64 characters)."
            )
        target = self.root / name
        if target.exists() or any(i.name == name for i in self.list_projects()):
            raise WorkspaceError(f"A project called '{name}' already exists.")
        return target

    def create_project(self, name: str, title: str = "") -> Path:
        target = self._check_new_name(name)
        (target / "objects").mkdir(parents=True)
        config = {
            "name": title.strip() or name,
            "description": "",
            "working_connection": "working",
            "connections": {"working": {"url_env": DEFAULT_DB_ENV}},
        }
        (target / "project.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return target

    def create_demo(self, name: str, *, defects: bool) -> Path:
        target = self._check_new_name(name)
        try:
            prepare_target(default_example_dir(), target, force=False, defects=defects)
        except DemoError as exc:
            raise WorkspaceError(str(exc)) from exc
        return target

    def add_existing(self, folder: str) -> ProjectInfo:
        path = Path(folder.strip().strip('"')).expanduser()
        if not (path / "project.yaml").is_file():
            raise WorkspaceError("That folder has no project.yaml.")
        path = path.resolve()
        known = {i.path for i in self.list_projects()}
        if path in known:
            raise WorkspaceError("That project is already listed.")
        self._save_external([*self._external(), path])
        for info in self.list_projects():
            if info.path == path:
                return info
        raise WorkspaceError("Could not add the project.")  # pragma: no cover

    def forget(self, name: str) -> None:
        """Remove an external project from the list (the folder is never deleted)."""
        info = next((i for i in self.list_projects() if i.name == name), None)
        if info is None or not info.external:
            raise WorkspaceError("Only projects added by path can be removed from the list.")
        self._save_external([p for p in self._external() if p.resolve() != info.path])


@contextmanager
def open_project(path: Path) -> Iterator[Ctx]:
    """Open a project for one operation. Holds the global lock; points the working-database
    variable at a SQLite file in the project folder unless the shell already set it."""
    with _LOCK:
        project = load_project(path)
        conn = project.config.connections[project.config.working_connection]
        previous = os.environ.get(conn.url_env)
        if previous is None:
            db_file = (path / "work.db").resolve()
            os.environ[conn.url_env] = f"sqlite:///{db_file.as_posix()}"
            database = f"SQLite file {db_file}"
        else:
            database = f"environment variable {conn.url_env}"
        engine = get_working_engine(project.config)
        runlog = RunLog(engine)
        runlog.ensure_schema()
        try:
            yield Ctx(path.resolve(), project, engine, runlog, database)
        finally:
            engine.dispose()
            if previous is None:
                os.environ.pop(conn.url_env, None)
