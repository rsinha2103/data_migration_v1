# ADR 0009: Proposals instead of defaults

Status: accepted

## Decision
- Checks, value lists, reconciliations, load destinations and mappings are **proposed, never applied automatically**. A new project or object contains none of them; a **Proposals** tab shows ready-made suggestions the user can accept one by one. Computing or viewing proposals has no side effects (tested by comparing the project files before and after).
- The engine (`data_migration_concept/proposals`) is pure: it reads a snapshot of the object (`ObjectFacts`: structures, source columns, column profiles, distinct values, existing checks/recons/lists/loads) and returns `Proposal` objects with a stable id, a reason, a confidence (high/medium), an origin (target structure, library, your data) and the exact payload that would be written.
- Three sources: the target structure (names, flags, types, keys), a built-in library (ISO country and currency lists, unit and language starting points, field-name format checks, legacy alternative names), and the user's staged data (profiles and distinct values). Data-based checks default to `warn`; library format checks that are certain (country, currency) default to `error`.
- Proposals that duplicate something already in the project (same structure, field and check type; an existing list or reconciliation; a mapped field) are not made. Similar proposals are grouped (one phone-format proposal for many fields). Numbered copies such as `SMTP_ADDR_2` are never mapped by similar spelling.
- Every proposed library check is **dry-run against the staged values** of the source column the field is copied from (`value_counts`). A check the data already breaks is proposed as a `warn` with a visible heads-up (rows flagged, example values), so accepting a proposal cannot unexpectedly block a load. Mandatory fields that are still unmapped are listed at the top of the tab, since the automatic mandatory check will block on them. Check results explain themselves: failing values are shown (`e.g. Germany`) and a mandatory unmapped field says "mandatory but not mapped yet".
- Value lists leave out values they cannot recognise, so the transform still stops loudly on them; nothing is mapped silently.
- Applying: the UI sends only proposal ids. The server re-creates the proposals and applies the ones whose ids match, so a form cannot inject its own payload. Each proposal is validated against the whole project (ADR 0007); one that would break it is refused and reported, others still apply. A value list and the MOVE-to-XREF switches of its fields are written in one step.
- Dismissed ids are stored in `objects/<object>/proposals_dismissed.json` (not part of the validated configuration) and can be restored.

## Consequences
Mandatory, length, type and key checks remain automatic because they come straight from the target structure the user defined; everything else is opt-in. The library is a starting point, not target-system configuration: its unit-of-measure and language values must be confirmed. Proposals are computed on each visit from up to 100,000 staged rows per source.
