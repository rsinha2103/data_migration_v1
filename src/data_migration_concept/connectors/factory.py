"""Create SQLAlchemy engines from project connection definitions."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine

from data_migration_concept.config.errors import ConfigError
from data_migration_concept.config.models import ProjectConfig


def resolve_url(project: ProjectConfig, connection: str) -> str:
    """Return the database URL for a named connection, read from its environment variable."""
    conn = project.connections.get(connection)
    if conn is None:
        raise ConfigError([f"unknown connection '{connection}'"])
    url = os.environ.get(conn.url_env)
    if not url:
        raise ConfigError(
            [f"connection '{connection}': environment variable '{conn.url_env}' is not set"]
        )
    return url


def get_engine(project: ProjectConfig, connection: str) -> Engine:
    return create_engine(resolve_url(project, connection))


def get_working_engine(project: ProjectConfig) -> Engine:
    return get_engine(project, project.working_connection)
