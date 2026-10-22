import pytest
from sqlalchemy import text

from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.connectors import get_working_engine, resolve_url


def test_resolve_url_reads_env_var(example_project, monkeypatch):
    project = load_project(example_project).config
    monkeypatch.setenv("DMC_WORKING_DB_URL", "sqlite://")
    assert resolve_url(project, "working") == "sqlite://"
    with get_working_engine(project).connect() as conn:
        assert conn.execute(text("select 1")).scalar() == 1


def test_missing_env_var_is_a_config_error(example_project, monkeypatch):
    project = load_project(example_project).config
    monkeypatch.delenv("DMC_WORKING_DB_URL", raising=False)
    with pytest.raises(ConfigError, match="DMC_WORKING_DB_URL"):
        resolve_url(project, "working")


def test_unknown_connection(example_project):
    project = load_project(example_project).config
    with pytest.raises(ConfigError, match="unknown connection"):
        resolve_url(project, "ghost")
