"""Proposals: pre-written suggestions (mappings, value lists, checks, reconciliations, loads)
that a user may accept. Generating proposals never changes a project."""

from data_migration_concept.proposals.engine import propose
from data_migration_concept.proposals.model import (
    KIND_LABELS,
    KINDS,
    FieldFacts,
    ObjectFacts,
    Proposal,
    StructureFacts,
)

__all__ = [
    "KINDS",
    "KIND_LABELS",
    "FieldFacts",
    "ObjectFacts",
    "Proposal",
    "StructureFacts",
    "propose",
]
