import pytest
from sqlalchemy import create_engine, inspect

from data_migration_concept.runlog import RunLog, RunStatus


@pytest.fixture
def runlog() -> RunLog:
    log = RunLog(create_engine("sqlite://"))
    log.ensure_schema()
    return log


def test_schema_has_run_tables(runlog):
    assert {"dmc_run", "dmc_run_step"} <= set(inspect(runlog.engine).get_table_names())


def test_successful_run_records_steps_and_counts(runlog):
    run_id = runlog.start_run("proj", "supplier")
    with runlog.step(run_id, "extract") as rec:
        rec.rows_in = 4
        rec.rows_out = 4
    runlog.finish_run(run_id, RunStatus.SUCCESS)

    run = runlog.get_run(run_id)
    assert run["status"] == "SUCCESS" and run["finished_at"] is not None
    steps = runlog.get_steps(run_id)
    assert [(s["step_name"], s["status"], s["rows_out"]) for s in steps] == [
        ("extract", "SUCCESS", 4)
    ]


def test_failing_step_is_recorded_and_reraised(runlog):
    run_id = runlog.start_run("proj", "supplier")
    with pytest.raises(ValueError, match="boom"), runlog.step(run_id, "transform"):
        raise ValueError("boom")
    step = runlog.get_steps(run_id)[0]
    assert step["status"] == "FAILED"
    assert "ValueError: boom" in step["message"]


def test_unknown_run_returns_none(runlog):
    assert runlog.get_run("missing") is None
