"""Reports built from the run log: Excel, HTML and JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_migration_concept.report.collect import DEFAULT_MAX_REJECTS, ReportError, collect_report
from data_migration_concept.report.excel import write_excel
from data_migration_concept.report.html import render_html, write_html

FORMATS = ("xlsx", "html", "json")


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def write_reports(report: dict[str, Any], out_dir: Path, formats: tuple[str, ...]) -> list[Path]:
    """Write `<object>_report.<ext>` for each format into `out_dir` (overwriting)."""
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        raise ReportError(f"unknown format(s): {', '.join(unknown)} (use {', '.join(FORMATS)})")
    writers = {"xlsx": write_excel, "html": write_html, "json": write_json}
    paths = []
    for fmt in formats:
        path = out_dir / f"{report['object']}_report.{fmt}"
        writers[fmt](report, path)
        paths.append(path)
    return paths


__all__ = [
    "DEFAULT_MAX_REJECTS",
    "FORMATS",
    "ReportError",
    "collect_report",
    "render_html",
    "write_excel",
    "write_html",
    "write_json",
    "write_reports",
]
