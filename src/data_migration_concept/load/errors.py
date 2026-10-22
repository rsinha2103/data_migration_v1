"""Load errors."""

from __future__ import annotations

from data_migration_concept.checks import CheckResult


class LoadError(Exception):
    """A structure could not be loaded."""


class LoadBlockedError(LoadError):
    """Load refused because error-severity checks failed. Nothing was written."""

    def __init__(self, blocking: list[CheckResult]) -> None:
        self.blocking = blocking
        names = ", ".join(r.name for r in blocking[:10])
        more = f" and {len(blocking) - 10} more" if len(blocking) > 10 else ""
        super().__init__(f"{len(blocking)} blocking check(s) failed: {names}{more}")
