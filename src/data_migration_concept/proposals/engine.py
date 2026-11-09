"""Work out what could be added to an object: field mappings, value lists, checks,
reconciliations and load destinations. Pure functions over `ObjectFacts`; nothing is applied."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from data_migration_concept.checks.builtin import TYPE_VALIDATORS
from data_migration_concept.profile.profile import ColumnProfile
from data_migration_concept.proposals.catalog import (
    DOMAINS,
    NAME_CHECKS,
    SYNONYMS,
    domain_for_field,
)
from data_migration_concept.proposals.model import (
    KINDS,
    FieldFacts,
    ObjectFacts,
    Proposal,
    StructureFacts,
    identifier,
    proposal_id,
)

MAX_ALLOWED_VALUES = 12
MAX_TOTALS_PER_STRUCTURE = 3
CONFIDENCE_ORDER = {"high": 0, "medium": 1}


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _active(structure: StructureFacts) -> list[FieldFacts]:
    return [f for f in structure.fields if f.rule.get("type") != "NOT_MIGRATED"]


def _profile(facts: ObjectFacts, st: StructureFacts, column: str | None) -> ColumnProfile | None:
    """The staged column a mapping reference points at (own source or a joined one)."""
    if column is None:
        return None
    source, name = st.locate(column)
    return facts.profiles.get(source, {}).get(name)


def _rule_source(field: FieldFacts) -> str | None:
    return field.rule.get("source_field") if field.rule.get("type") == "MOVE" else None


# --- mappings ---------------------------------------------------------------------------


def _value_based_column(
    facts: ObjectFacts, st: StructureFacts, f: FieldFacts, columns: list[str]
) -> str | None:
    """For a country/currency/unit/language field: the one source column whose values are
    (almost) all recognised as such. None when no column, or more than one, fits."""
    domain = domain_for_field(f.name)
    profiles = facts.profiles.get(st.source, {})
    if domain is None or not profiles:
        return None
    scores: list[tuple[float, str]] = []
    for col in columns:
        p = profiles.get(col)
        if p is None or p.distinct_count == 0 or p.distinct_count > 300 or (p.max_length or 0) > 30:
            continue
        values = [v.strip() for v in (facts.distinct(st.source, col) or []) if v.strip()]
        if not values:
            continue
        share = sum(1 for v in values if domain.resolve(v)) / len(values)
        if share >= 0.9:
            scores.append((share, col))
    scores.sort(reverse=True)
    if not scores or (len(scores) > 1 and scores[0][0] == scores[1][0]):
        return None
    return scores[0][1]


def mapping_proposals(facts: ObjectFacts) -> list[Proposal]:
    out: list[Proposal] = []
    for st in facts.structures:
        columns = facts.source_columns.get(st.source, [])
        if st.custom_sql or not columns:
            continue
        squashed: dict[str, list[str]] = {}
        for c in columns:
            squashed.setdefault(_squash(c), []).append(c)
        joined_sq: dict[str, list[str]] = {}
        for js in st.joins:
            for c in facts.source_columns.get(js, []):
                joined_sq.setdefault(_squash(c), []).append(f"{js}.{c}")
        used = {_rule_source(f) for f in st.fields}
        # value-based: one field per kind of coded value, and none when a field of that kind is already mapped
        claimed = {
            d.key
            for f in st.fields
            if f.rule.get("type") != "UNMAPPED" and (d := domain_for_field(f.name))
        }
        for f in st.fields:
            if f.rule.get("type") != "UNMAPPED":
                continue
            base = _squash(f.name)
            column, confidence, why = None, "high", ""
            exact = squashed.get(base, [])
            alternatives = [c for s in SYNONYMS.get(base, ()) for c in squashed.get(s, [])]
            if len(exact) == 1:
                column = exact[0]
                why = f"The source column {column} has the same name as the target field."
            elif not exact and len(alternatives) == 1:
                column, confidence = alternatives[0], "medium"
                why = f"{column} is a common name for {f.name} in legacy systems."
            elif not exact and not alternatives and not re.search(r"\d$", f.name):
                # similar spelling, but never for numbered copies such as SMTP_ADDR_2
                near = difflib.get_close_matches(base, list(squashed), n=1, cutoff=0.86)
                if near and len(squashed[near[0]]) == 1:
                    column, confidence = squashed[near[0]][0], "medium"
                    why = f"The source column {column} has a very similar name."
            if column is None and joined_sq:
                found = joined_sq.get(base, [])
                alt = [c for s in SYNONYMS.get(base, ()) for c in joined_sq.get(s, [])]
                if len(found) == 1 or (not found and len(alt) == 1):
                    column, confidence = (found or alt)[0], "medium"
                    why = (
                        f"{column} (a joined source) has the same name as the target field."
                        if found
                        else f"{column} is a common name for {f.name} in legacy systems."
                    )
            if column is None and not re.search(r"\d$", f.name):
                domain = domain_for_field(f.name)
                if domain is not None and domain.key not in claimed:
                    column = _value_based_column(facts, st, f, columns)
                    if column is not None:
                        claimed.add(domain.key)
                        confidence = "medium"
                        why = (
                            f"The values in {column} look like {domain.title} codes "
                            f"(at least 90% are recognised)."
                        )
            if column is None:
                continue
            notes = [f"{column} is already used by another target field."] if column in used else []
            out.append(
                Proposal(
                    proposal_id("mapping", st.name, f.name),
                    "mapping",
                    st.name,
                    f"Fill {f.name} from {column}",
                    why,
                    confidence,
                    "your data" if "look like" in why else "target structure",
                    f"MOVE {column}",
                    {"field": f.name, "rule": {"type": "MOVE", "source_field": column}},
                    notes,
                )  # fmt: skip
            )
    return out


# --- value lists ------------------------------------------------------------------------


def xref_proposals(facts: ObjectFacts) -> list[Proposal]:
    by_domain: dict[str, list[tuple[StructureFacts, FieldFacts, str]]] = {}
    for st in facts.structures:
        if st.custom_sql:
            continue
        for f in st.fields:
            column = _rule_source(f)
            domain = domain_for_field(f.name)
            if column and domain:
                by_domain.setdefault(domain.key, []).append((st, f, column))
    out: list[Proposal] = []
    for domain in DOMAINS:
        items = by_domain.get(domain.key)
        if not items or domain.key in facts.xrefs:
            continue
        seen: dict[str, None] = {}
        observed = False
        for st, _f, column in items:
            values = facts.distinct(*st.locate(column))
            if values is not None:
                observed = True
                for v in values:
                    if v.strip():
                        seen.setdefault(v.strip())
        rows: list[tuple[str, str]] = []
        unknown: list[str] = []
        if observed:
            for value in seen:
                target = domain.resolve(value)
                if target is None:
                    unknown.append(value)
                else:
                    rows.append((value, target))
        else:
            rows = domain.universe()
        if not rows:
            continue
        names = ", ".join(f"{s.name}.{f.name}" for s, f, _ in items)
        notes = [domain.note] if domain.note else []
        if unknown:
            shown = ", ".join(unknown[:15]) + (" ..." if len(unknown) > 15 else "")
            notes.append(
                f"Not recognised, so left out: {shown}. The transform will stop and list them "
                "until you add them yourself."
            )
        if not observed:
            notes.append(
                "No data has been extracted yet, so this list holds all standard values. "
                "Run Extract to get a list based on the values in your data."
            )
        out.append(
            Proposal(
                proposal_id("xref", "", domain.key),
                "xref",
                items[0][0].name,
                f"Translate {names} through a {domain.title} list",
                f"These fields hold {domain.title} codes. A value list makes sure only valid, "
                "agreed values reach the target, and stops the transform on anything unknown.",
                "high" if observed and not unknown else "medium",
                "your data" if observed else "library",
                f"list '{domain.key}' with {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}; "
                f"{len(items)} field(s) switch from MOVE to XREF",
                {
                    "xref": domain.key,
                    "rows": rows,
                    "fields": [
                        {"structure": s.name, "field": f.name, "source_field": c}
                        for s, f, c in items
                    ],
                },
                notes,
            )  # fmt: skip
        )
    return out


# --- checks -----------------------------------------------------------------------------


def _regex_from_shape(shape: str) -> str:
    """'9999-AA' -> '\\d{4}\\-[A-Z]{2}' (digits, capitals, lower case; other characters literal)."""
    parts: list[str] = []
    for char, group in _runs(shape):
        n = len(group)
        base = {"9": r"\d", "A": "[A-Z]", "a": "[a-z]"}.get(char) or re.escape(char)
        parts.append(base if n == 1 else f"{base}{{{n}}}")
    return "".join(parts)


def _runs(text: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    for ch in text:
        if runs and runs[-1][0] == ch:
            runs[-1] = (ch, runs[-1][1] + ch)
        else:
            runs.append((ch, ch))
    return runs


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def _entry(
    structure: str, field: str, short: str, type_: str, settings: dict[str, Any], severity: str,
    why: str,
) -> dict[str, Any]:  # fmt: skip
    entry: dict[str, Any] = {
        "name": identifier(f"{structure}_{field}_{short}"),
        "structure": structure,
        "type": type_,
        **settings,
    }
    if severity == "warn":
        entry["severity"] = "warn"
    entry["description"] = why
    return entry


@dataclass
class Impact:
    """What a check would find in the staged data as it is today."""

    failing: int
    total: int
    samples: list[str]


def _impact(
    facts: ObjectFacts, st: StructureFacts, f: FieldFacts, entry: dict[str, Any]
) -> Impact | None:
    """Test-run a proposed check on the staged values of the source column the field is copied
    from. None when that cannot be known (not extracted, or the field is not a plain MOVE)."""
    column = _rule_source(f)
    profile = _profile(facts, st, column)
    counts = facts.value_counts(*st.locate(column)) if column else None
    if profile is None or counts is None:
        return None
    total = profile.row_count
    kind = entry["type"]
    if kind == "NULL_RATE":
        over = total and 100.0 * profile.empty_count / total > float(entry["max_pct"])
        return Impact(profile.empty_count if over else 0, total, [])
    if kind == "PATTERN":
        pattern = re.compile(entry["pattern"])
        bad = [(v, n) for v, n in counts if v.strip() and not pattern.fullmatch(v)]
    elif kind == "ALLOWED_VALUES":
        allowed = {str(v) for v in entry["values"]}
        bad = [(v, n) for v, n in counts if v.strip() and v not in allowed]
    elif kind == "RANGE":
        low, high = entry.get("min"), entry.get("max")
        bad = []
        for v, n in counts:
            number = _number(v)
            if v.strip() and (
                number is None
                or (low is not None and number < low)
                or (high is not None and number > high)
            ):
                bad.append((v, n))
    else:
        return None
    return Impact(sum(n for _, n in bad), total, [v for v, _ in bad[:3]])


def check_proposals(facts: ObjectFacts) -> list[Proposal]:
    existing = {(c.get("structure"), c.get("field"), c.get("type")) for c in facts.checks}
    existing |= {
        (c.get("structure"), ",".join(c.get("fields", [])), "UNIQUE") for c in facts.checks
    }
    taken = {c.get("name") for c in facts.checks}
    out: list[Proposal] = []

    def add(st: StructureFacts, fields: list[str], entries: list[dict[str, Any]], *, key: str,
            origin: str, conf: str, title: str, why: str, summary: str,
            notes: list[str] | None = None) -> None:  # fmt: skip
        fresh = [
            e for f, e in zip(fields, entries, strict=True)
            if (st.name, f, e["type"]) not in existing and e["name"] not in taken
        ]  # fmt: skip
        if not fresh:
            return
        for f, e in zip(fields, entries, strict=True):
            existing.add((st.name, f, e["type"]))
            taken.add(e["name"])
        out.append(
            Proposal(
                proposal_id("check", st.name, key),
                "check",
                st.name,
                title,
                why,
                conf,
                origin,
                summary,
                {"entries": fresh},
                notes or [],
            )  # fmt: skip
        )

    def add_one(st: StructureFacts, f: str, entry: dict[str, Any], **kw: Any) -> None:
        add(st, [f], [entry], key=entry["name"], **kw)

    for st in facts.structures:
        if st.custom_sql:
            continue
        unique_added = bool(any(f.key for f in st.fields))
        library: dict[str, tuple[Any, list[tuple[str, dict[str, Any], FieldFacts]]]] = {}
        filled: list[tuple[str, dict[str, Any]]] = []
        for f in _active(st):
            covered = False
            for rule in NAME_CHECKS:
                if rule.field_names.match(f.name):
                    entry = _entry(
                        st.name, f.name, rule.key, rule.type,
                        {"field": f.name, **rule.settings}, rule.severity, rule.why,
                    )  # fmt: skip
                    library.setdefault(rule.key, (rule, []))[1].append((f.name, entry, f))
                    covered = True
            column = _rule_source(f)
            profile = _profile(facts, st, column)
            if profile is None or column is None:
                continue
            present = profile.row_count - profile.empty_count
            is_domain = domain_for_field(f.name) is not None
            if (
                not covered and not is_domain and profile.row_count >= 10
                and 2 <= profile.distinct_count <= min(MAX_ALLOWED_VALUES, profile.row_count // 2)
            ):  # fmt: skip
                values = facts.distinct(*st.locate(column))
                if values and len(values) <= MAX_ALLOWED_VALUES:
                    clean = sorted({v.strip() for v in values if v.strip()})
                    entry = _entry(st.name, f.name, "allowed", "ALLOWED_VALUES", {"field": f.name, "values": clean}, "warn",
                                   f"{f.name} only holds {len(clean)} different values in the data.")  # fmt: skip
                    add_one(st, f.name, entry, origin="your data", conf="medium",
                        title=f"{f.name}: only allow the {len(clean)} values found",
                        why=f"Your data has just {len(clean)} different values for this field "
                            f"({', '.join(clean[:6])}{' ...' if len(clean) > 6 else ''}). "
                            "A new value later may be a mistake.",
                        summary=f"ALLOWED_VALUES {', '.join(clean)} (warn)")  # fmt: skip
                    covered = True
            shape = profile.top_patterns[0] if profile.top_patterns else None
            if (
                not covered and not is_domain and shape and present >= 3
                and shape[1] == present and profile.distinct_count > 1 and len(shape[0]) <= 40
            ):  # fmt: skip
                regex = _regex_from_shape(shape[0])
                entry = _entry(st.name, f.name, "format", "PATTERN", {"field": f.name, "pattern": regex}, "warn",
                               f"Every value of {f.name} in the data has the same layout ({shape[0]}).")  # fmt: skip
                add_one(st, f.name, entry, origin="your data", conf="medium",
                    title=f"{f.name}: keep the layout {shape[0]}",
                    why=f"All {present} filled values have the same layout "
                        "(9 = digit, A = capital, a = lower case).",
                    summary=f"PATTERN {regex} (warn)")  # fmt: skip
            if not f.mandatory and profile.empty_count == 0 and profile.row_count >= 5:
                entry = _entry(st.name, f.name, "filled", "NULL_RATE", {"field": f.name, "max_pct": 5}, "warn",
                               f"{f.name} is filled in every row of the data.")  # fmt: skip
                filled.append((f.name, entry))
            low = _number(profile.min_value)
            if f.data_type in ("integer", "decimal") and low is not None and low >= 0:
                entry = _entry(st.name, f.name, "not_negative", "RANGE", {"field": f.name, "min": 0}, "warn",
                               f"No negative {f.name} in the data.")  # fmt: skip
                add_one(st, f.name, entry, origin="your data", conf="medium",
                    title=f"{f.name}: no negative values",
                    why="There are no negative values in your data.", summary="RANGE min 0 (warn)")  # fmt: skip
            if (
                not unique_added and not f.key and profile.row_count >= 5
                and profile.distinct_count == present == profile.row_count
            ):  # fmt: skip
                entry = _entry(st.name, f.name, "unique", "UNIQUE", {"fields": [f.name]}, "warn",
                               f"Every value of {f.name} is different in the data.")  # fmt: skip
                add_one(st, f.name, entry, origin="your data", conf="medium",
                    title=f"{f.name}: values should be unique",
                    why="Every row has a different value. If this identifies a record, a "
                        "duplicate would be an error. You can also mark the field as a key "
                        "in the mapping.",
                    summary="UNIQUE (warn)")  # fmt: skip
                unique_added = True
        if filled:
            names = [n for n, _ in filled]
            shown = ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
            add(
                st, names, [e for _, e in filled], key="filled", origin="your data", conf="medium",
                title=(
                    f"{names[0]}: warn if it starts to be left empty"
                    if len(names) == 1
                    else f"{len(names)} fields are filled in every row: warn if they start to be left empty ({shown})"
                ),
                why="These fields are filled in every row of your data, so empty values later "
                "would be unusual.",
                summary=f"NULL_RATE max 5% (warn) on {len(names)} field(s)",
            )  # fmt: skip
        for rule, items in library.values():
            names = [n for n, _, _ in items]
            heads_up: list[str] = []
            for name, entry, fld in items:
                impact = _impact(facts, st, fld, entry)
                if impact is not None and impact.failing:
                    # never block the load on a rule the data already breaks today
                    entry["severity"] = "warn"
                    e_g = f" (e.g. {', '.join(impact.samples)})" if impact.samples else ""
                    heads_up.append(
                        f"Heads-up: with your current data {name} would flag {impact.failing} of "
                        f"{impact.total} rows{e_g}. It is proposed as a warning so it does not "
                        "block your load; make it an error once the data is clean."
                    )
            shown = ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
            label = rule.key.replace("_", " ").capitalize()
            title = (
                f"{names[0]}: {rule.why.rstrip('.')}"
                if len(items) == 1
                else f"{label} for {len(items)} fields ({shown})"
            )
            severities = {e.get("severity", "error") for _, e, _ in items}
            add(
                st,
                names,
                [e for _, e, _ in items],
                key=rule.key,
                origin="library",
                conf="medium" if heads_up else rule.confidence,
                title=title,
                why=rule.why,
                summary=f"{rule.type} {rule.settings.get('pattern', '')} "
                f"({'/'.join(sorted(severities))}) on {len(items)} field(s)",
                notes=heads_up,
            )
    return out


# --- reconciliations --------------------------------------------------------------------


def recon_proposals(facts: ObjectFacts) -> list[Proposal]:
    have = {(r.get("structure"), r.get("type"), r.get("target_field")) for r in facts.recons}
    taken = {r.get("name") for r in facts.recons}
    out: list[Proposal] = []

    def add(st: StructureFacts, entry: dict[str, Any], title: str, why: str, summary: str) -> None:
        marker = (st.name, entry["type"], entry.get("target_field"))
        if marker in have or entry["name"] in taken:
            return
        have.add(marker)
        taken.add(entry["name"])
        out.append(
            Proposal(
                proposal_id("recon", st.name, entry["name"]),
                "recon",
                st.name,
                title,
                why,
                "high",
                "target structure",
                summary,
                {"entries": [entry]},
            )  # fmt: skip
        )

    for st in facts.structures:
        if st.custom_sql:
            continue
        name = identifier(st.name)
        if st.unions:  # the row count covers all row sets; key/value recons read the main one only
            add(st, {"name": f"{name}_row_count", "structure": st.name, "type": "COUNT"},
                f"{st.name}: the target has as many rows as all its row sets together",
                "The simplest proof that nothing was lost or added on the way.", "COUNT")  # fmt: skip
            continue
        add(st, {"name": f"{name}_row_count", "structure": st.name, "type": "COUNT"},
            f"{st.name}: the target has as many rows as the source",
            "The simplest proof that nothing was lost or added on the way.", "COUNT")  # fmt: skip
        keys = [f for f in st.fields if f.key]

        # reconciliation reads the structure's own source table, never a joined one
        def own(f: FieldFacts, st: StructureFacts = st) -> str | None:
            ref = _rule_source(f)
            return ref if ref and st.locate(ref)[0] == st.source else None

        key_sources = [own(f) for f in keys]
        if keys and all(key_sources):
            source_key = [s for s in key_sources if s]
            target_key = [f.name for f in keys]
            add(st, {"name": f"{name}_keys", "structure": st.name, "type": "KEYS",
                     "source_key": source_key, "target_key": target_key},
                f"{st.name}: every source key arrives, and nothing extra",
                f"Compares the key ({', '.join(target_key)}) of every source row with the target.",
                f"KEYS {', '.join(source_key)} -> {', '.join(target_key)}")  # fmt: skip
            if any(own(f) for f in st.fields if not f.key):
                add(st, {"name": f"{name}_values", "structure": st.name, "type": "FIELDS",
                         "source_key": source_key, "target_key": target_key, "fields": "auto"},
                    f"{st.name}: copied fields have the same values in the target",
                    "Compares every field that is moved unchanged, row by row, using the key.",
                    "FIELDS auto")  # fmt: skip
        sums = [
            f for f in _active(st)
            if f.data_type in ("integer", "decimal") and own(f)
        ][:MAX_TOTALS_PER_STRUCTURE]  # fmt: skip
        for f in sums:
            source = own(f) or ""
            add(st, {"name": f"{name}_total_{identifier(f.name)}", "structure": st.name,
                     "type": "TOTALS", "source_field": source, "target_field": f.name, "agg": "sum"},
                f"{st.name}: the total of {f.name} is unchanged",
                "A control total: the sum in the source must equal the sum in the target.",
                f"TOTALS sum {source} -> {f.name}")  # fmt: skip
    return out


# --- loads ------------------------------------------------------------------------------


def load_proposals(facts: ObjectFacts) -> list[Proposal]:
    done = {ld.get("structure") for ld in facts.loads}
    out = []
    for st in facts.structures:
        if st.name in done:
            continue
        path = f"output/{identifier(st.name)}.csv"
        out.append(
            Proposal(
                proposal_id("load", st.name, "csv"),
                "load",
                st.name,
                f"Write {st.name} to {path}",
                "Nothing is loaded until a destination exists. A CSV file in the project's "
                "output folder is the simplest start.",
                "high",
                "library",
                f"file {path} (csv)",
                {"kind": "file", "structure": st.name, "path": path, "format": "csv"},
            )  # fmt: skip
        )
    return out


# --- the target structure's own rules ---------------------------------------------------

RULE_KINDS = {
    "mandatory": (
        "Mandatory fields must be filled",
        "The target structure marks these fields as mandatory, so an empty value is an error in the target.",
    ),
    "length": (
        "Values must fit the field length",
        "The target structure gives these fields a maximum length; a longer value would be cut off or refused.",
    ),
    "data_type": (
        "Values must match the field's data type",
        "The target structure gives these fields a number, decimal, date or boolean type; text that does not fit is an error.",
    ),
    "key": (
        "Key fields must be unique",
        "The target structure marks key fields; two rows with the same key would collide in the target.",
    ),
}  # fmt: skip


def _applies(kind: str, f: FieldFacts) -> bool:
    return {
        "mandatory": f.mandatory,
        "length": f.length is not None,
        "data_type": f.data_type != "string",
        "key": f.key,
    }[kind]


def _rule_impact(facts: ObjectFacts, st: StructureFacts, f: FieldFacts, kind: str) -> Impact | None:
    """How many staged rows a structure rule would flag today (plain MOVE fields only)."""
    column = _rule_source(f)
    profile = _profile(facts, st, column)
    if profile is None or column is None:
        return None
    total = profile.row_count
    if kind == "mandatory":
        return Impact(profile.empty_count, total, [])
    counts = facts.value_counts(*st.locate(column))
    if kind == "key":
        present = total - profile.empty_count
        return Impact(max(0, present - profile.distinct_count), total, [])
    if counts is None:
        return None
    if kind == "length":
        bad = [(v, n) for v, n in counts if f.length is not None and len(v) > f.length]
    else:
        valid = TYPE_VALIDATORS[f.data_type][0]
        bad = [(v, n) for v, n in counts if v.strip() and not valid(v)]
    return Impact(sum(n for _, n in bad), total, [v for v, _ in bad[:3]])


def target_rule_proposals(facts: ObjectFacts) -> list[Proposal]:
    """Offer to confirm the mandatory / length / data-type / key rules of the target structure.
    Until confirmed, none of them is checked."""
    out: list[Proposal] = []
    for kind, (label, why) in RULE_KINDS.items():
        if kind in facts.from_mapping:
            continue
        names: list[str] = []
        failing: list[str] = []
        unmapped: list[str] = []
        for st in facts.structures:
            if st.custom_sql:
                continue
            fields = [f for f in _active(st) if _applies(kind, f)]
            if kind == "key" and fields:
                names.append(f"{st.name} ({', '.join(f.name for f in fields)})")
                fields = fields[:1] if len(fields) == 1 else []  # a combined key is not test-run
            else:
                names.extend(f"{st.name}.{f.name}" for f in fields)
            for f in fields:
                if kind == "mandatory" and f.rule.get("type") == "UNMAPPED":
                    unmapped.append(f"{st.name}.{f.name}")
                    continue
                impact = _rule_impact(facts, st, f, kind)
                if impact is not None and impact.failing:
                    e_g = f" (e.g. {', '.join(impact.samples)})" if impact.samples else ""
                    failing.append(
                        f"{st.name}.{f.name}: {impact.failing} of {impact.total} rows{e_g}"
                    )
        if not names:
            continue
        notes: list[str] = []
        if failing:
            shown = "; ".join(failing[:5]) + (
                f"; and {len(failing) - 5} more" if len(failing) > 5 else ""
            )
            notes.append(f"Heads-up: with your current data this would flag {shown}.")
        if unmapped:
            more = " ..." if len(unmapped) > 6 else ""
            notes.append(
                f"Heads-up: {len(unmapped)} mandatory field(s) are not mapped yet "
                f"({', '.join(unmapped[:6])}{more}) and would be flagged until you map them."
            )
        severity = "warn" if notes else "error"
        if notes:
            notes.append(
                "It is proposed as a warning so it does not block your load; make it an error "
                "on the Checks tab once the data is clean."
            )
        shown_names = ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
        out.append(
            Proposal(
                proposal_id("check", "", f"from_mapping_{kind}"),
                "check",
                "",
                f"{label}: {len(names)} field(s) ({shown_names})"
                if kind != "key"
                else f"{label}: {shown_names}",
                why,
                "medium" if notes else "high",
                "target structure",
                f"confirm the '{kind}' rules of the target structure as {severity}",
                {"from_mapping": {kind: severity}},
                notes,
            )  # fmt: skip
        )
    return out


def propose(facts: ObjectFacts, dismissed: set[str] | None = None) -> list[Proposal]:
    """All proposals for an object, grouped by kind, best first. Dismissed ids are left out."""
    found = (
        mapping_proposals(facts) + xref_proposals(facts) + target_rule_proposals(facts)
        + check_proposals(facts) + recon_proposals(facts) + load_proposals(facts)
    )  # fmt: skip
    skip = dismissed or set()
    keep = [p for p in found if p.id not in skip]
    keep.sort(
        key=lambda p: (KINDS.index(p.kind), CONFIDENCE_ORDER[p.confidence], p.structure, p.title)
    )
    return keep
