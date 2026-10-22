"""Runs the example end to end on PostgreSQL. Skipped unless DMC_TEST_POSTGRES_URL is set,
e.g. postgresql+psycopg://dmc:dmc@localhost:5432/dmc (after `docker compose up -d db`)."""

import os

import pytest
from sqlalchemy import create_engine, text

from data_migration_concept.config import load_project
from data_migration_concept.extract import extract_object
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform import transform_object

URL = os.environ.get("DMC_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL, reason="DMC_TEST_POSTGRES_URL not set"),
]


def test_example_transforms_on_postgres(example_project):
    engine = create_engine(URL)
    log = RunLog(engine)
    log.ensure_schema()
    project = load_project(example_project)
    run_id = log.start_run("p", "supplier")
    extract_object(project, "supplier", engine, log, run_id)
    results = transform_object(project, "supplier", engine, log, run_id, write_build=False)
    assert (results[0].rows_in, results[0].rows_out) == (4, 4)
    with engine.connect() as conn:
        row = conn.execute(
            text('select "COUNTRY", "SEARCH_TERM" from tgt_bp_general where "_dmc_row_no" = 2')
        ).one()
    assert row == ("GB", "Contoso Lo")
