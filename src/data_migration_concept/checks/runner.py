"""Run the checks of an object that the user has confirmed: the explicit ones in checks.yaml and
the kinds of structure-based rule listed under `from_mapping`. Nothing else is checked."""

from __future__ import annotations

from sqlalchemy import Engine, inspect

from data_migration_concept.checks import builtin
from data_migration_concept.checks.base import Check, CheckContext, CheckResult, CheckRunError
from data_migration_concept.config import models as cfg
from data_migration_concept.config.loader import LoadedObject, LoadedProject
from data_migration_concept.runlog import RunLog, RunStatus
from data_migration_concept.transform.generator import target_table


def derived_checks(obj: LoadedObject) -> list[Check]:
    """The mandatory, length, data-type and key rules the target structure implies, but only
    the kinds the user has confirmed in `from_mapping` (each with its own severity)."""
    confirmed = obj.from_mapping
    checks: list[Check] = []
    if not confirmed:
        return checks
    for st in obj.config.structures:
        mapping = obj.mappings.get(st.name)
        if mapping is None:
            continue
        for fld in mapping.fields:
            if isinstance(fld.rule, cfg.NotMigratedRule):
                continue
            if fld.mandatory and "mandatory" in confirmed:
                checks.append(
                    builtin.MandatoryCheck(
                        st.name,
                        fld.target_field,
                        confirmed["mandatory"],  # type: ignore[arg-type]
                        unmapped=isinstance(fld.rule, cfg.UnmappedRule),
                    )
                )
            if fld.length is not None and "length" in confirmed:
                checks.append(
                    builtin.LengthCheck(
                        st.name,
                        fld.target_field,
                        fld.length,
                        confirmed["length"],  # type: ignore[arg-type]
                    )
                )
            if fld.data_type != "string" and "data_type" in confirmed:
                checks.append(
                    builtin.DataTypeCheck(
                        st.name,
                        fld.target_field,
                        fld.data_type,
                        confirmed["data_type"],  # type: ignore[arg-type]
                    )
                )
        keys = [
            f.target_field
            for f in mapping.fields
            if f.key and not isinstance(f.rule, cfg.NotMigratedRule)
        ]
        if keys and "key" in confirmed:
            checks.append(
                builtin.UniqueCheck(f"key.{st.name}", st.name, keys, confirmed["key"])  # type: ignore[arg-type]
            )
    return checks


def explicit_checks(obj: LoadedObject) -> list[Check]:
    checks: list[Check] = []
    for c in obj.checks:
        if isinstance(c, cfg.UniqueCheck):
            checks.append(builtin.UniqueCheck(c.name, c.structure, c.fields, c.severity))
        elif isinstance(c, cfg.AllowedValuesCheck):
            checks.append(
                builtin.AllowedValuesCheck(c.name, c.structure, c.field, c.values, c.severity)
            )
        elif isinstance(c, cfg.ReferentialCheck):
            checks.append(
                builtin.ReferentialCheck(
                    c.name,
                    c.structure,
                    c.field,
                    c.ref_structure,
                    c.ref_field,
                    c.ref_object,
                    c.severity,
                )
            )
        elif isinstance(c, cfg.NullRateCheck):
            checks.append(
                builtin.NullRateCheck(c.name, c.structure, c.field, c.max_pct, c.severity)
            )
        elif isinstance(c, cfg.PatternCheck):
            checks.append(builtin.PatternCheck(c.name, c.structure, c.field, c.pattern, c.severity))
        elif isinstance(c, cfg.RangeCheck):
            checks.append(
                builtin.RangeCheck(c.name, c.structure, c.field, c.min, c.max, c.severity)
            )
        elif isinstance(c, cfg.OutlierCheck):
            checks.append(
                builtin.OutlierCheck(c.name, c.structure, c.field, c.max_zscore, c.severity)
            )
        else:
            checks.append(builtin.SqlAssertCheck(c.name, c.structure, c.sql, c.severity))
    return checks


def build_checks(obj: LoadedObject) -> list[Check]:
    return derived_checks(obj) + explicit_checks(obj)


def blocking_failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.blocking]


def run_checks(
    project: LoadedProject,
    object_name: str,
    engine: Engine,
    runlog: RunLog,
    run_id: str,
) -> list[CheckResult]:
    """Run the confirmed checks, store results and rejects in the run log, and return the
    results. With no confirmed check nothing runs: an empty list is returned and no step is
    recorded (so nothing can block a load either)."""
    obj = project.objects.get(object_name)
    if obj is None:
        known = ", ".join(sorted(project.objects)) or "none"
        raise CheckRunError(f"unknown object '{object_name}' (known: {known})")
    checks = build_checks(obj)
    if not checks:
        return []

    tables = {st.name: target_table(project, st) for st in obj.config.structures}
    existing = set(inspect(engine).get_table_names())
    missing = sorted(t for t in tables.values() if t not in existing)
    if missing:
        raise CheckRunError(f"target table(s) not found: {', '.join(missing)}; run transform first")

    ctx = CheckContext(engine=engine, project=project, obj=obj, target_tables=tables)
    return execute_and_record(checks, ctx, runlog, run_id, "checks")


def execute_and_record(
    checks: list[Check], ctx: CheckContext, runlog: RunLog, run_id: str, step_name: str
) -> list[CheckResult]:
    """Run checks as one run-log step; store each result and its rejects. The step is marked
    FAILED when anything blocks."""
    results: list[CheckResult] = []
    step_id = runlog.start_step(run_id, step_name)
    try:
        for check in checks:
            result = check.run(ctx)
            results.append(result)
            runlog.record_check(
                run_id,
                check_name=result.name,
                check_type=result.check_type,
                structure=result.structure,
                field=result.field,
                severity=result.severity,
                status=result.status.value,
                checked_count=result.checked_count,
                failed_count=result.failed_count,
                truncated=result.truncated,
                message=result.message,
            )
            runlog.record_rejects(
                run_id,
                [
                    {
                        "check_name": result.name,
                        "structure": result.structure,
                        "severity": result.severity,
                        "row_no": f.row_no,
                        "field": f.field,
                        "value": f.value,
                        "message": f.message,
                    }
                    for f in result.failures
                ],
            )
    except Exception as exc:
        runlog.finish_step(step_id, RunStatus.FAILED, message=f"{type(exc).__name__}: {exc}")
        raise
    blocking = blocking_failures(results)
    failed = sum(1 for r in results if r.failed_count or r.status.value == "ERROR")
    runlog.finish_step(
        step_id,
        RunStatus.FAILED if blocking else RunStatus.SUCCESS,
        rows_in=len(results),
        rows_out=len(results) - failed,
        message=f"{len(results)} {step_name}: {failed} not passed, {len(blocking)} blocking",
    )
    return results
