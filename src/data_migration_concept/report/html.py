"""Self-contained HTML report (inline CSS, no scripts). All values are HTML-escaped."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ r.object }} - migration report</title>
<style>
body{font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;margin:24px;color:#1f2933;max-width:1200px}
h1{margin:0 0 4px}h2{margin-top:32px;border-bottom:1px solid #d5dae1;padding-bottom:4px}
.meta{color:#52606d;margin-bottom:16px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid #d5dae1;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#eef2f7}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600;font-size:12px}
.OK,.PASS,.SUCCESS{background:#c6efce;color:#14532d}
.ATTENTION,.WARN{background:#ffeb9c;color:#7a4b00}
.BLOCKED,.FAIL,.FAILED,.ERROR{background:#ffc7ce;color:#8a1c1c}
.cards{display:flex;gap:12px;flex-wrap:wrap}
.card{border:1px solid #d5dae1;border-radius:6px;padding:8px 14px;min-width:150px}
.card b{display:block;font-size:20px}
.num{text-align:right}
</style></head><body>
<h1>{{ r.object }} <span class="badge {{ r.overall }}">{{ r.overall }}</span></h1>
<div class="meta">{{ r.project }} &middot; generated {{ r.generated_at }} &middot; {{ r.tool }}</div>
<div class="cards">
 <div class="card">Checks passed<b>{{ s.checks_passed }} / {{ s.checks_total }}</b></div>
 <div class="card">Checks blocking<b>{{ s.checks_blocking }}</b></div>
 <div class="card">Reconciliations passed<b>{{ s.recons_passed }} / {{ s.recons_total }}</b></div>
 <div class="card">Reconciliations blocking<b>{{ s.recons_blocking }}</b></div>
 <div class="card">Warnings<b>{{ s.checks_warnings + s.recons_warnings }}</b></div>
 <div class="card">Failed steps<b>{{ s.failed_steps }}</b></div>
</div>

<h2>Steps</h2>
<table><tr><th>Run</th><th>Step</th><th>Status</th><th>Rows in</th><th>Rows out</th>
<th>Message</th></tr>
{% for st in r.steps %}<tr><td>{{ st.run_id[:8] }}</td><td>{{ st.step_name }}</td>
<td><span class="badge {{ st.status }}">{{ st.status }}</span></td>
<td class="num">{{ st.rows_in if st.rows_in is not none }}</td>
<td class="num">{{ st.rows_out if st.rows_out is not none }}</td>
<td>{{ st.message or "" }}</td></tr>
{% else %}<tr><td colspan="6">No steps recorded.</td></tr>{% endfor %}</table>

{% for title, items in sections %}
<h2>{{ title }}</h2>
<table><tr><th>Check</th><th>Type</th><th>Structure</th><th>Severity</th><th>Result</th>
<th>Rows checked</th><th>Failures</th><th>Message</th></tr>
{% for c in items %}<tr><td>{{ c.check_name }}</td><td>{{ c.check_type }}</td>
<td>{{ c.structure }}</td><td>{{ c.severity }}</td>
<td><span class="badge {{ c.label }}">{{ c.label }}</span></td>
<td class="num">{{ c.checked_count if c.checked_count is not none }}</td>
<td class="num">{{ c.failed_count }}{{ " (truncated)" if c.truncated }}</td>
<td>{{ c.message or "" }}</td></tr>
{% else %}<tr><td colspan="8">None defined or run.</td></tr>{% endfor %}</table>
{% endfor %}

<h2>Rejects (sample)</h2>
<table><tr><th>Check</th><th>Structure</th><th>Source row</th><th>Field</th><th>Value</th>
<th>Message</th></tr>
{% for x in r.rejects %}<tr><td>{{ x.check_name }}</td><td>{{ x.structure }}</td>
<td class="num">{{ x.row_no if x.row_no is not none }}</td><td>{{ x.field or "" }}</td>
<td>{{ x.value if x.value is not none }}</td><td>{{ x.message or "" }}</td></tr>
{% else %}<tr><td colspan="6">No rejected rows.</td></tr>{% endfor %}</table>

<h2>Profile</h2>
{% for table, cols in profiles %}
<h3>{{ table }}</h3>
<table><tr><th>Column</th><th>Rows</th><th>Empty %</th><th>Distinct</th><th>Min</th><th>Max</th>
<th>Length</th><th>Top values</th><th>Top shapes</th></tr>
{% for p in cols %}<tr><td>{{ p.column_name }}</td><td class="num">{{ p.row_count }}</td>
<td class="num">{{ p.empty_pct }}</td><td class="num">{{ p.distinct_count }}</td>
<td>{{ p.min_value if p.min_value is not none }}</td><td>{{ p.max_value if p.max_value is not none }}</td>
<td>{{ p.min_length if p.min_length is not none }}-{{ p.max_length if p.max_length is not none }}</td>
<td>{{ p.top }}</td><td>{{ p.shapes }}</td></tr>{% endfor %}</table>
{% else %}<p>No profile recorded.</p>{% endfor %}
</body></html>
"""

_env = Environment(autoescape=True)


def _label(check: dict[str, Any]) -> str:
    if check["status"] == "FAIL" and check["severity"] == "warn":
        return "WARN"
    return str(check["status"])


def _fmt(pairs: str | None, n: int = 5) -> str:
    return ", ".join(f"{v} ({c})" for v, c in json.loads(pairs or "[]")[:n])


def render_html(report: dict[str, Any]) -> str:
    def prep(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**c, "label": _label(c)} for c in items]

    profiles = []
    for table, cols in report["profiles"].items():
        rows = []
        for p in cols:
            pct = 100.0 * p["empty_count"] / p["row_count"] if p["row_count"] else 0.0
            rows.append(
                {**p, "empty_pct": f"{pct:.1f}", "top": _fmt(p["top_values"]),
                 "shapes": _fmt(p["top_patterns"])}
            )  # fmt: skip
        profiles.append((table, rows))
    return _env.from_string(TEMPLATE).render(
        r=report,
        s=report["summary"],
        sections=[
            ("Checks", prep(report["checks"])),
            ("Reconciliation", prep(report["reconciliations"])),
        ],
        profiles=profiles,
    )


def write_html(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
