"""Data classes for the proposal engine. The engine reads a snapshot of the object (the
facts) and returns proposals; it never touches the project."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from data_migration_concept.profile.profile import ColumnProfile

KINDS = ["mapping", "xref", "check", "recon", "load"]
KIND_LABELS = {
    "mapping": "Field mappings",
    "xref": "Value lists (XREF)",
    "check": "Checks",
    "recon": "Reconciliations",
    "load": "Load destinations",
}


def identifier(text: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]+", "_", text)).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"{fallback}_{cleaned}".strip("_")
    return cleaned


def proposal_id(kind: str, structure: str, key: str) -> str:
    return hashlib.sha1(f"{kind}|{structure}|{key}".encode()).hexdigest()[:12]


@dataclass
class Proposal:
    """One thing the user may accept. `payload` is exactly what would be written."""

    id: str
    kind: str  # one of KINDS
    structure: str
    title: str
    why: str
    confidence: str  # "high" | "medium"
    origin: str  # "target structure" | "library" | "your data"
    summary: str  # a short description of what would be written
    payload: dict[str, Any]
    notes: list[str] = field(default_factory=list)


@dataclass
class FieldFacts:
    name: str
    rule: dict[str, Any]
    data_type: str = "string"
    length: int | None = None
    mandatory: bool = False
    key: bool = False
    description: str = ""


@dataclass
class StructureFacts:
    name: str
    source: str
    fields: list[FieldFacts]
    custom_sql: bool = False


def _no_values(source: str, column: str) -> list[str] | None:
    return None


def _no_counts(source: str, column: str) -> list[tuple[str, int]] | None:
    return None


@dataclass
class ObjectFacts:
    """What the engine knows about an object. Everything is optional except the structures:
    without staged data, only proposals that need no data are made."""

    structures: list[StructureFacts]
    source_columns: dict[str, list[str]] = field(default_factory=dict)
    profiles: dict[str, dict[str, ColumnProfile]] = field(default_factory=dict)
    distinct: Callable[[str, str], list[str] | None] = _no_values
    value_counts: Callable[[str, str], list[tuple[str, int]] | None] = _no_counts
    checks: list[dict[str, Any]] = field(default_factory=list)
    from_mapping: dict[str, str] = field(default_factory=dict)  # confirmed structure-based rules
    recons: list[dict[str, Any]] = field(default_factory=list)
    xrefs: set[str] = field(default_factory=set)
    loads: list[dict[str, Any]] = field(default_factory=list)
