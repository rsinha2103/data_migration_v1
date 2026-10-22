"""Collect everything a report shows from the run log into one JSON-friendly dict."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from data_migration_concept import __version__
from data_migration_concept.runlog import RunLog

KINDS = ("extract", "transform", "checks", "load", "recon", "profile")
DEFAULT_MAX_REJECTS = 100


class ReportError(Exception):
    """A report cannot be built (unknown run, nothing recorded)."""


def step_kind(step_name: str) -> str:
    head = step_name.split(":", 1)[0]
    return {"xref": "transform"}.get(head, head) if head in (*KINDS, "xref") else "other"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in row.items()}


def _select_runs(
    runlog: RunLog, project_name: str, object_name: str, run_id: str | None
) -> list[dict[str, Any]]:
    """One explicit run, or the newest run of each kind (extract, transform, ...)."""
    if run_id is not None:
        run = runlog.get_run(run_id)
        if run is None:
            raise ReportError(f"unknown run '{run_id}'")
        return [run]
    chosen: dict[str, dict[str, Any]] = {}
    for run in runlog.list_runs(project_name, object_name):
        for step in runlog.get_steps(run["run_id"]):
            kind = step_kind(step["step_name"])
            if kind in KINDS and kind not in chosen:
                chosen[kind] = run
        if len(chosen) == len(KINDS):
            break
    unique = {r["run_id"]: r for r in chosen.values()}
    return sorted(unique.values(), key=lambda r: r["started_at"])


def collect_report(
    runlog: RunLog,
    project_name: str,
    object_name: str,
    *,
    run_id: str | None = None,
    max_rejects: int = DEFAULT_MAX_REJECTS,
) -> dict[str, Any]:
    runs = _select_runs(runlog, project_name, object_name, run_id)
    if not runs:
        raise ReportError(f"nothing recorded yet for object '{object_name}'")

    steps: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    recons: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    profiles: dict[str, list[dict[str, Any]]] = {}

    for run in runs:
        rid = run["run_id"]
        for step in runlog.get_steps(rid):
            step = _row(step)
            step["kind"] = step_kind(step["step_name"])
            steps.append(step)
            if step["kind"] == "profile":
                table = step["step_name"].split(":", 1)[1]
                profiles[table] = [_row(p) for p in runlog.get_profile(rid, table)]
        for check in runlog.get_checks(rid):
            check = _row(check)
            (recons if check["check_type"].startswith("RECON_") else checks).append(check)
            if check["status"] != "PASS" and check["failed_count"]:
                rows = runlog.get_rejects(rid, check["check_name"])
                check["rejects_shown"] = min(len(rows), max_rejects)
                rejects.extend(_row(r) for r in rows[:max_rejects])

    def count(items: list[dict[str, Any]], status: str, severity: str | None = None) -> int:
        return sum(
            1
            for c in items
            if c["status"] == status and (severity is None or c["severity"] == severity)
        )

    def blocking(items: list[dict[str, Any]]) -> int:
        return sum(
            1
            for c in items
            if c["status"] == "ERROR" or (c["status"] == "FAIL" and c["severity"] == "error")
        )

    failed_steps = [s for s in steps if s["status"] == "FAILED"]
    blocked = blocking(checks) + blocking(recons)
    warned = count(checks, "FAIL", "warn") + count(recons, "FAIL", "warn")
    if blocked or any(s["kind"] != "checks" and s["kind"] != "recon" for s in failed_steps):
        overall = "BLOCKED" if blocked else "FAILED"
    else:
        overall = "ATTENTION" if warned else "OK"

    return {
        "tool": f"Data_Migration_Concept {__version__}",
        "generated_at": datetime.now(UTC).isoformat(),
        "project": project_name,
        "object": object_name,
        "overall": overall,
        "summary": {
            "checks_total": len(checks),
            "checks_passed": count(checks, "PASS"),
            "checks_blocking": blocking(checks),
            "checks_warnings": count(checks, "FAIL", "warn"),
            "recons_total": len(recons),
            "recons_passed": count(recons, "PASS"),
            "recons_blocking": blocking(recons),
            "recons_warnings": count(recons, "FAIL", "warn"),
            "failed_steps": len(failed_steps),
        },
        "runs": [_row(r) for r in runs],
        "steps": steps,
        "checks": checks,
        "reconciliations": recons,
        "rejects": rejects,
        "profiles": profiles,
    }
